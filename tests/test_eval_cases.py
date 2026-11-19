"""Eval case format: loading, validation and `case_hash` (`evals/cases/*.json`)."""
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from houselib.evals import cases
from houselib.evals.cases import Call, Case, case_hash, load, load_all

AGENT_CASE = {
    "agent": "coder",
    "fixture": {"name": "one-task", "tasks": 1},
    "prompt": "Do T001 and finish.",
    "expect": [
        {"label": "check", "tool": "Bash", "input_match": "house check", "min": 1},
        {"tool": "Bash", "input_match": "house merge", "min": 1, "max": 1},
    ],
    "forbid": [{"label": "raw-merge", "tool": "Bash", "input_match": "git merge"}],
    "order": [{"before": "check", "after": "Bash#1"}],
    "outcome": [
        {"type": "git_branch", "name": "main", "exists": True},
        {"type": "git_merge_commit_on", "branch": "main", "message_match": "^Merge"},
        {"type": "git_clean"},
        {"type": "file_exists", "path": "src/x.py", "exists": True},
        {"type": "file_unchanged", "path": ".house/checks/T001.json"},
        {"type": "task_status", "task": "T001", "status": "review"},
        {"type": "open_questions", "count": 0},
    ],
    "kind": "regression",
    "max_turns": 25,
    "timeout_seconds": 600,
    "tags": ["smoke"],
}

SKILL_CASE = {
    "skill": "house-plan",
    "model": "opus",
    "fixture": {"name": "planning"},
    "prompt": "/house-plan",
    "expect": [{"tool": "Skill", "input_match": "house-plan"}],
}


class CaseTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def write(self, name: str, data, raw: str | None = None) -> Path:
        path = self.root / f"{name}.json"
        path.write_text(raw if raw is not None else json.dumps(data))
        return path

    def assertInvalid(self, data, *needles: str, name: str = "bad"):
        path = self.write(name, data)
        with self.assertRaises(ValueError) as ctx:
            load(path)
        message = str(ctx.exception)
        self.assertIn(path.name, message)
        for needle in needles:
            self.assertIn(needle, message)

    def variant(self, base: dict, **changes) -> dict:
        data = json.loads(json.dumps(base))
        for key, value in changes.items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        return data


class LoadValidTest(CaseTestBase):
    def test_agent_case_loads_with_all_fields(self):
        path = self.write("merge-via-house", AGENT_CASE)
        case = load(path)
        self.assertIsInstance(case, Case)
        self.assertEqual(case.name, "merge-via-house")
        self.assertEqual(case.path, path)
        self.assertEqual((case.agent, case.skill, case.model), ("coder", None, None))
        self.assertEqual(case.fixture, {"name": "one-task", "tasks": 1})
        self.assertEqual(case.prompt, "Do T001 and finish.")
        self.assertEqual([c.label for c in case.expect], ["check", "Bash#1"])
        self.assertEqual([c.label for c in case.forbid], ["raw-merge"])
        self.assertEqual(case.order, [("check", "Bash#1")])
        self.assertEqual(len(case.outcome), 7)
        self.assertEqual(case.kind, "regression")
        self.assertEqual((case.max_turns, case.timeout_seconds), (25, 600))
        self.assertEqual(case.tags, ["smoke"])
        self.assertEqual(case.tool, "claude")

    def test_skill_case_loads_with_defaults(self):
        case = load(self.write("plan-freezes", SKILL_CASE))
        self.assertEqual((case.agent, case.skill, case.model), (None, "house-plan", "opus"))
        self.assertEqual(case.kind, "capability")
        self.assertEqual((case.max_turns, case.timeout_seconds), (40, 900))
        self.assertEqual((case.forbid, case.order, case.outcome, case.tags), ([], [], [], []))
        self.assertEqual(case.tool, "claude")

    def test_call_defaults(self):
        case = load(self.write("c", AGENT_CASE))
        first, second = case.expect
        self.assertEqual((first.tool, first.input_match, first.min, first.max), ("Bash", "house check", 1, None))
        self.assertEqual((second.min, second.max), (1, 1))
        self.assertEqual(case.forbid[0].min, 1)
        self.assertIsNone(case.forbid[0].max)

    def test_default_labels_are_unique_across_expect_and_forbid(self):
        data = self.variant(SKILL_CASE, expect=[{"tool": "Bash"}, {"tool": "Bash"}], forbid=[{"tool": "Bash"}])
        case = load(self.write("c", data))
        labels = [c.label for c in case.expect + case.forbid]
        self.assertEqual(labels, ["Bash#0", "Bash#1", "Bash#2"])

    def test_input_match_defaults_to_match_everything(self):
        case = load(self.write("c", self.variant(SKILL_CASE, expect=[{"tool": "Bash"}])))
        self.assertTrue(case.expect[0].matches({"command": "anything"}))

    def test_codex_tool_for_coder_codex(self):
        data = self.variant(AGENT_CASE, agent="coder-codex", tool="codex")
        self.assertEqual(load(self.write("c", data)).tool, "codex")

    def test_agent_case_may_override_model(self):
        data = self.variant(AGENT_CASE, model="haiku")
        self.assertEqual(load(self.write("c", data)).model, "haiku")


class CallMatchingTest(CaseTestBase):
    def test_matches_json_of_viewed_input_with_sorted_keys(self):
        data = self.variant(SKILL_CASE, expect=[
            {"tool": "Bash", "input_match": r'"command": "house merge T001"'}])
        call = load(self.write("c", data)).expect[0]
        self.assertTrue(call.matches({"command": "house merge T001"}))
        self.assertFalse(call.matches({"command": "git merge T001"}))

    def test_regex_compiled_once_at_load(self):
        data = self.variant(SKILL_CASE, expect=[{"tool": "Bash", "input_match": "house merge"}])
        path = self.write("c", data)
        with mock.patch.object(cases.re, "compile", wraps=re.compile) as compile_spy:
            call = load(path).expect[0]
            self.assertEqual(compile_spy.call_count, 1)
            for _ in range(5):
                call.matches({"command": "house merge T001"})
            self.assertEqual(compile_spy.call_count, 1)
        self.assertIsInstance(call, Call)


class LoadInvalidTest(CaseTestBase):
    def test_invalid_json(self):
        path = self.write("bad", None, raw="{not json")
        with self.assertRaises(ValueError) as ctx:
            load(path)
        self.assertIn("bad.json", str(ctx.exception))
        self.assertIn("JSON", str(ctx.exception))

    def test_top_level_not_object(self):
        self.assertInvalid([1, 2], "object")

    def test_missing_prompt(self):
        self.assertInvalid(self.variant(AGENT_CASE, prompt=None), "prompt")

    def test_empty_prompt(self):
        self.assertInvalid(self.variant(AGENT_CASE, prompt="  "), "prompt")

    def test_missing_fixture(self):
        self.assertInvalid(self.variant(AGENT_CASE, fixture=None), "fixture")

    def test_fixture_without_name(self):
        self.assertInvalid(self.variant(AGENT_CASE, fixture={"tasks": 1}), "fixture", "name")

    def test_both_agent_and_skill(self):
        self.assertInvalid(self.variant(AGENT_CASE, skill="house-plan"), "agent", "skill")

    def test_neither_agent_nor_skill(self):
        self.assertInvalid(self.variant(AGENT_CASE, agent=None), "agent", "skill")

    def test_skill_without_model(self):
        self.assertInvalid(self.variant(SKILL_CASE, model=None), "model", "skill")

    def test_unknown_top_level_key(self):
        self.assertInvalid(self.variant(AGENT_CASE, colour="red"), "colour")

    def test_unknown_predicate(self):
        data = self.variant(AGENT_CASE, outcome=[{"type": "git_exploded"}])
        self.assertInvalid(data, "outcome[0]", "git_exploded")

    def test_predicate_missing_param(self):
        data = self.variant(AGENT_CASE, outcome=[{"type": "git_branch", "name": "main"}])
        self.assertInvalid(data, "outcome[0]", "exists")

    def test_predicate_unknown_param(self):
        data = self.variant(AGENT_CASE, outcome=[{"type": "git_clean", "extra": 1}])
        self.assertInvalid(data, "outcome[0]", "extra")

    def test_predicate_wrong_param_type(self):
        data = self.variant(AGENT_CASE, outcome=[{"type": "file_exists", "path": "a", "exists": "yes"}])
        self.assertInvalid(data, "outcome[0]", "exists")

    def test_predicate_bad_message_match_regex(self):
        data = self.variant(AGENT_CASE, outcome=[
            {"type": "git_merge_commit_on", "branch": "main", "message_match": "("}])
        self.assertInvalid(data, "outcome[0]", "message_match")

    def test_task_status_unknown_status(self):
        data = self.variant(AGENT_CASE, outcome=[{"type": "task_status", "task": "T001", "status": "bogus"}])
        self.assertInvalid(data, "outcome[0]", "status")

    def test_open_questions_needs_a_bound(self):
        data = self.variant(AGENT_CASE, outcome=[{"type": "open_questions"}])
        self.assertInvalid(data, "outcome[0]", "count")

    def test_open_questions_count_excludes_min_max(self):
        data = self.variant(AGENT_CASE, outcome=[{"type": "open_questions", "count": 1, "min": 0}])
        self.assertInvalid(data, "outcome[0]", "count")

    def test_open_questions_min_greater_than_max(self):
        data = self.variant(AGENT_CASE, outcome=[{"type": "open_questions", "min": 3, "max": 1}])
        self.assertInvalid(data, "outcome[0]", "min")

    def test_open_questions_valid_forms(self):
        for params in ({"count": 0}, {"min": 1}, {"max": 2}, {"min": 1, "max": 2}):
            data = self.variant(AGENT_CASE, outcome=[{"type": "open_questions", **params}], order=[])
            self.assertEqual(len(load(self.write("c", data)).outcome), 1)

    def test_final_answer_needs_a_valid_regex(self):
        for bad, needles in (({"type": "final_answer"}, ("outcome[0]", "match")),
                             ({"type": "final_answer", "match": "("}, ("outcome[0].match", "regex")),
                             ({"type": "final_answer", "match": 6}, ("outcome[0].match",)),
                             ({"type": "final_answer", "match": "a", "extra": 1}, ("outcome[0]", "extra"))):
            self.assertInvalid(self.variant(AGENT_CASE, outcome=[bad]), *needles)
        data = self.variant(AGENT_CASE, outcome=[{"type": "final_answer", "match": r"\b6\b"}], order=[])
        self.assertEqual(load(self.write("c", data)).outcome, [{"type": "final_answer", "match": r"\b6\b"}])

    def test_final_answer_key(self):
        for bad in ("", "Q1:", "Q\n1", 7, "x" * 33):
            data = self.variant(AGENT_CASE, outcome=[{"type": "final_answer", "match": "a", "key": bad}])
            self.assertInvalid(data, "outcome[0].key")
        pred = {"type": "final_answer", "match": r"\b6\b", "key": "Q1"}
        data = self.variant(AGENT_CASE, outcome=[pred], order=[])
        self.assertEqual(load(self.write("c", data)).outcome, [pred])

    def test_file_unchanged_ref_and_worktree_forms(self):
        for params in ({"ref": "task/T001-calc"}, {"ref": "task/T001-calc", "ref_optional": True},
                       {"worktree": ".worktrees/T001-calc"}):
            data = self.variant(AGENT_CASE, outcome=[{"type": "file_unchanged", "path": "a.py", **params}])
            self.assertEqual(load(self.write("c", data)).outcome[0], {"type": "file_unchanged", "path": "a.py",
                                                                      **params})

    def test_file_unchanged_bad_ref_and_worktree(self):
        bad = [({"ref": "b", "worktree": "w"}, "worktree"), ({"ref_optional": True}, "ref_optional"),
               ({"ref": "b", "ref_optional": "yes"}, "ref_optional"), ({"ref": ""}, "ref"),
               ({"worktree": 3}, "worktree")]
        for params, needle in bad:
            with self.subTest(params=params):
                data = self.variant(AGENT_CASE, outcome=[{"type": "file_unchanged", "path": "a.py", **params}])
                self.assertInvalid(data, "outcome[0]", needle)

    def test_git_branch_ahead(self):
        data = self.variant(AGENT_CASE, outcome=[{"type": "git_branch_ahead", "branch": "task/T001", "min": 1}])
        self.assertEqual(load(self.write("c", data)).outcome[0]["min"], 1)
        for pred, needle in (({"branch": "b"}, "min"), ({"min": 1}, "branch"), ({"branch": "b", "min": 0}, "min"),
                             ({"branch": "b", "min": "1"}, "min")):
            with self.subTest(pred=pred):
                data = self.variant(AGENT_CASE, outcome=[{"type": "git_branch_ahead", **pred}])
                self.assertInvalid(data, "outcome[0]", needle)

    def test_check_state(self):
        data = self.variant(AGENT_CASE, outcome=[{"type": "check_state", "task": "T001", "state": "GREEN"}])
        self.assertEqual(load(self.write("c", data)).outcome[0]["state"], "GREEN")
        for pred, needle in (({"task": "T001", "state": "green"}, "state"), ({"state": "GREEN"}, "task"),
                             ({"task": "T001"}, "state")):
            with self.subTest(pred=pred):
                self.assertInvalid(self.variant(AGENT_CASE, outcome=[{"type": "check_state", **pred}]), needle)

    def test_call_ok(self):
        data = self.variant(AGENT_CASE, expect=[{"tool": "Bash", "input_match": "house merge", "ok": True}],
                            order=[])
        self.assertTrue(load(self.write("c", data)).expect[0].ok)
        self.assertFalse(load(self.write("d", AGENT_CASE)).expect[0].ok)
        for value in (False, "yes", 1):
            with self.subTest(value=value):
                data = self.variant(AGENT_CASE, expect=[{"tool": "Bash", "ok": value}], order=[])
                self.assertInvalid(data, "expect[0].ok")

    def test_bad_regex_in_expect(self):
        data = self.variant(AGENT_CASE, expect=[{"tool": "Bash", "input_match": "(unclosed"}], order=[])
        self.assertInvalid(data, "expect[0]", "input_match")

    def test_bad_regex_in_forbid(self):
        data = self.variant(AGENT_CASE, forbid=[{"tool": "Bash", "input_match": "["}], order=[])
        self.assertInvalid(data, "forbid[0]", "input_match")

    def test_call_missing_tool(self):
        data = self.variant(AGENT_CASE, expect=[{"input_match": "x"}], order=[])
        self.assertInvalid(data, "expect[0]", "tool")

    def test_call_unknown_key(self):
        data = self.variant(AGENT_CASE, expect=[{"tool": "Bash", "mx": 1}], order=[])
        self.assertInvalid(data, "expect[0]", "mx")

    def test_non_positive_limits(self):
        for field in ("max_turns", "timeout_seconds"):
            for value in (0, -3):
                with self.subTest(field=field, value=value):
                    self.assertInvalid(self.variant(AGENT_CASE, **{field: value}), field)

    def test_limits_must_be_integers(self):
        self.assertInvalid(self.variant(AGENT_CASE, max_turns="40"), "max_turns")
        self.assertInvalid(self.variant(AGENT_CASE, max_turns=True), "max_turns")

    def test_call_counts(self):
        bad_calls = [{"tool": "Bash", "max": 0}, {"tool": "Bash", "min": -1},
                     {"tool": "Bash", "min": 3, "max": 2}, {"tool": "Bash", "min": 1.5}]
        for call in bad_calls:
            with self.subTest(call=call):
                data = self.variant(AGENT_CASE, expect=[call], order=[])
                self.assertInvalid(data, "expect[0]")

    def test_forbid_min_must_be_positive_and_max_rejected(self):
        self.assertInvalid(self.variant(AGENT_CASE, forbid=[{"tool": "Bash", "min": 0}], order=[]), "forbid[0]", "min")
        self.assertInvalid(self.variant(AGENT_CASE, forbid=[{"tool": "Bash", "max": 2}], order=[]), "forbid[0]", "max")

    def test_duplicate_labels(self):
        data = self.variant(AGENT_CASE, expect=[{"label": "a", "tool": "Bash"}],
                            forbid=[{"label": "a", "tool": "Edit"}], order=[])
        self.assertInvalid(data, "label", "a")

    def test_order_with_undefined_label(self):
        data = self.variant(AGENT_CASE, order=[{"before": "check", "after": "ghost"}])
        self.assertInvalid(data, "order[0]", "ghost")

    def test_order_bad_shape(self):
        self.assertInvalid(self.variant(AGENT_CASE, order=[{"before": "check"}]), "order[0]", "after")

    def test_bad_kind(self):
        self.assertInvalid(self.variant(AGENT_CASE, kind="smoke"), "kind")

    def test_bad_tool(self):
        self.assertInvalid(self.variant(AGENT_CASE, tool="gemini"), "tool")

    def test_codex_only_for_coder_codex(self):
        self.assertInvalid(self.variant(AGENT_CASE, tool="codex"), "tool", "coder-codex")
        self.assertInvalid(self.variant(SKILL_CASE, tool="codex"), "tool", "coder-codex")

    def test_tags_must_be_list_of_strings(self):
        self.assertInvalid(self.variant(AGENT_CASE, tags="smoke"), "tags")
        self.assertInvalid(self.variant(AGENT_CASE, tags=[1]), "tags")

    def test_missing_file(self):
        with self.assertRaises(ValueError) as ctx:
            load(self.root / "nope.json")
        self.assertIn("nope.json", str(ctx.exception))


class LoadAllTest(CaseTestBase):
    def setUp(self):
        super().setUp()
        self.write("b-case", self.variant(AGENT_CASE, tags=["smoke", "merge"]))
        self.write("a-case", self.variant(SKILL_CASE, tags=["smoke"]))
        self.write("c-case", self.variant(SKILL_CASE, tags=["slow"]))
        (self.root / "README.md").write_text("not a case")

    def test_sorted_by_name_and_ignores_non_json(self):
        self.assertEqual([c.name for c in load_all(self.root)], ["a-case", "b-case", "c-case"])

    def test_filter_by_tag(self):
        self.assertEqual([c.name for c in load_all(self.root, tag="smoke")], ["a-case", "b-case"])
        self.assertEqual([c.name for c in load_all(self.root, tag="merge")], ["b-case"])
        self.assertEqual(load_all(self.root, tag="none"), [])

    def test_filter_by_name(self):
        self.assertEqual([c.name for c in load_all(self.root, name="c-case")], ["c-case"])
        self.assertEqual(load_all(self.root, name="missing"), [])

    def test_filter_by_tag_and_name(self):
        self.assertEqual(load_all(self.root, tag="slow", name="a-case"), [])

    def test_name_filter_skips_other_invalid_files(self):
        self.write("zz-broken", None, raw="{")
        self.assertEqual([c.name for c in load_all(self.root, name="a-case")], ["a-case"])

    def test_invalid_file_raises(self):
        self.write("zz-broken", None, raw="{")
        with self.assertRaises(ValueError) as ctx:
            load_all(self.root)
        self.assertIn("zz-broken.json", str(ctx.exception))

    def test_missing_root(self):
        with self.assertRaises(ValueError):
            load_all(self.root / "nowhere")


class CaseHashTest(CaseTestBase):
    def test_hash_is_sha256_of_file_bytes(self):
        import hashlib
        path = self.write("c", SKILL_CASE)
        self.assertEqual(case_hash(path), hashlib.sha256(path.read_bytes()).hexdigest())

    def test_stable_when_unchanged(self):
        path = self.write("c", SKILL_CASE)
        self.assertEqual(case_hash(path), case_hash(path))

    def test_changes_when_file_changes(self):
        path = self.write("c", SKILL_CASE)
        before = case_hash(path)
        path.write_text(json.dumps(self.variant(SKILL_CASE, prompt="different")))
        self.assertNotEqual(before, case_hash(path))

    def test_whitespace_only_change_changes_hash(self):
        path = self.write("c", SKILL_CASE)
        before = case_hash(path)
        path.write_text(path.read_text() + "\n")
        self.assertNotEqual(before, case_hash(path))


if __name__ == "__main__":
    unittest.main()
