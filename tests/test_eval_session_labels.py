"""K5: `"session": "main"` cases (the coordinator's main session, no `--agent`) and labels of outcome predicates."""
import json
import tempfile
import unittest
from pathlib import Path

from houselib.evals import cases, runner
from tests import test_eval_grade as tgrade
from tests import test_eval_runner as trun

MAIN = {"session": "main", "model": "sonnet", "fixture": {"name": "small_build"}, "prompt": "Look at the log."}


class LoadTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def load(self, data: dict) -> cases.Case:
        path = Path(self._tmp.name) / "c.json"
        path.write_text(json.dumps(data))
        return cases.load(path)

    def invalid(self, data: dict, *needles: str):
        with self.assertRaises(ValueError) as ctx:
            self.load(data)
        for needle in needles:
            self.assertIn(needle, str(ctx.exception))


class SessionLoadTest(LoadTestBase):
    def test_main_session_case_loads(self):
        case = self.load(MAIN)
        self.assertEqual((case.session, case.agent, case.skill, case.model), ("main", None, None, "sonnet"))

    def test_agent_and_skill_cases_have_no_session(self):
        case = self.load({"agent": "coder", "fixture": {"name": "x"}, "prompt": "p"})
        self.assertIsNone(case.session)

    def test_session_excludes_agent_and_skill(self):
        self.invalid({**MAIN, "agent": "coder"}, "agent", "skill", "session")
        self.invalid({**MAIN, "skill": "house-plan"}, "agent", "skill", "session")

    def test_one_of_agent_skill_session_is_required(self):
        self.invalid({"model": "sonnet", "fixture": {"name": "x"}, "prompt": "p"}, "session")

    def test_session_needs_a_model(self):
        self.invalid({k: v for k, v in MAIN.items() if k != "model"}, "model", "session")

    def test_only_main_is_a_session(self):
        self.invalid({**MAIN, "session": "coordinator"}, "session", "main")
        self.invalid({**MAIN, "session": 1}, "session", "main")

    def test_codex_cannot_run_a_main_session(self):
        self.invalid({**MAIN, "tool": "codex"}, "tool")


class SessionRunTest(trun.RunnerCase):
    def install(self, case):
        if case.session:
            (self.root.contest / "CLAUDE.md").write_text("@AGENTS.md\n")
        else:
            super().install(case)

    def main_case(self, **kw):
        return trun.make_case(agent=None, session="main", model="sonnet", **kw)

    def test_argv_has_no_agent_and_the_prompt_as_is(self):
        result, popens, _, _ = self.go(self.main_case())
        self.assertIsNone(result.infra)
        claude = popens.procs[0].argv[popens.procs[0].argv.index(trun.dk.image_name()) + 5:]
        self.assertNotIn("--agent", claude)
        self.assertEqual(trun.option(claude, "--model"), "sonnet")
        self.assertEqual(popens.procs[0].stdin.lines()[0]["message"]["content"], "do the thing")

    def test_followups_are_sent_as_typed(self):
        case = self.main_case(followups=["next one"])
        lines = [json.loads(line)["message"]["content"] for line in runner.user_lines(case)]
        self.assertEqual(lines, ["do the thing", "next one"])
        self.assertNotIn("--agent", runner.claude_argv(case, self.root, "sonnet", "sid"))

    def test_missing_claude_md_is_infra(self):
        result, popens, _, _ = self.go(self.main_case(), installed=False)
        self.assertEqual(popens.procs, [])
        self.assertIn("CLAUDE.md", result.infra)

    def test_model_is_the_cases(self):
        self.assertEqual(runner.resolve_model(self.main_case(), self.root), "sonnet")


class LabelLoadTest(LoadTestBase):
    def case(self, outcome, **extra):
        return {**MAIN, "outcome": outcome, **extra}

    def test_label_loads_and_names_the_predicate(self):
        case = self.load(self.case([{"type": "git_clean", "label": "clean"}, {"type": "git_clean"}]))
        self.assertEqual([cases.outcome_name(i, p) for i, p in enumerate(case.outcome)],
                         ["clean", "outcome[1] git_clean"])

    def test_label_must_be_a_non_empty_string(self):
        self.invalid(self.case([{"type": "git_clean", "label": ""}]), "outcome[0].label")
        self.invalid(self.case([{"type": "git_clean", "label": 3}]), "outcome[0].label")

    def test_labels_are_unique_across_calls_and_outcome(self):
        self.invalid(self.case([{"type": "git_clean", "label": "x"}, {"type": "git_clean", "label": "x"}]),
                     "duplicate label 'x'")
        self.invalid(self.case([{"type": "git_clean", "label": "push"}],
                               forbid=[{"label": "push", "tool": "Bash", "input_match": "git push"}]),
                     "duplicate label 'push'")
        self.invalid(self.case([{"type": "git_clean", "label": "Bash#0"}],
                               expect=[{"tool": "Bash", "input_match": "ls"}]), "duplicate label 'Bash#0'")
        # a label shaped like a generated name is rejected before any collision can happen (C9 of K5)
        self.invalid(self.case([{"type": "git_clean", "label": "outcome[1] git_clean"}, {"type": "git_clean"}]),
                     "starts like a generated outcome name")

    def test_label_works_with_every_predicate_type(self):
        case = self.load(self.case([
            {"type": "final_answer", "match": "x", "label": "fact"},
            {"type": "hidden_tests", "dir": "chain", "command": ["python3"], "min_passed": 1, "label": "s1"},
            {"type": "file_unchanged", "path": "a.py", "label": "frozen"}]))
        self.assertEqual([p["label"] for p in case.outcome], ["fact", "s1", "frozen"])

    def test_unknown_keys_still_rejected(self):
        self.invalid(self.case([{"type": "git_clean", "lable": "x"}]), "lable")


class LabelGradeTest(tgrade.GradeBase):
    def test_label_is_the_name_in_failed_predicates_and_predicates(self):
        (self.contest / "dirty.txt").write_text("x")
        case = tgrade.make_case(outcome=[{"type": "git_clean", "label": "clean"},
                                         {"type": "file_exists", "path": "keep.txt", "exists": True}])
        g = self.grade(case, tgrade.Log().bash("ls"))
        self.assertEqual(g.failed_predicates, ["clean"])
        self.assertEqual(g.predicates, {"clean": False, "outcome[1] file_exists": True})
        self.assertEqual(g.score, 0.5)

    def test_labelled_question_cap_still_counts_as_a_cap(self):
        (self.contest / ".house/QUESTIONS.md").write_text(
            "# Questions\n\n## Q-001 [open] old\n- **Poziom:** zwykły\n\n## Q-003 [open] new\n- **Poziom:** zwykły\n")
        case = tgrade.make_case(outcome=[{"type": "open_questions", "max": 1, "label": "no-new-questions"}])
        g = self.grade(case, tgrade.Log().bash("ls"))
        self.assertEqual((g.result, g.failed_predicates), ("fail", ["no-new-questions"]))

    def test_labelled_final_answer_note_uses_the_label(self):
        case = tgrade.make_case(outcome=[{"type": "final_answer", "match": "x", "label": "fact-a"}])
        g = self.grade(case, tgrade.Log(terminal=False).bash("ls").result("", False))
        self.assertEqual(g.failed_predicates, ["fact-a"])


if __name__ == "__main__":
    unittest.main()
