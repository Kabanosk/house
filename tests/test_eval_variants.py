"""Eval variants (K5): the case field, `runner.apply_variant`, the CLI flags and the per-run cost metrics.

Synthetic data only; nothing starts a real `claude`, Docker or git export (fakes as in test_eval_runner/cli)."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from houselib.evals import cases, grade, results, runner
from houselib.evals.cases import Edit, Variant
from tests import test_eval_cli as tcli
from tests import test_eval_runner as trun
from tests.helpers import git
from tests.test_cost import OPUS, s_asst, s_result, usage, write_jsonl
from tests.test_eval_sandbox import ScratchBase

BASE = {"agent": "coder", "fixture": {"name": "small_build"}, "prompt": "do it"}


def load_case(**fields):
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "c.json"
        path.write_text(json.dumps({**BASE, **fields}))
        return cases.load(path)


class FakeContest:
    """A contest directory in a temp dir, shaped like what `apply_variant` needs of a `TempRoot`."""

    def __init__(self, test: unittest.TestCase):
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.contest = Path(tmp.name) / "contest"
        (self.contest / ".claude").mkdir(parents=True)
        self.root = SimpleNamespace(contest=self.contest, commit_patch=lambda: None)

    def write(self, rel, text):
        path = self.contest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def settings(self):
        return json.loads((self.contest / ".claude/settings.local.json").read_text())


class LoadTest(unittest.TestCase):
    def test_no_variants_by_default(self):
        self.assertEqual(load_case().variants, {})

    def test_full_variant(self):
        case = load_case(variants={"ctl": {
            "settings": {"a": {"b": 1}}, "env": {"X": "1", "Y": None},
            "edit": [{"path": "f.md", "pattern": "a+", "repl": "b"}], "remove": ["old"], "prompt": "go {file:x.md}"}})
        v = case.variants["ctl"]
        self.assertEqual(v, Variant("ctl", {"a": {"b": 1}}, {"X": "1", "Y": None}, [Edit("f.md", "a+", "b")],
                                    ["old"], "go {file:x.md}"))

    def test_empty_variant_is_a_valid_control(self):
        self.assertEqual(load_case(variants={"control": {}}).variants["control"], Variant("control"))

    def check_invalid(self, expect, **fields):
        with self.assertRaises(ValueError) as cm:
            load_case(**fields)
        self.assertIn(expect, str(cm.exception))

    def test_default_is_reserved(self):
        self.check_invalid("reserved", variants={"default": {}})

    def test_bad_names(self):
        for name in ("Upper", "has_underscore", "x" * 33, ""):
            self.check_invalid("name must match", variants={name: {}})

    def test_unknown_key_is_rejected(self):
        self.check_invalid("unknown key(s) mode", variants={"a": {"mode": 1}})

    def test_env_values_are_strings_or_null(self):
        self.check_invalid("variants.a.env.X", variants={"a": {"env": {"X": 1}}})

    def test_edit_needs_path_pattern_repl_and_a_valid_regex(self):
        self.check_invalid("edit[0].repl: missing", variants={"a": {"edit": [{"path": "f", "pattern": "x"}]}})
        self.check_invalid("invalid regex", variants={"a": {"edit": [{"path": "f", "pattern": "(", "repl": ""}]}})
        self.check_invalid("unknown key(s) count",
                           variants={"a": {"edit": [{"path": "f", "pattern": "x", "repl": "", "count": 1}]}})

    def test_paths_must_stay_inside_the_contest(self):
        for bad in ("/etc/passwd", "../x", "a/../../x", ".", "C:/x"):
            self.check_invalid("inside the contest", variants={"a": {"remove": [bad]}})
            self.check_invalid("inside the contest",
                               variants={"a": {"edit": [{"path": bad, "pattern": "x", "repl": ""}]}})

    def test_prompt_must_be_text(self):
        self.check_invalid("prompt: must be a non-empty string", variants={"a": {"prompt": ""}})


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.fc = FakeContest(self)

    def apply(self, **kw):
        runner.apply_variant(self.fc.root, Variant("v", **kw))

    def test_settings_deep_merge_with_null_delete(self):
        self.fc.write(".claude/settings.local.json", json.dumps(
            {"permissions": {"allow": ["x"], "deny": ["y"]}, "hooks": {"A": 1}, "keep": True}))
        self.apply(settings={"permissions": {"allow": ["z"], "deny": None}, "hooks": None, "new": {"k": 1}})
        self.assertEqual(self.fc.settings(), {"permissions": {"allow": ["z"]}, "keep": True, "new": {"k": 1}})

    def test_settings_file_is_created_when_missing(self):
        self.apply(settings={"a": {"b": 1, "c": 2}})
        self.assertEqual(self.fc.settings(), {"a": {"b": 1, "c": 2}})

    def test_null_delete_of_an_absent_settings_key_is_infra(self):
        self.fc.write(".claude/settings.local.json", json.dumps({"keep": 1}))
        with self.assertRaises(runner._Infra) as cm:
            self.apply(settings={"autoCompactWindow": None})
        self.assertIn("settings: autoCompactWindow not present", str(cm.exception))
        self.assertEqual(self.fc.settings(), {"keep": 1})  # nothing written

    def test_null_delete_without_a_settings_file_is_infra(self):
        with self.assertRaises(runner._Infra):
            self.apply(settings={"a": None})
        self.assertFalse((self.fc.contest / ".claude/settings.local.json").exists())

    def test_null_delete_of_a_nested_path_that_is_absent_is_infra(self):
        self.fc.write(".claude/settings.local.json", json.dumps({"a": {"x": 1}}))
        for patch in ({"a": {"b": None}}, {"nope": {"b": None}}, {"a": {"x": {"y": None}}}):
            with self.subTest(patch=patch), self.assertRaises(runner._Infra) as cm:
                self.apply(settings=patch)
            self.assertIn("not present", str(cm.exception))
        self.assertIn("a.b not present", self.try_message(settings={"a": {"b": None}}))

    def try_message(self, **kw):
        try:
            self.apply(**kw)
        except runner._Infra as e:
            return str(e)
        return ""

    def test_null_delete_of_an_absent_env_key_is_infra(self):
        self.fc.write(".claude/settings.local.json", json.dumps({"env": {"A": "1"}}))
        with self.assertRaises(runner._Infra) as cm:
            self.apply(env={"CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS": None})
        self.assertIn("env: CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS not present", str(cm.exception))
        self.assertEqual(self.fc.settings(), {"env": {"A": "1"}})
        with self.assertRaises(runner._Infra):  # no env block at all
            self.fc.write(".claude/settings.local.json", "{}")
            self.apply(env={"A": None})

    def test_os_error_while_patching_is_infra(self):
        self.fc.write("a.md", "x")
        self.fc.write("b.md", "x")
        for kw, target in (({"remove": ["a.md"]}, "unlink"), ({"edit": [Edit("b.md", "x", "y")]}, "write_text"),
                           ({"settings": {"a": 1}}, "write_text")):
            with self.subTest(kw=kw), mock.patch.object(Path, target, side_effect=PermissionError(13, "denied")):
                with self.assertRaises(runner._Infra) as cm:
                    self.apply(**kw)
                self.assertIn("variant v: cannot patch the contest (PermissionError", str(cm.exception))

    def test_env_is_merged_into_the_env_block_and_null_deletes(self):
        self.fc.write(".claude/settings.local.json", json.dumps({"env": {"A": "1", "B": "2"}}))
        self.apply(env={"B": None, "C": "3"})
        self.assertEqual(self.fc.settings(), {"env": {"A": "1", "C": "3"}})

    def test_env_follows_settings(self):
        self.apply(settings={"env": {"A": "1", "B": "2"}}, env={"A": None, "C": "3"})
        self.assertEqual(self.fc.settings(), {"env": {"B": "2", "C": "3"}})

    def test_no_settings_or_env_leaves_the_file_alone(self):
        self.apply(prompt="x")
        self.assertFalse((self.fc.contest / ".claude/settings.local.json").exists())

    def test_unreadable_settings_is_infra(self):
        self.fc.write(".claude/settings.local.json", "{not json")
        with self.assertRaises(runner._Infra):
            self.apply(settings={"a": 1})

    def test_env_over_a_non_object_is_infra(self):
        self.fc.write(".claude/settings.local.json", json.dumps({"env": "oops"}))
        with self.assertRaises(runner._Infra):
            self.apply(env={"A": "1"})

    def test_edit_replaces_every_match(self):
        path = self.fc.write("docs/a.md", "limit: 100\nlimit: 100\n")
        self.apply(edit=[Edit("docs/a.md", r"limit: (\d+)", r"max: \1")])
        self.assertEqual(path.read_text(), "max: 100\nmax: 100\n")

    def test_edit_that_matches_nothing_is_infra(self):
        path = self.fc.write("a.md", "hello")
        with self.assertRaises(runner._Infra) as cm:
            self.apply(edit=[Edit("a.md", "nope", "x")])
        self.assertIn("matched nothing", str(cm.exception))
        self.assertEqual(path.read_text(), "hello")

    def test_edit_of_a_missing_file_is_infra(self):
        with self.assertRaises(runner._Infra):
            self.apply(edit=[Edit("absent.md", "x", "y")])

    def test_edits_apply_in_order(self):
        path = self.fc.write("a.md", "one")
        self.apply(edit=[Edit("a.md", "one", "two"), Edit("a.md", "two", "three")])
        self.assertEqual(path.read_text(), "three")

    def test_remove_file_and_tree(self):
        self.fc.write("a.md", "x")
        self.fc.write("tree/sub/b.md", "x")
        self.apply(remove=["a.md", "tree"])
        self.assertFalse((self.fc.contest / "a.md").exists())
        self.assertFalse((self.fc.contest / "tree").exists())
        self.assertTrue(self.fc.contest.is_dir())

    def test_remove_of_a_missing_path_is_infra(self):
        with self.assertRaises(runner._Infra) as cm:
            self.apply(remove=["absent"])
        self.assertIn("absent", str(cm.exception))

    def test_remove_runs_before_edit(self):
        self.fc.write("a.md", "x")
        with self.assertRaises(runner._Infra):  # the edit finds the file already gone
            self.apply(remove=["a.md"], edit=[Edit("a.md", "x", "y")])

    def test_paths_escaping_the_contest_are_infra(self):
        outside = self.fc.contest.parent / "outside.md"
        outside.write_text("secret")
        (self.fc.contest / "link").symlink_to(outside)
        (self.fc.contest / "dirlink").symlink_to(self.fc.contest.parent)
        for kw in ({"remove": ["../outside.md"]}, {"edit": [Edit("../outside.md", "s", "t")]},
                   {"edit": [Edit("link", "s", "t")]}, {"edit": [Edit("dirlink/outside.md", "s", "t")]},
                   {"remove": ["dirlink/outside.md"]}, {"remove": ["/etc/hostname"]}, {"remove": ["."]}):
            with self.subTest(kw=kw), self.assertRaises(runner._Infra):
                self.apply(**kw)
        self.assertEqual(outside.read_text(), "secret")

    def test_remove_of_a_symlink_removes_the_link_only(self):
        outside = self.fc.contest.parent / "outside.md"
        outside.write_text("secret")
        (self.fc.contest / "link").symlink_to(outside)
        self.apply(remove=["link"])
        self.assertFalse((self.fc.contest / "link").is_symlink())
        self.assertEqual(outside.read_text(), "secret")

    def test_variants_do_not_see_each_others_patch(self):
        """Each run has its own temp root: a patch applied to one never shows in the next, and `apply_variant`
        does not change the variant (so it can be applied again to a fresh root)."""
        a = Variant("a", settings={"env": {"A": "1"}}, edit=[Edit("f.md", "base", "patched-a")])
        b = Variant("b", settings={"env": {"B": "2"}})
        first, second, third = FakeContest(self), FakeContest(self), FakeContest(self)
        for fc in (first, second, third):
            fc.write("f.md", "base")
        runner.apply_variant(first.root, a)
        runner.apply_variant(second.root, b)
        runner.apply_variant(third.root, a)
        self.assertEqual(first.settings(), {"env": {"A": "1"}})
        self.assertEqual(second.settings(), {"env": {"B": "2"}})
        self.assertEqual((second.contest / "f.md").read_text(), "base")
        self.assertEqual((third.contest / "f.md").read_text(), "patched-a")
        self.assertEqual(third.settings(), first.settings())
        self.assertEqual(a.edit, [Edit("f.md", "base", "patched-a")])


class PromptTest(unittest.TestCase):
    def setUp(self):
        self.fc = FakeContest(self)

    def resolve(self, text, default="case prompt"):
        return runner.variant_prompt(self.fc.root, Variant("v", prompt=text), default)

    def test_without_a_prompt_the_case_prompt_stays(self):
        self.assertEqual(runner.variant_prompt(self.fc.root, Variant("v"), "case prompt"), "case prompt")
        self.assertEqual(runner.variant_prompt(self.fc.root, None, "case prompt"), "case prompt")

    def test_file_references_are_expanded(self):
        self.fc.write("docs/a.md", "ALPHA ünï")
        self.fc.write("b.md", "BETA")
        self.assertEqual(self.resolve("read {file:docs/a.md} and {file:b.md} and {file:b.md}"),
                         "read ALPHA ünï and BETA and BETA")

    def test_expanded_text_is_not_expanded_again(self):
        self.fc.write("a.md", "see {file:b.md}")
        self.fc.write("b.md", "BETA")
        self.assertEqual(self.resolve("{file:a.md}"), "see {file:b.md}")

    def test_prompt_is_resolved_after_the_edits(self):
        self.fc.write("a.md", "old")
        runner.apply_variant(self.fc.root, Variant("v", edit=[Edit("a.md", "old", "new")], prompt="{file:a.md}"))
        self.assertEqual(self.resolve("{file:a.md}"), "new")

    def test_missing_file_is_infra(self):
        with self.assertRaises(runner._Infra):
            self.resolve("{file:absent.md}")

    def test_removed_file_is_infra(self):
        self.fc.write("a.md", "x")
        runner.apply_variant(self.fc.root, Variant("v", remove=["a.md"]))
        with self.assertRaises(runner._Infra):
            self.resolve("{file:a.md}")

    def test_escaping_paths_are_infra(self):
        (self.fc.contest.parent / "outside.md").write_text("secret")
        (self.fc.contest / "link").symlink_to(self.fc.contest.parent / "outside.md")
        for ref in ("../outside.md", "link", "/etc/hostname", "."):
            with self.subTest(ref=ref), self.assertRaises(runner._Infra):
                self.resolve("{file:%s}" % ref)

    def test_not_utf8_is_infra(self):
        (self.fc.contest / "bin").write_bytes(b"\xff\xfe\x00")
        with self.assertRaises(runner._Infra):
            self.resolve("{file:bin}")

    def test_length_limit(self):
        self.fc.write("big.md", "x" * runner.MAX_PROMPT_CHARS)
        self.assertEqual(len(self.resolve("{file:big.md}")), runner.MAX_PROMPT_CHARS)
        with self.assertRaises(runner._Infra) as cm:
            self.resolve("{file:big.md}y")
        self.assertIn("2000000", str(cm.exception))
        with self.assertRaises(runner._Infra):
            self.resolve("{file:big.md}{file:big.md}")


class RunCaseVariantTest(trun.RunnerCase):
    def setUp(self):
        super().setUp()
        self.root.commit_patch = lambda: None  # a real `TempRoot` commits the patch (CommitPatchTest)

    def test_variant_is_applied_before_the_container_starts(self):
        (self.root.contest / "docs").mkdir()
        (self.root.contest / "docs/a.md").write_text("old rule")
        variant = Variant("v", edit=[Edit("docs/a.md", "old", "new")], settings={"env": {"K": "1"}},
                          prompt="Follow: {file:docs/a.md}")
        result, popens, _, _ = self.go(trun.make_case(), trun.Popens(stdout=trun.ASSISTANT + "\n"), variant=variant)
        self.assertIsNone(result.infra)
        self.assertEqual(popens.procs[0].stdin.lines()[0]["message"]["content"], "Follow: new rule")
        self.assertEqual(json.loads((self.root.contest / ".claude/settings.local.json").read_text()),
                         {"env": {"K": "1"}})

    def test_without_a_variant_nothing_changes(self):
        result, popens, _, _ = self.go(trun.make_case(), trun.Popens(stdout=trun.ASSISTANT + "\n"))
        self.assertEqual(popens.procs[0].stdin.lines()[0]["message"]["content"], "do the thing")
        self.assertFalse((self.root.contest / ".claude/settings.local.json").exists())

    def test_os_error_in_a_patch_is_an_infra_result_not_an_exception(self):
        (self.root.contest / "a.md").write_text("x")
        variant = Variant("v", remove=["a.md"])
        with mock.patch.object(Path, "unlink", side_effect=OSError(5, "I/O error")):
            result, popens, _, _ = self.go(trun.make_case(), variant=variant)
        self.assertEqual(popens.procs, [])
        self.assertIn("variant v: cannot patch the contest", result.infra)
        self.assertFalse(runner.is_setup_error(result.infra))

    def test_failing_patch_is_infra_and_nothing_starts(self):
        variant = Variant("v", edit=[Edit("absent.md", "x", "y")])
        result, popens, docker, _ = self.go(trun.make_case(), variant=variant)
        self.assertEqual(popens.procs, [])
        self.assertFalse(any(c[:2] == ["docker", "run"] for c in docker.calls))
        self.assertIn("variant v", result.infra)
        self.assertFalse(runner.is_setup_error(result.infra))
        self.assertTrue(result.log.exists())


def stream_log(path: Path, partial: bool = False) -> Path:
    """Two main calls, one sub-agent call, one auto compaction and the `result` total."""
    calls = [s_asst("m1", usage(10, 0, 500, 0, 100)), s_asst("m2", usage(2, 500, 30, 0, 50)),
             s_asst("s1", usage(1, 0, 9000, 0, 20), side_parent="toolu_1")]
    if partial:
        for c in calls:
            c["message"]["stop_reason"] = None
    boundary = {"type": "system", "subtype": "compact_boundary", "session_id": calls[0]["session_id"],
                "compact_metadata": {"trigger": "auto", "pre_tokens": 530, "post_tokens": 50}}
    return write_jsonl(path, {"type": "system", "subtype": "init", "session_id": calls[0]["session_id"]},
                       calls[0], boundary, calls[1], calls[2], s_result({OPUS: (13, 500, 9530, 700)}))


class MetricsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_metrics_of_a_stream_with_a_compaction_and_a_sub_agent(self):
        got = results.run_metrics(stream_log(self.dir / "run.jsonl"))
        # the buckets come from the `result` total: input 13, cache_read 500*0.1, cache_write_5m 9530*1.25 (no TTL
        # split in the total, none per call either), output 700*5; calls and peak per call
        self.assertEqual(got, {"weighted": round(13 + 50 + 9530 * 1.25 + 700 * 5), "peak": 532, "calls": 3,
                               "compactions": 1, "output": 700})

    def test_partial_stream_output_comes_from_the_result_total(self):
        got = results.run_metrics(stream_log(self.dir / "run.jsonl", partial=True))
        self.assertEqual((got["output"], got["calls"], got["compactions"]), (700, 3, 1))
        self.assertEqual(got["weighted"], round(13 + 50 + 9530 * 1.25 + 700 * 5))

    def test_missing_or_empty_log_is_all_zero(self):
        zero = {"weighted": 0, "peak": 0, "calls": 0, "compactions": 0, "output": 0}
        self.assertEqual(results.run_metrics(self.dir / "absent.jsonl"), zero)
        (self.dir / "empty.jsonl").write_text("")
        self.assertEqual(results.run_metrics(self.dir / "empty.jsonl"), zero)

    def test_metrics_hold_numbers_only(self):
        got = results.run_metrics(stream_log(self.dir / "run.jsonl"))
        self.assertTrue(all(type(v) is int for v in got.values()))


class VariantCliTest(tcli.EvalCliBase):
    VARIANTS = {"ctl": {"settings": {"env": {"X": "1"}}}, "other": {"remove": ["x"]}}

    def setUp(self):
        super().setUp()
        self.write_case("alpha", variants=self.VARIANTS)
        self.variants_seen: list = []

    def fake_run(self, case, root, model=None, **kw):
        self.variants_seen.append(kw.get("variant"))
        result = super().fake_run(case, root, model, **kw)
        stream_log(result.log)
        return result

    def test_default_run_has_variant_default_and_metrics(self):
        code, _, _ = self.call("run", "--name", "alpha")
        self.assertEqual(code, 0)
        (row,) = self.rows()
        self.assertEqual(row["variant"], "default")
        self.assertEqual(row["metrics"], {"weighted": round(13 + 50 + 9530 * 1.25 + 700 * 5), "peak": 532,
                                          "calls": 3, "compactions": 1, "output": 700})
        self.assertEqual(self.variants_seen, [None])

    def test_variant_flag_runs_that_variant(self):
        code, _, _ = self.call("run", "--name", "alpha", "--variant", "ctl")
        self.assertEqual(code, 0)
        (row,) = self.rows()
        self.assertEqual(row["variant"], "ctl")
        self.assertEqual(self.variants_seen, [cases.Variant("ctl", settings={"env": {"X": "1"}})])

    def test_explicit_default_means_no_patch(self):
        self.call("run", "--name", "alpha", "--variant", "default")
        self.assertEqual(self.variants_seen, [None])
        self.assertEqual(self.rows()[0]["variant"], "default")

    def test_case_hash_does_not_depend_on_the_variant(self):
        self.call("run", "--name", "alpha")
        self.call("run", "--name", "alpha", "--variant", "ctl")
        a, b = self.rows()
        self.assertEqual(a["case_hash"], b["case_hash"])

    def test_unknown_variant_is_exit_2_before_any_run(self):
        code, _, err = self.call("run", "--name", "alpha", "--variant", "nope")
        self.assertEqual(code, 2)
        self.assertIn("nope", err)
        self.assertEqual(self.runs, [])
        self.assertEqual(FakeRootEntered.count(), 0)

    def test_case_without_variants_accepts_only_default(self):
        self.write_case("plain")
        code, _, err = self.call("run", "--name", "plain", "--variant", "ctl")
        self.assertEqual(code, 2)
        self.assertIn("plain", err)
        self.assertEqual(self.runs, [])
        self.assertEqual(self.call("run", "--name", "plain", "--variant", "default")[0], 0)

    def test_variant_must_exist_in_every_selected_case(self):
        self.write_case("plain", tags=["t"])
        self.write_case("alpha", variants=self.VARIANTS, tags=["t"])
        code, _, err = self.call("run", "--tag", "t", "--variant", "ctl")
        self.assertEqual(code, 2)
        self.assertEqual(self.runs, [])

    def test_repeat_writes_one_row_per_run_each_in_a_fresh_root(self):
        code, _, _ = self.call("run", "--name", "alpha", "--variant", "ctl", "--repeat", "2")
        self.assertEqual(code, 0)
        rows = self.rows()
        self.assertEqual([r["variant"] for r in rows], ["ctl", "ctl"])
        self.assertEqual(len(tcli.FakeRoot.entered), 2)
        self.assertEqual(len(self.out_dirs), 2)

    def test_a_patch_failure_goes_to_the_grader_not_to_setup(self):
        """`run.infra` of a failed patch is not a setup error, so the grader decides: `infra_error` (grade tests)."""
        self.infra["alpha"] = ["variant ctl: edit of a.md matched nothing"]
        self.verdicts["alpha"] = ["infra_error"]
        self.call("run", "--name", "alpha", "--variant", "ctl")
        self.assertEqual(self.graded, ["alpha"])
        (row,) = self.rows()
        self.assertEqual((row["result"], row["variant"]), ("infra_error", "ctl"))

    def test_setup_failure_row_still_has_variant_and_zero_metrics(self):
        tcli.FakeRoot.fail_setup = [RuntimeError("boom")]
        self.call("run", "--name", "alpha", "--variant", "ctl")
        (row,) = self.rows()
        self.assertEqual(row["variant"], "ctl")
        self.assertEqual(row["metrics"]["calls"], 0)


def result_row(variant=None, result="pass", i=0):
    r = {"case": "a", "case_hash": "h", "result": result, "kind": "capability", "commit": "c1", "model": "m",
         "date": f"2026-11-17T10:00:{i:02d}+01:00"}
    if variant is not None:
        r["variant"] = variant
    return r


class StatusVariantTest(unittest.TestCase):
    def test_variants_of_one_case_are_separate_groups(self):
        rows = [result_row("default", "pass", 0), result_row("ctl", "fail", 1), result_row("ctl", "fail", 2),
                result_row("default", "pass", 3)]
        by = {g["variant"]: g for g in results.status(rows)}
        self.assertEqual(sorted(by), ["ctl", "default"])
        self.assertEqual((by["default"]["runs"], by["default"]["passes"]), (2, 2))
        self.assertEqual((by["ctl"]["runs"], by["ctl"]["passes"]), (2, 0))

    def test_rows_without_a_variant_count_as_default(self):
        rows = [result_row(None, "pass", 0), result_row("default", "pass", 1), result_row("ctl", "pass", 2)]
        by = {g["variant"]: g for g in results.status(rows)}
        self.assertEqual(by["default"]["runs"], 2)
        self.assertEqual(by["ctl"]["runs"], 1)


class StatusCliTest(tcli.EvalCliBase):
    def put(self, *rows):
        for r in rows:
            results.append(self.results_path, r)

    def test_status_shows_one_line_per_variant_with_a_variant_column(self):
        self.put(result_row(None, "pass", 0), result_row("ctl", "fail", 1))
        out = self.call("status", "--all")[1]
        header = out.splitlines()[0].split()
        self.assertIn("variant", header)
        lines = [ln.split() for ln in out.splitlines()[1:]]
        self.assertEqual(sorted(ln[header.index("variant")] for ln in lines), ["ctl", "default"])
        self.assertEqual(len(lines), 2)


class CommitPatchTest(ScratchBase):
    """A real temp root: `remove`/`edit` are committed as one neutral commit before the agent could start, so the
    contest starts clean and the host baselines describe the patched state."""

    def setUp(self):
        super().setUp()
        self.root = self.enter(self.temp_root())

    def enter(self, t):
        t.__enter__()
        self.addCleanup(t.cleanup)
        return t

    def contest_head(self):
        return git(self.root.contest, "rev-parse", "main").strip()

    def test_remove_variant_leaves_a_clean_contest_and_one_neutral_commit(self):
        before = self.contest_head()
        runner.apply_variant(self.root, Variant("secret-name", remove=["test_calc.py"]))
        self.assertFalse((self.root.contest / "test_calc.py").exists())
        self.assertEqual(git(self.root.contest, "status", "--porcelain").strip(), "")
        self.assertEqual(git(self.root.contest, "rev-list", "--count", f"{before}..main").strip(), "1")
        self.assertEqual(git(self.root.contest, "log", "-1", "--format=%s%n%an <%ae>").splitlines(),
                         ["chore: setup", "House Eval <eval@house.invalid>"])
        self.assertNotIn("secret-name", git(self.root.contest, "log", "--format=%B"))
        # the graders' reference is the patched state
        self.assertEqual(self.root.info["initial_commit"], self.contest_head())
        self.assertNotIn("test_calc.py", self.root.baseline)
        self.assertEqual(git(self.root.contest, "rev-parse", "origin/main").strip(), self.contest_head())
        self.assertTrue(grade._git_clean(self.root.contest, self.root.info["initial_commit"], {}))

    def test_edit_variant_baseline_has_the_patched_file(self):
        runner.apply_variant(self.root, Variant("v", edit=[Edit("calc.py", "NotImplementedError", "ValueError")]))
        self.assertEqual(git(self.root.contest, "status", "--porcelain").strip(), "")
        self.assertIn("ValueError", (self.root.contest / "calc.py").read_text())
        blob = git(self.root.contest, "rev-parse", "main:calc.py").strip()
        self.assertEqual(self.root.baseline["calc.py"][0], blob)
        self.assertTrue(grade._file_unchanged(self.root.contest, self.root.info["initial_commit"],
                                              {"path": "calc.py"}, baseline=self.root.baseline))

    def test_settings_and_env_are_not_committed(self):
        before = self.contest_head()
        runner.apply_variant(self.root, Variant("v", settings={"a": 1}, env={"K": "1"}))
        self.assertEqual(self.contest_head(), before)
        self.assertEqual(git(self.root.contest, "status", "--porcelain").strip(), "")  # settings.local.json is excluded

    def test_untouched_run_of_a_remove_variant_passes_git_clean(self):
        runner.apply_variant(self.root, Variant("v", remove=["test_calc.py"]))
        self.assertTrue(grade._git_clean(self.root.contest, self.root.info["initial_commit"], {}))
        self.assertTrue(grade._file_unchanged(self.root.contest, self.root.info["initial_commit"],
                                              {"path": "calc.py"}, baseline=self.root.baseline))


class FakeRootEntered:
    @staticmethod
    def count():
        return len(tcli.FakeRoot.entered)


if __name__ == "__main__":
    unittest.main()
