"""K5 infra fix wave (C2-C10 of the Codex review): conversation completion, stdin transport, prompt material,
cohorts, hidden-test robustness, label names and `from_turn`. Fakes and synthetic logs only."""
import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from houselib.evals import cases, fixtures, grade, results, runner, sandbox
from houselib.evals.cases import Copy, Edit, Variant
from houselib.evals.runner import RunResult
from tests import test_eval_cli as tcli
from tests import test_eval_compare as tcmp
from tests import test_eval_followups as tfol
from tests import test_eval_grade as tgrade
from tests import test_eval_runner as trun
from tests import test_eval_variants as tvar
from tests.helpers import git
from tests.test_eval_sandbox import ScratchBase


class CompletionTest(tgrade.GradeBase):
    """C4: a run is complete only when every scheduled turn ended in a `success` result and the process exited."""

    def case(self, followups=("second",), **kw):
        return tgrade.make_case(followups=list(followups), outcome=[{"type": "file_exists", "path": "keep.txt",
                                                                     "exists": True}], **kw)

    def test_all_turns_succeeded_is_graded(self):
        log = tgrade.Log().bash("ls").result("one", False).bash("ls").result("two", False)
        g = self.grade(self.case(), log)
        self.assertEqual((g.result, g.score), ("pass", 1.0))

    def test_a_missing_last_turn_is_incomplete(self):
        log = tgrade.Log().bash("ls").result("one", False)
        g = self.grade(self.case(), log)
        self.assertEqual((g.result, g.note, g.score, g.predicates), ("fail", "incomplete", None, {}))

    def test_an_error_result_of_a_later_turn_is_incomplete(self):
        log = tgrade.Log().bash("ls").result("one", False).result("boom", True, subtype="error_during_execution")
        self.assertEqual(self.grade(self.case(), log).note, "incomplete")

    def test_a_one_turn_case_ending_in_an_error_result_is_incomplete(self):
        log = tgrade.Log().bash("ls").result("boom", True, subtype="error_during_execution")
        self.assertEqual(self.grade(self.case(followups=()), log).note, "incomplete")

    def test_more_successes_than_turns_is_incomplete(self):
        log = tgrade.Log().bash("ls").result("one", False).result("two", False).result("three", False)
        self.assertEqual(self.grade(self.case(), log).note, "incomplete")

    def test_a_process_that_did_not_exit_is_incomplete(self):
        log = tgrade.Log().bash("ls").result("one", False).result("two", False)
        self.assertEqual(self.grade(self.case(), log, exit=None).note, "incomplete")

    def test_max_turns_and_timeout_keep_their_notes(self):
        log = tgrade.Log().bash("ls").result("", True, subtype="error_max_turns")
        self.assertEqual(self.grade(self.case(), log).note, "max_turns")
        self.assertEqual(self.grade(self.case(), tgrade.Log().bash("ls"), timed_out=True, exit=None).note, "timeout")


class TurnCountTest(trun.RunnerCase):
    """C4: the runner records how many turns it sent and how many ended in a `success` result."""

    def test_all_turns_sent_and_succeeded(self):
        result, _, _, _ = self.go(trun.make_case(followups=["two", "three"]), tfol.StdinPopens())
        self.assertEqual((result.turns_sent, result.turns_ok), (3, 3))

    def test_a_failed_turn_stops_the_count(self):
        result, _, _, _ = self.go(trun.make_case(followups=["two", "three"]),
                                  tfol.StdinPopens(results=[tfol.result_line(), tfol.result_line("error_max_turns")]))
        self.assertEqual((result.turns_sent, result.turns_ok), (2, 1))

    def test_nothing_started_counts_nothing(self):
        result, _, _, _ = self.go(trun.make_case(), installed=False)
        self.assertEqual((result.turns_sent, result.turns_ok), (0, 0))


class TurnCountRowTest(tcli.EvalCliBase):
    """C4: the results row carries the runner's turn counts."""

    def test_row_has_turns_sent_and_ok(self):
        def run(case, root, model=None, **kw):
            done = self.fake_run(case, root, model, **kw)
            return RunResult(done.log, 0, 1.0, None, False, "sid", done.out_dir, turns_sent=3, turns_ok=2)

        with mock.patch.object(tcli.cli.evalrunner, "run_case", side_effect=run):
            self.call("run", "--name", "alpha")
        row = self.rows()[-1]
        self.assertEqual((row["turns_sent"], row["turns_ok"]), (3, 2))


class StdinTransportTest(trun.RunnerCase):
    """C2: every Claude run sends its prompt over stdin stream-json, never argv (a 500K prompt exceeds E2BIG)."""

    def test_a_one_turn_case_uses_stdin_and_no_prompt_in_argv(self):
        result, popens, _, _ = self.go(trun.make_case(), tfol.StdinPopens())
        proc = popens.procs[0]
        self.assertIsNone(result.infra)
        self.assertEqual(proc.kw["stdin"], subprocess.PIPE)
        self.assertEqual(trun.option(proc.argv, "--input-format"), "stream-json")
        self.assertNotIn("do the thing", proc.argv)
        self.assertNotIn("--", proc.argv[proc.argv.index("claude"):])
        self.assertEqual(proc.stdin.lines(), [{"type": "user", "message": {"role": "user", "content": "do the thing"}}])
        self.assertTrue(proc.stdin.closed.is_set())
        self.assertEqual((result.turns_sent, result.turns_ok), (1, 1))

    def test_a_huge_prompt_never_reaches_argv(self):
        prompt = "x" * 500_000
        _, popens, _, _ = self.go(trun.make_case(prompt=prompt), tfol.StdinPopens())
        proc = popens.procs[0]
        self.assertLess(max(len(a) for a in proc.argv), 10_000)
        self.assertEqual(proc.stdin.lines()[0]["message"]["content"], prompt)

    def test_codex_keeps_devnull_stdin(self):
        with mock.patch("houselib.evals.runner.shutil.which", return_value="/usr/bin/codex"):
            result, popens, _, _ = self.go(trun.make_case(agent="coder-codex", tool="codex", model=None),
                                           trun.Popens(stdout=trun.CODEX_DONE + "\n"))
        self.assertEqual(popens.procs[0].kw["stdin"], subprocess.DEVNULL)
        self.assertEqual((result.turns_sent, result.turns_ok), (0, 0))


class MaterialRoot:
    """A temp dir shaped like a `TempRoot` for `apply_variant`: `<tmp>/material`, `<tmp>/house/contests/calc`."""

    def __init__(self, test: unittest.TestCase):
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.contest = self.tmp / "house/contests/calc"
        (self.contest / ".claude").mkdir(parents=True)
        (self.tmp / fixtures.MATERIAL_DIR).mkdir()
        self.commits = 0
        self.seen_at_commit: list[bool] = []

    def commit_patch(self):
        self.commits += 1
        self.seen_at_commit.append((self.contest / "docs/rules.md").exists())


class MaterialFixtureTest(unittest.TestCase):
    """C8: fixtures write prompt material outside the contest, like hidden tests."""

    def test_write_material_goes_to_tmp_material(self):
        root = MaterialRoot(self)
        path = fixtures.write_material(root.contest, "rules.md", "the rules\n")
        self.assertEqual(path, root.tmp / "material/rules.md")
        self.assertEqual(path.read_text(), "the rules\n")
        self.assertNotIn(root.contest, path.parents)

    def test_material_names_stay_below_the_material_dir(self):
        root = MaterialRoot(self)
        for bad in ("../x", "/abs", "", "."):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                fixtures.write_material(root.contest, bad, "x")


class MaterialLoadTest(unittest.TestCase):
    """C8: the variant key `copy`."""

    def load(self, copy):
        return tvar.load_case(variants={"v": {"copy": copy}})

    def test_copy_entries(self):
        case = self.load([{"material": "rules.md", "to": ".house/sources/rules.md"}])
        self.assertEqual(case.variants["v"].copy, [Copy("rules.md", ".house/sources/rules.md")])

    def test_bad_copy_entries_are_rejected(self):
        bad = ["x", [{"material": "rules.md"}], [{"to": "a"}], [{"material": "../x", "to": "a"}],
               [{"material": "rules.md", "to": "../a"}], [{"material": "rules.md", "to": "a", "extra": 1}],
               [{"material": "", "to": "a"}], ["rules.md"]]
        for copy in bad:
            with self.subTest(copy=copy), self.assertRaisesRegex(ValueError, r"c.json: variants\.v\.copy"):
                self.load(copy)


class MaterialApplyTest(unittest.TestCase):
    """C8: `copy` puts material into the contest before the `chore: setup` commit; `{material:}` reads it."""

    def setUp(self):
        self.root = MaterialRoot(self)
        fixtures.write_material(self.root.contest, "rules.md", "RULES TEXT\n")

    def test_copy_lands_before_the_commit(self):
        runner.apply_variant(self.root, Variant("v", copy=[Copy("rules.md", "docs/rules.md")]))
        self.assertEqual((self.root.contest / "docs/rules.md").read_text(), "RULES TEXT\n")
        self.assertEqual((self.root.commits, self.root.seen_at_commit), (1, [True]))

    def test_copy_runs_after_remove_and_before_edit(self):
        (self.root.contest / "docs").mkdir()
        (self.root.contest / "docs/old.md").write_text("old")
        runner.apply_variant(self.root, Variant("v", remove=["docs/old.md"],
                                                copy=[Copy("rules.md", "docs/rules.md")],
                                                edit=[Edit("docs/rules.md", "RULES", "EDITED")]))
        self.assertFalse((self.root.contest / "docs/old.md").exists())
        self.assertEqual((self.root.contest / "docs/rules.md").read_text(), "EDITED TEXT\n")

    def test_copy_refuses_a_missing_material_an_existing_target_and_an_escape(self):
        (self.root.contest / "taken.md").write_text("x")
        outside = self.root.tmp / "secret.txt"
        outside.write_text("secret")
        (self.root.tmp / "material/link.md").symlink_to(outside)
        for copy, text in ((Copy("absent.md", "a.md"), "material 'absent.md' is missing"),
                           (Copy("rules.md", "taken.md"), "'taken.md' exists"),
                           (Copy("link.md", "a.md"), "material 'link.md' is missing")):
            with self.subTest(copy=copy), self.assertRaisesRegex(runner._Infra, f"variant v: copy: .*{text}"):
                runner.apply_variant(self.root, Variant("v", copy=[copy]))

    def test_material_placeholder_in_the_prompt(self):
        got = runner.variant_prompt(self.root, Variant("v", prompt="Read:\n{material:rules.md}end"), "case")
        self.assertEqual(got, "Read:\nRULES TEXT\nend")

    def test_material_placeholder_with_a_missing_or_escaping_name_is_infra(self):
        for name in ("absent.md", "../house/contests/calc/.claude"):
            with self.subTest(name=name), self.assertRaisesRegex(runner._Infra, "variant v: prompt: "):
                runner.variant_prompt(self.root, Variant("v", prompt=f"{{material:{name}}}"), "case")

    def test_material_is_not_expanded_again(self):
        fixtures.write_material(self.root.contest, "nested.md", "{file:docs/x.md} {material:rules.md}")
        got = runner.variant_prompt(self.root, Variant("v", prompt="{material:nested.md}"), "case")
        self.assertEqual(got, "{file:docs/x.md} {material:rules.md}")


def material_build(contest, *, env=None, **params):
    """`small_build` plus a document that exists only as material (like `long-input`'s base fixture)."""
    info = fixtures.small_build(contest, env=env, **params)
    fixtures.write_material(contest, "rules.md", "MATERIAL-CANARY-7f1 rules\n")
    return info


class MaterialTempRootTest(ScratchBase):
    """C8 with a real temp root: the material is never in the contest or its history unless a variant copies it."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(sandbox.BUILDERS, {"material_build": material_build})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = self.temp_root({"name": "material_build", "contest": "fx"})
        self.root.__enter__()
        self.addCleanup(self.root.cleanup)

    def history_has_canary(self) -> bool:
        return bool(git(self.root.contest, "log", "--all", "-S", "MATERIAL-CANARY-7f1", "--format=%H").strip())

    def test_material_dir_is_private_and_outside_the_contest(self):
        material = self.root.tmp / fixtures.MATERIAL_DIR
        self.assertEqual(material.stat().st_mode & 0o777, 0o700)
        self.assertTrue((material / "rules.md").is_file())
        self.assertFalse(self.history_has_canary())

    def test_pasted_arm_keeps_the_document_out_of_the_contest(self):
        variant = Variant("pasted", prompt="Rules:\n{material:rules.md}")
        runner.apply_variant(self.root, variant)
        self.assertIn("MATERIAL-CANARY-7f1", runner.variant_prompt(self.root, variant, "case"))
        self.assertFalse(self.history_has_canary())
        self.assertFalse(any("MATERIAL-CANARY-7f1" in p.read_text(errors="replace")
                             for p in self.root.contest.rglob("*") if p.is_file() and ".git" not in p.parts))

    def test_sources_arm_copies_it_into_the_setup_commit(self):
        runner.apply_variant(self.root, Variant("sources", copy=[Copy("rules.md", ".house/sources/rules.md")]))
        self.assertEqual(git(self.root.contest, "status", "--porcelain").strip(), "")
        self.assertEqual(git(self.root.contest, "log", "-1", "--format=%s").strip(), "chore: setup")
        self.assertEqual(git(self.root.contest, "show", "main:.house/sources/rules.md"), "MATERIAL-CANARY-7f1 rules\n")
        self.assertIn(".house/sources/rules.md", self.root.baseline)


class MaterialMaskTest(trun.RunnerCase):
    """C8: the agent container masks `<tmp>/material` like `<tmp>/hidden`."""

    def test_material_dir_is_masked(self):
        _, popens, _, _ = self.go(trun.make_case(), tfol.StdinPopens())
        argv = popens.procs[0].argv
        masks = [argv[i + 1] for i, a in enumerate(argv) if a == "--tmpfs"]
        material = str(self.tmpdir / "material")
        self.assertTrue(any(m.startswith(material + ":ro") for m in masks), masks)


def cohort_row(variant="v", commit="c1", model="m", cli="2.1.327", cohort=None, score=1.0, weighted=100):
    row = tcmp.run(variant, score=score, weighted=weighted)
    row.update({"commit": commit, "model": model, "cli": cli})
    if cohort is not None:
        row["cohort"] = cohort
    return row


class CohortCompareTest(unittest.TestCase):
    """C3: compare never pools rows of different commits, models or CLI versions; `--cohort` picks one batch."""

    def test_rows_of_one_commit_model_and_cli_are_one_group(self):
        (g,) = results.compare([cohort_row(), cohort_row()], "a", "h1")
        self.assertEqual((g["n"], g["flags"]), (2, ["n<3"]))
        self.assertEqual((g["commit"], g["model"], g["cli"]), ("c1", "m", "2.1.327"))

    def test_different_commits_models_or_clis_are_split_and_flagged_mixed(self):
        rows = [cohort_row(), cohort_row(commit="c2"), cohort_row(model="opus"), cohort_row(cli="2.1.400"),
                cohort_row()]
        got = results.compare(rows, "a", "h1")
        self.assertEqual([(g["commit"], g["model"], g["cli"], g["n"]) for g in got],
                         [("c1", "m", "2.1.327", 2), ("c2", "m", "2.1.327", 1), ("c1", "opus", "2.1.327", 1),
                          ("c1", "m", "2.1.400", 1)])
        self.assertTrue(all("mixed" in g["flags"] for g in got))

    def test_cohort_selects_only_its_rows(self):
        rows = [cohort_row(cohort="smoke", score=0.0), cohort_row(cohort="k5", score=1.0),
                cohort_row(cohort="k5", score=0.5), cohort_row(score=0.0)]
        (g,) = results.compare(rows, "a", "h1", cohort="k5")
        self.assertEqual((g["n"], g["score_mean"]), (2, 0.75))
        self.assertNotIn("mixed", g["flags"])

    def test_a_mixed_cohort_is_still_split(self):
        rows = [cohort_row(cohort="k5"), cohort_row(cohort="k5", commit="c2")]
        got = results.compare(rows, "a", "h1", cohort="k5")
        self.assertEqual(len(got), 2)
        self.assertTrue(all("mixed" in g["flags"] for g in got))

    def test_render_names_the_group_when_mixed(self):
        lines = results.render_compare(results.compare(
            [cohort_row(commit="abcdef0123"), cohort_row(commit="9876543210")], "a", "h1"))
        self.assertTrue(lines[1].startswith("v@abcdef0/m/2.1.327 "), lines[1])
        self.assertTrue(lines[2].startswith("v@9876543/m/2.1.327 "), lines[2])
        self.assertIn("mixed", lines[1])

    def test_render_keeps_the_plain_variant_when_not_mixed(self):
        lines = results.render_compare(results.compare([cohort_row()], "a", "h1"))
        self.assertEqual(lines[1].split()[0], "v")


class CohortCliTest(tcli.EvalCliBase):
    """C3: `house eval run --cohort NAME` writes it into every row; `compare --cohort NAME` uses only those rows."""

    def test_run_writes_the_cohort(self):
        self.assertEqual(self.call("run", "--name", "alpha", "--cohort", "k5-main")[0], 0)
        self.assertEqual(self.rows()[-1]["cohort"], "k5-main")

    def test_run_without_a_cohort_writes_null(self):
        self.call("run", "--name", "alpha")
        self.assertIsNone(self.rows()[-1]["cohort"])

    def test_a_bad_cohort_name_exits_2(self):
        for bad in ("", "Has Space", "../x", "x" * 65):
            with self.subTest(bad=bad):
                self.assertEqual(self.call("run", "--name", "alpha", "--cohort", bad)[0], 2)
        self.assertEqual(self.runs, [])

    def test_compare_cohort(self):
        d = tcli.cli.evalcases.case_hash(self.root / "evals/cases/alpha.json")
        for cohort, variant in (("k5", "control"), ("smoke", "stale")):
            row = cohort_row(variant, cohort=cohort)
            row.update({"case": "alpha", "case_hash": d})
            results.append(self.results_path, row)
        code, out, _ = self.call("compare", "alpha", "--cohort", "k5")
        names = [ln.split()[0] for ln in out.splitlines() if ln]
        self.assertEqual(code, 0)
        self.assertIn("control", names)
        self.assertNotIn("stale", names)
        self.assertIn("no rows", self.call("compare", "alpha", "--cohort", "other")[1])


class BoundedSummaryTest(unittest.TestCase):
    """C6: numeric fields of a test summary are bounded (at most 9 digits); longer ones are a malformed summary."""

    def test_huge_counts_are_malformed_not_a_crash(self):
        for text in ("Ran " + "9" * 5000 + " tests in 0.1s\n\nOK\n",
                     "Ran 3 tests in 0.1s\n\nFAILED (failures=" + "1" * 5000 + ")\n",
                     "Ran 1234567890 tests in 0.1s\n\nOK\n",
                     "== " + "7" * 4000 + " passed in 0.1s ==\n"):
            with self.subTest(text=text[:30]), self.assertRaises(runner.MalformedSummary):
                runner.parse_test_summary(text)

    def test_a_superscript_count_is_malformed_not_a_crash(self):
        with self.assertRaises(runner.MalformedSummary):
            runner.parse_test_summary("Ran 3 tests in 0.1s\n\nFAILED (failures=\u00b2)\n")

    def test_nine_digits_are_fine(self):
        self.assertEqual(runner.parse_test_summary("Ran 999999999 tests in 1s\n\nOK\n"), (999999999, 0))


class HiddenRobustnessTest(trun.RunnerCase):
    """C6/C7: a malformed summary fails only its predicate, output on disk is capped, a hidden container that cannot
    be removed keeps the run's evidence like the agent container does."""

    def setUp(self):
        super().setUp()
        (self.tmpdir / "hidden/chain").mkdir(parents=True)
        (self.tmpdir / "hidden/chain/test_a.py").write_text("# hidden\n")
        self.case = trun.make_case(outcome=[dict(tfol.HIDDEN), dict(tfol.HIDDEN)])

    def run_hidden(self, hidden, docker=None, case=None):
        return self.go(case or self.case, tfol.HiddenPopens(hidden=hidden, stdout=trun.ASSISTANT + "\n"),
                       docker=docker)

    def test_a_malformed_summary_marks_only_its_predicate(self):
        huge = "Ran " + "9" * 5000 + " tests in 0.1s\n\nOK\n"
        result, _, _, _ = self.run_hidden(({"stderr": huge}, {"stderr": tfol.UNITTEST_OK}))
        self.assertIsNone(result.infra)
        self.assertEqual(result.hidden, {0: {"exit": 0, "passed": 0, "failed": 0, "malformed": True},
                                         1: {"exit": 0, "passed": 3, "failed": 0}})

    def test_output_files_are_capped_and_keep_the_summary(self):
        noise = "x" * 1000 + "\n"
        big = noise * 3000 + tfol.UNITTEST_OK  # ~3 MB, the summary last
        result, _, _, _ = self.run_hidden(({"stderr": big, "stdout": noise * 3000}, {"stderr": tfol.UNITTEST_OK}))
        self.assertEqual(result.hidden[0], {"exit": 0, "passed": 3, "failed": 0})
        for name in ("hidden-0.out", "hidden-0.err"):
            self.assertLessEqual((result.out_dir / name).stat().st_size, runner.HIDDEN_KEEP)
        self.assertTrue((result.out_dir / "hidden-0.err").read_text().endswith(tfol.UNITTEST_OK))

    def test_output_files_live_in_the_out_dir(self):
        result, _, _, _ = self.run_hidden(({"stderr": tfol.UNITTEST_OK}, {"stderr": tfol.UNITTEST_OK}))
        self.assertTrue((result.out_dir / "hidden-0.err").read_text().endswith(tfol.UNITTEST_OK))
        self.assertTrue((result.out_dir / "hidden-1.out").exists())

    def no_remove(self):
        class NoRemove(trun.FakeDocker):
            def __call__(self, argv, **kw):
                if argv[:3] == ["docker", "rm", "-f"] and "hidden-" in argv[3]:
                    return subprocess.CompletedProcess(argv, 1, "", "daemon gone")
                return super().__call__(argv, **kw)
        return NoRemove()

    def test_a_hidden_container_that_cannot_be_removed_is_cleanup_failed(self):
        with contextlib.redirect_stderr(io.StringIO()):
            result, popens, _, _ = self.run_hidden(({"hang": True},), docker=self.no_remove())
        name = trun.option(popens.procs[1].argv, "--name")
        self.assertTrue(result.cleanup_failed)
        self.assertEqual(result.leftover, name)
        self.assertIn("hidden tests: container not removed", result.infra)
        self.assertTrue(result.out_dir.is_dir())
        self.assertIn(name, (result.out_dir / runner.ERR_NAME).read_text())

    def test_an_interrupt_with_a_stuck_hidden_container_keeps_the_out_dir(self):
        popens = tfol.HiddenPopens(hidden=({"interrupt": True},))
        self.install(self.case)
        with mock.patch("houselib.evals.runner.os.killpg"), self.assertRaises(KeyboardInterrupt), \
                contextlib.redirect_stderr(io.StringIO()):
            runner.run_case(self.case, self.root, run=self.no_remove(), popen=popens,
                            clock=iter(range(0, 1000, 5)).__next__)
        (kept,) = list(self.outbase.iterdir())
        self.addCleanup(shutil.rmtree, kept, ignore_errors=True)
        self.assertIn("docker rm -f", (kept / runner.ERR_NAME).read_text())


class HiddenCleanupCliTest(tcli.EvalCliBase):
    """C7: the CLI keeps the out-dir and temp root of a run whose hidden container is stuck, as for the agent's."""

    def test_stuck_hidden_container_is_not_graded_and_keeps_everything(self):
        def run(case, root, model=None, **kw):
            done = self.fake_run(case, root, model, **kw)
            return RunResult(done.log, 0, 1.0, "hidden tests: container not removed", False, "sid", done.out_dir,
                             cleanup_failed=True, leftover="house-eval-hidden-x")

        with mock.patch.object(tcli.cli.evalrunner, "run_case", side_effect=run):
            code, _, err = self.call("run", "--name", "alpha")
        row = self.rows()[-1]
        self.assertEqual((row["result"], row["note"]), ("infra_error", "container not removed"))
        self.assertEqual(self.graded, [])
        self.assertTrue(self.out_dirs[-1].is_dir())
        self.assertIn("house-eval-hidden-x", err)


class HiddenGradeMalformedTest(tgrade.GradeBase):
    """C6: the grader fails a predicate whose summary was malformed, with a note."""

    def test_malformed_summary_fails_the_predicate(self):
        case = tgrade.make_case(outcome=[dict(tfol.HIDDEN)])
        g = self.grade(case, hidden={0: {"exit": 0, "passed": 0, "failed": 0, "malformed": True}})
        self.assertEqual(g.failed_predicates, ["outcome[0] hidden_tests"])
        self.assertIn("malformed test summary", g.note)


class ReservedLabelTest(unittest.TestCase):
    """C9: labels that look like generated outcome names are rejected, for calls and predicates alike."""

    def test_call_label_like_an_outcome_name_is_rejected(self):
        for section in ("expect", "forbid"):
            with self.subTest(section=section), self.assertRaisesRegex(ValueError, rf"c.json: {section}\[0\]\.label"):
                tfol.load(tfol.case_json(**{section: [{"tool": "Bash", "label": "outcome[0] hidden_tests"}],
                                            "outcome": [dict(tfol.HIDDEN)]}))

    def test_a_label_naming_a_later_outcome_index_is_rejected_too(self):
        with self.assertRaisesRegex(ValueError, r"c.json: forbid\[0\]\.label"):
            tfol.load(tfol.case_json(forbid=[{"tool": "Bash", "label": "outcome[7] git_clean"}]))

    def test_outcome_label_like_an_outcome_name_is_rejected(self):
        with self.assertRaisesRegex(ValueError, r"c.json: outcome\[0\]\.label"):
            tfol.load(tfol.case_json(outcome=[{"type": "git_clean", "label": "outcome[1] git_clean"}]))

    def test_other_labels_are_fine(self):
        case = tfol.load(tfol.case_json(forbid=[{"tool": "Bash", "label": "outcomes"}],
                                        outcome=[{"type": "git_clean", "label": "outcome-clean"}]))
        self.assertEqual([c.label for c in case.forbid], ["outcomes"])


class FromTurnLoadTest(unittest.TestCase):
    """C10: calls take an optional `from_turn` (integer >= 1, at most the number of user turns)."""

    def test_from_turn_is_kept(self):
        case = tfol.load(tfol.case_json(followups=["two", "three"],
                                        forbid=[{"tool": "Bash", "label": "late", "from_turn": 3}]))
        self.assertEqual(case.forbid[0].from_turn, 3)
        self.assertIsNone(tfol.load(tfol.case_json(forbid=[{"tool": "Bash"}])).forbid[0].from_turn)

    def test_bad_from_turn_is_rejected(self):
        for bad in (0, -1, "2", True, 1.5, 4):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, r"c.json: expect\[0\]\.from_turn"):
                tfol.load(tfol.case_json(followups=["two", "three"],
                                         expect=[{"tool": "Bash", "label": "x", "from_turn": bad}]))


class FromTurnGradeTest(tgrade.GradeBase):
    """C10: a call with `from_turn` counts only calls made during user turn >= from_turn (turn k = after the
    (k-1)-th `result` event)."""

    def case(self, **call):
        forbid = [tgrade.call("retry", "Bash", "fastcsv", call.get("min", 1))]
        forbid[0].from_turn = call.get("from_turn")
        return tgrade.make_case(followups=["two", "three"], forbid=forbid)

    def log(self, *turns):
        log = tgrade.Log()
        for k, commands in enumerate(turns, 1):
            for command in commands:
                log.bash(command)
            log.result(f"turn {k}", False)
        return log

    def test_calls_before_from_turn_do_not_count(self):
        g = self.grade(self.case(from_turn=3), self.log(["pip install fastcsv", "python3 -c 'import fastcsv'"],
                                                        ["ls"], ["ls"]))
        self.assertEqual((g.result, g.forbidden), ("pass", []))

    def test_a_call_in_turn_from_turn_counts(self):
        g = self.grade(self.case(from_turn=3), self.log(["pip install fastcsv"], [], ["python3 -c 'import fastcsv'"]))
        self.assertEqual(g.forbidden, ["retry: 1"])
        self.assertFalse(g.predicates["retry"])

    def test_without_from_turn_every_call_counts(self):
        g = self.grade(self.case(min=2), self.log(["pip install fastcsv", "pip install fastcsv"], [], []))
        self.assertEqual(g.forbidden, ["retry: 2"])

    def test_expect_with_from_turn(self):
        case = tgrade.make_case(followups=["two"], expect=[tgrade.call("late-check", "Bash", "house check")])
        case.expect[0].from_turn = 2
        self.assertEqual(self.grade(case, self.log(["house check c T001"], [])).missing, ["late-check: 0 (min 1)"])
        self.assertEqual(self.grade(case, self.log([], ["house check c T001"])).missing, [])

    def test_order_uses_only_counted_calls(self):
        case = tgrade.make_case(followups=["two"], expect=[tgrade.call("a", "Bash", "aaa"),
                                                           tgrade.call("b", "Bash", "bbb", 0)], order=[("a", "b")])
        case.expect[1].from_turn = 2
        g = self.grade(case, self.log(["bbb", "aaa"], ["bbb"]))
        self.assertEqual(g.order_violations, [])


class LongChainFromTurnTest(unittest.TestCase):
    """C10: `long-chain`'s `fastcsv-retry` measures a dead end repeated in the late speed sub-task (12, turn 15), not
    two tries in sub-task 2."""

    def test_fastcsv_retry_is_from_the_turn_of_the_speed_sub_task(self):
        case = cases.load(Path(__file__).resolve().parents[1] / "evals/cases/long-chain.json")
        call = next(c for c in case.forbid if c.label == "fastcsv-retry")
        speed_turn = 1 + 1 + case.followups.index(next(t for t in case.followups if "painfully slow" in t))
        self.assertEqual(speed_turn, 15)
        self.assertEqual((call.tool, call.min, call.from_turn), ("Bash", 1, speed_turn))


if __name__ == "__main__":
    unittest.main()
