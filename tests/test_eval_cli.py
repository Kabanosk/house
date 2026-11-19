"""`house eval run` and `house eval status`: refusals, selection, rows, exit codes (fake runner and grader)."""
import io
import json
import os
import signal
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from houselib import HOUSE_ROOT, agentdocker, cli, locks
from houselib.evals import results
from houselib.evals.grade import Grade
from houselib.evals.runner import RunResult
from tests import helpers
from tests.helpers import git

CASE = {"agent": "coder", "fixture": {"name": "small_build"}, "prompt": "do it"}


class FakeRoot:
    """Stands in for `sandbox.TempRoot`: no git export, no fixture."""
    entered: list = []
    log_path: Path | None = None
    fail_setup: list = []  # exceptions to raise on entry, one per entry

    def __init__(self, commit, fixture, *a, **kw):
        self.commit, self.fixture = commit, fixture

    tmp = Path("/tmp/house-eval-fake")
    exited: list = []

    def __enter__(self):
        FakeRoot.entered.append(self.commit)
        if FakeRoot.fail_setup:
            raise FakeRoot.fail_setup.pop(0)
        return self

    def __exit__(self, *exc):
        FakeRoot.exited.append(self.commit)
        return False

    def env(self):
        return {"HOUSE_EVAL_LOG": str(FakeRoot.log_path)}


class EvalCliBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.root = helpers.make_root(tmp)
        (self.root / "evals/cases").mkdir(parents=True)
        (self.root / "adapters/claude-code/skills/house-plan").mkdir(parents=True, exist_ok=True)
        (self.root / "adapters/claude-code/skills/house-plan/SKILL.md").write_text("x")
        self.write_case("alpha", kind="capability")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "init")
        self.sha = git(self.root, "rev-parse", "HEAD").strip()
        self.data = tmp / "data"
        self.data.mkdir()
        self.out_dirs: list[Path] = []
        self.runs: list[str] = []
        self.verdicts: dict[str, list[str]] = {}
        self.infra: dict[str, list[str]] = {}  # case -> run.infra texts handed out by the fake runner
        self.events: list[str] = []  # event names the fake agent run wrote to the house eval log
        self.sweeps = mock.Mock(return_value=[])
        self.cleanup_failed = False
        self.graded: list[str] = []  # what the fake runner reports about removing the container on a timeout
        FakeRoot.log_path = tmp / "eval.log"
        FakeRoot.fail_setup = []
        self.patches = [
            mock.patch.dict(os.environ, {"HOUSE_DATA_DIR": str(self.data)}),
            mock.patch.object(cli, "HOUSE_ROOT", self.root),
            mock.patch.object(cli.evalsandbox, "TempRoot", FakeRoot),
            mock.patch.object(cli.evalrunner, "run_case", side_effect=self.fake_run),
            mock.patch.object(cli.evalgrade, "grade", side_effect=self.fake_grade),
            mock.patch.object(results, "tool_version", return_value="cli-1.0"),
            mock.patch.object(cli.registry, "contests_in_progress", return_value=[]),
            mock.patch.object(cli.evalrunner, "sweep_stale", self.sweeps),
        ]
        for p in self.patches:
            p.start()
        FakeRoot.entered = []
        FakeRoot.exited = []

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self._tmp.cleanup()

    def write_case(self, name, **fields):
        (self.root / "evals/cases" / f"{name}.json").write_text(json.dumps({**CASE, **fields}))

    def fake_run(self, case, root, model=None, **kw):
        out = Path(self._tmp.name) / f"out-{len(self.out_dirs)}"
        out.mkdir()
        (out / "run.jsonl").write_text('{"type":"system","claude_code_version":"2.0.0","SECRET":"prompt text"}\n')
        self.out_dirs.append(out)
        self.runs.append(f"{case.name}:{model}")
        FakeRoot.log_path.write_text("".join(json.dumps({"time": "t", "event": e}) + "\n" for e in self.events))
        queue = self.infra.get(case.name)
        return RunResult(out / "run.jsonl", 0, 12.4, queue.pop(0) if queue else None, False, "sid", out,
                         cleanup_failed=self.cleanup_failed)

    def fake_grade(self, case, run, root):
        self.graded.append(case.name)
        queue = self.verdicts.get(case.name)
        result = queue.pop(0) if queue else "pass"
        grade = Grade(result, duration=run.duration, turns=3, subagent_turns=1,
                      tokens={"input": 1, "cache_read": 2, "cache_write": 3, "output": 4})
        if result == "fail":
            grade.missing, grade.failed_predicates = ["check: 0 (min 1)"], ["outcome[0] git_clean"]
        if result == "infra_error":
            grade.note = "docker: missing"
        return grade

    def call(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = cli.main(["eval", *argv])
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    @property
    def results_path(self):
        return self.root / "evals/results.jsonl"

    def rows(self):
        return results.read(self.results_path)


class RefusalTest(EvalCliBase):
    def test_refuses_when_night_lock_is_held(self):
        with locks.hold("night"):
            code, out, err = self.call("run")
        self.assertEqual(code, 1)
        self.assertIn("night", err)
        self.assertIn("--force", err)
        self.assertEqual(self.runs, [])

    def test_refuses_when_a_contest_is_in_progress(self):
        with mock.patch.object(cli.registry, "contests_in_progress", return_value=["live-one"]):
            code, out, err = self.call("run")
        self.assertEqual(code, 1)
        self.assertIn("live-one", err)
        self.assertEqual(self.runs, [])

    def test_empty_end_contest_does_not_refuse(self):
        # the real check treats a contest without timeline.end as not in progress (registry.in_progress)
        self.assertEqual(self.call("run")[0], 0)

    def test_force_bypasses_both_refusals(self):
        with locks.hold("night"), mock.patch.object(cli.registry, "contests_in_progress", return_value=["live"]):
            code, out, err = self.call("run", "--force")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.runs), 1)

    def test_eval_lock_busy_exits_1_even_with_force(self):
        with locks.hold("eval"):
            code, out, err = self.call("run", "--force")
        self.assertEqual(code, 1)
        self.assertIn("eval is already running", err)
        self.assertEqual(self.runs, [])

    def test_eval_lock_is_held_during_the_run_and_released(self):
        seen = []
        orig = self.fake_run

        def spy(*a, **kw):
            seen.append(locks.held("eval"))
            return orig(*a, **kw)

        with mock.patch.object(cli.evalrunner, "run_case", side_effect=spy):
            self.call("run")
        self.assertEqual(seen, [True])
        self.assertFalse(locks.held("eval"))


class SelectionTest(EvalCliBase):
    def test_unknown_name_exits_2(self):
        code, out, err = self.call("run", "--name", "nope")
        self.assertEqual(code, 2)
        self.assertIn("nope", err)
        self.assertEqual(self.runs, [])

    def test_tag_matching_nothing_exits_2(self):
        self.assertEqual(self.call("run", "--tag", "nothing")[0], 2)

    def test_unknown_agent_exits_2_before_any_run(self):
        self.write_case("bad", agent="no-such-agent")
        code, out, err = self.call("run")
        self.assertEqual(code, 2)
        self.assertIn("no-such-agent", err)
        self.assertEqual(self.runs, [])

    def test_coordinator_is_not_an_installable_agent_and_exits_2_up_front(self):
        self.write_case("coord", agent="coordinator")
        code, out, err = self.call("run", "--name", "coord")
        self.assertEqual(code, 2)
        self.assertIn("coordinator", err)
        self.assertIn("installable", err)
        self.assertEqual((self.runs, FakeRoot.entered), ([], []))
        self.assertEqual(self.rows(), [])

    def test_every_installable_profile_passes_the_preflight(self):
        from houselib import adapters
        names = sorted(set(adapters.load_profiles(self.root)) - {"coordinator"})
        self.assertIn("coder", names)
        for name in names:
            self.write_case("p-" + name, agent=name, **({"tool": "codex"} if name == "coder-codex" else {}))
        code, _, err = self.call("run", "--allow-codex", "--max-runs", "1")
        self.assertNotEqual(code, 2, err)

    def test_unknown_skill_exits_2_and_known_skill_is_fine(self):
        self.write_case("sk", agent=None, skill="no-skill", model="opus")
        code, _, err = self.call("run", "--name", "sk")
        self.assertEqual(code, 2)
        self.assertIn("no-skill", err)
        self.write_case("sk", agent=None, skill="house-plan", model="opus")
        self.assertEqual(self.call("run", "--name", "sk")[0], 0)

    def test_name_and_tag_select(self):
        self.write_case("beta", tags=["smoke"])
        self.assertEqual(self.call("run", "--tag", "smoke")[0], 0)
        self.assertEqual(self.runs, ["beta:None"])
        self.runs.clear()
        self.assertEqual(self.call("run", "--name", "alpha")[0], 0)
        self.assertEqual(self.runs, ["alpha:None"])

    def test_name_and_tag_together_are_a_usage_error(self):
        self.assertEqual(self.call("run", "--name", "alpha", "--tag", "x")[0], 2)

    def test_model_commit_and_repeat(self):
        code, out, _ = self.call("run", "--repeat", "3", "--model", "opus")
        self.assertEqual(code, 0)
        self.assertEqual(self.runs, ["alpha:opus"] * 3)
        self.assertEqual(FakeRoot.entered, [self.sha] * 3)  # HEAD resolved to a sha
        self.assertEqual(len(out.strip().splitlines()), 4)  # 3 runs and the summary
        FakeRoot.entered.clear()
        self.assertEqual(self.call("run", "--commit", self.sha[:8])[0], 0)
        self.assertEqual(FakeRoot.entered, [self.sha])

    def test_unknown_commit_is_an_error(self):
        code, _, err = self.call("run", "--commit", "no-such-ref")
        self.assertEqual(code, 1)
        self.assertIn("no-such-ref", err)
        self.assertEqual(self.runs, [])

    def test_max_runs_stops_across_cases_and_repeats(self):
        self.write_case("beta")
        code, out, _ = self.call("run", "--repeat", "2", "--max-runs", "3")
        self.assertEqual(code, 0)
        self.assertEqual(self.runs, ["alpha:None", "alpha:None", "beta:None"])
        self.assertEqual(len(self.rows()), 3)

    def test_max_runs_counts_infra_runs(self):
        self.verdicts["alpha"] = ["infra_error", "infra_error", "pass"]
        self.call("run", "--repeat", "3", "--max-runs", "2")
        self.assertEqual(len(self.runs), 2)


class RowsTest(EvalCliBase):
    def test_gitignore_lists_results_file(self):
        lines = (HOUSE_ROOT / ".gitignore").read_text().splitlines()
        self.assertIn("evals/results.jsonl", lines)

    def test_row_fields_and_no_session_content(self):
        self.verdicts["alpha"] = ["fail"]
        self.call("run")
        (row,) = self.rows()
        self.assertEqual(set(row), {
            "date", "commit", "cli", "model", "case", "case_hash", "kind", "result", "missing", "forbidden",
            "order_violations", "failed_predicates", "turns", "subagent_turns", "tokens", "duration", "note",
            "eval_events", "variant", "metrics", "score", "hidden", "predicates", "turns_sent", "turns_ok", "cohort",
            "audit"})
        self.assertEqual((row["case"], row["commit"], row["kind"], row["result"]), ("alpha", self.sha, "capability", "fail"))
        self.assertEqual(row["case_hash"], hashlib_sha(self.root / "evals/cases/alpha.json"))
        self.assertEqual(row["cli"], "2.0.0")  # from the log's init event
        self.assertEqual(row["missing"], ["check: 0 (min 1)"])
        self.assertEqual(row["failed_predicates"], ["outcome[0] git_clean"])
        self.assertEqual(row["tokens"]["output"], 4)
        self.assertEqual((row["turns"], row["subagent_turns"], row["duration"]), (3, 1, 12.4))
        self.assertTrue(row["date"].startswith("20"))
        text = self.results_path.read_text()
        self.assertNotIn("prompt text", text)
        self.assertNotIn("do it", text)

    def test_a_container_that_may_still_run_is_not_graded_and_its_root_is_kept(self):
        self.verdicts["alpha"] = ["pass"]
        self.cleanup_failed = True
        _, _, err = self.call("run")
        (row,) = self.rows()
        self.assertEqual(self.graded, [])
        self.assertEqual((row["result"], row["note"]), ("infra_error", "container not removed"))
        self.assertTrue(self.out_dirs[0].is_dir())
        self.assertEqual(FakeRoot.exited, [])  # the temp root is not cleaned up
        self.assertIn(str(self.out_dirs[0]), err)
        self.assertIn(str(FakeRoot.tmp), err)

    def test_note_and_event_counts(self):
        self.verdicts["alpha"] = ["infra_error", "pass"]
        self.events = ["signal.send", "notify.send", "signal.send"]
        self.call("run", "--repeat", "2")
        first, second = self.rows()
        self.assertEqual(first["note"], "docker: missing")  # the grader's short note
        self.assertEqual(second["note"], "")
        self.assertEqual(first["eval_events"], {"notify.send": 1, "signal.send": 2})
        self.events = []
        self.call("run")
        self.assertEqual(self.rows()[-1]["eval_events"], {})

    def test_model_column_is_the_override_else_the_cases_model(self):
        self.write_case("alpha", model="haiku")
        self.call("run")
        self.call("run", "--model", "opus")
        self.assertEqual([r["model"] for r in self.rows()], ["haiku", "opus"])

    def test_cli_version_falls_back_to_the_pinned_image_version(self):
        (Path(self._tmp.name) / "out-fallback").mkdir()
        with mock.patch.object(cli.evalrunner, "run_case") as rc:
            out = Path(self._tmp.name) / "out-fallback"
            (out / "run.jsonl").write_text('{"type":"system"}\n')
            rc.return_value = RunResult(out / "run.jsonl", 0, 1.0, None, False, "sid", out)
            self.call("run")
        self.assertEqual(self.rows()[0]["cli"], agentdocker.CLAUDE_VERSION)

    def test_out_dir_removed_after_each_run_even_when_grading_fails(self):
        self.call("run", "--repeat", "2")
        self.assertEqual(len(self.out_dirs), 2)
        self.assertFalse(any(d.exists() for d in self.out_dirs))
        with mock.patch.object(cli.evalgrade, "grade", side_effect=RuntimeError("boom")):
            code, _, err = self.call("run")
        self.assertEqual(code, 1)
        self.assertFalse(self.out_dirs[-1].exists())

    def test_rows_are_written_as_the_runs_finish(self):
        with mock.patch.object(cli.evalgrade, "grade", side_effect=[Grade("pass", duration=1.0), KeyboardInterrupt]):
            with self.assertRaises(KeyboardInterrupt):
                self.call("run", "--repeat", "2")
        self.assertEqual(len(self.rows()), 1)

    def test_line_per_run(self):
        self.verdicts["alpha"] = ["fail", "pass"]
        code, out, _ = self.call("run", "--repeat", "2")
        first, second = out.strip().splitlines()[:2]
        self.assertTrue(first.startswith("alpha 1/2 fail 12s"), first)
        self.assertIn("missing", first)
        self.assertIn("failed", first)
        self.assertTrue(second.startswith("alpha 2/2 pass 12s"), second)
        self.assertNotIn("missing", second)


class ExitCodeTest(EvalCliBase):
    def test_failing_regression_case_exits_1(self):
        self.write_case("reg", kind="regression")
        self.verdicts["reg"] = ["pass", "fail"]
        self.assertEqual(self.call("run", "--name", "reg", "--repeat", "2")[0], 1)

    def test_paused_regression_case_exits_1(self):
        self.write_case("reg", kind="regression")
        self.verdicts["reg"] = ["paused"]
        self.assertEqual(self.call("run", "--name", "reg")[0], 1)

    def test_failing_capability_case_exits_0(self):
        self.verdicts["alpha"] = ["fail"]
        self.assertEqual(self.call("run", "--name", "alpha")[0], 0)

    def test_passing_regression_case_exits_0(self):
        self.write_case("reg", kind="regression")
        self.assertEqual(self.call("run", "--name", "reg", "--repeat", "3")[0], 0)

    def test_infra_runs_do_not_count_against_a_regression_case(self):
        self.write_case("reg", kind="regression")
        self.verdicts["reg"] = ["infra_error", "pass", "pass"]
        self.assertEqual(self.call("run", "--name", "reg", "--repeat", "3")[0], 3)  # 2 counted of 3: not measured
        self.verdicts["reg"] = ["infra_error", "fail"]
        self.assertEqual(self.call("run", "--name", "reg", "--repeat", "2")[0], 1)  # a counted failure still fails

    def test_infra_is_reported_and_excluded_from_the_summary_counts(self):
        self.verdicts["alpha"] = ["infra_error", "pass"]
        code, out, _ = self.call("run", "--repeat", "2")
        self.assertIn("infra_error", out)
        self.assertIn("1/1", out.strip().splitlines()[-1])


class GateTest(EvalCliBase):
    """W2.3: a regression case that was not measured `--repeat` times is inconclusive (exit 3), never a quiet pass."""

    def setUp(self):
        super().setUp()
        self.write_case("reg", kind="regression")

    def test_only_infra_runs_is_inconclusive(self):
        self.verdicts["reg"] = ["infra_error"]
        code, out, err = self.call("run", "--name", "reg")
        self.assertEqual(code, 3)
        self.assertIn("inconclusive", err)
        self.assertIn("reg", err)
        self.assertIn("0 of 1", err)

    def test_fewer_counted_runs_than_repeat_is_inconclusive(self):
        self.verdicts["reg"] = ["pass", "infra_error", "pass"]
        code, out, err = self.call("run", "--name", "reg", "--repeat", "3")
        self.assertEqual(code, 3)
        self.assertIn("2 of 3", err)

    def test_max_runs_that_leaves_a_regression_case_short_is_inconclusive(self):
        self.assertEqual(self.call("run", "--name", "reg", "--repeat", "3", "--max-runs", "2")[0], 3)

    def test_a_regression_case_that_never_ran_is_inconclusive(self):
        self.write_case("alpha", kind="capability")
        code, _, err = self.call("run", "--max-runs", "1")  # alpha runs first, reg is not reached
        self.assertEqual(code, 3)
        self.assertIn("reg", err)

    def test_full_measurement_passes(self):
        self.assertEqual(self.call("run", "--name", "reg", "--repeat", "2")[0], 0)

    def test_a_failing_regression_case_wins_over_inconclusive(self):
        self.write_case("reg2", kind="regression")
        self.verdicts["reg"] = ["fail"]
        self.verdicts["reg2"] = ["infra_error"]
        code, out, err = self.call("run")
        self.assertEqual(code, 1)
        self.assertIn("not pass^k: reg", err)
        self.assertIn("inconclusive", err)  # still reported

    def test_capability_cases_never_gate(self):
        self.verdicts["alpha"] = ["infra_error"]
        self.assertEqual(self.call("run", "--name", "alpha")[0], 0)

    def test_infra_runs_print_a_warning(self):
        self.verdicts["alpha"] = ["infra_error", "pass"]
        code, out, err = self.call("run", "--name", "alpha", "--repeat", "2")
        self.assertEqual(code, 0)
        self.assertIn("warning", err)
        self.assertIn("alpha", err)
        self.assertIn("1 infra_error", err)
        code, out, err = self.call("run", "--name", "alpha")
        self.assertNotIn("infra_error run", err)

    def test_setup_problems_are_failures_not_infra(self):
        for infra in ("profile not installed: .claude/agents/coder.md is missing in the contest",
                      "model: unknown agent profile 'x'"):
            with self.subTest(infra=infra):
                self.infra["reg"] = [infra]
                self.runs.clear()
                code, out, err = self.call("run", "--name", "reg")
                row = self.rows()[-1]
                self.assertEqual((row["result"], code), ("fail", 1))
                self.assertTrue(row["note"].startswith("setup: "), row["note"])
                self.assertIn(infra[:20], row["note"])
                self.assertIn("setup", out)
                self.assertNotIn("infra_error run", err)

    def test_docker_and_token_problems_stay_infra(self):
        self.infra["reg"] = ["docker: Docker is not usable"]
        self.verdicts["reg"] = ["infra_error"]
        self.call("run", "--name", "reg")
        self.assertEqual(self.rows()[-1]["result"], "infra_error")

    def test_a_temp_root_setup_error_is_a_failed_row_and_the_batch_goes_on(self):
        self.write_case("beta", kind="capability")
        FakeRoot.fail_setup = [RuntimeError("git archive abc failed: boom"), subprocess.SubprocessError("x")]
        code, out, err = self.call("run", "--repeat", "2", "--name", "reg")
        self.assertEqual(self.runs, [], "no agent started")
        rows = self.rows()
        self.assertEqual([r["result"] for r in rows], ["fail", "fail"])
        self.assertTrue(rows[0]["note"].startswith("setup: "))
        self.assertIn("git archive", rows[0]["note"])
        self.assertEqual(code, 1)
        self.assertEqual(len(out.strip().splitlines()), 3)  # two lines and the summary: it did not abort
        FakeRoot.fail_setup = [OSError("disk full")]
        code, out, err = self.call("run", "--name", "beta", "--repeat", "2")
        self.assertEqual([r["result"] for r in self.rows()[-2:]], ["fail", "pass"])  # next run is unaffected
        self.assertEqual(self.runs, ["beta:None"])

    def test_a_setup_error_row_has_the_row_shape(self):
        FakeRoot.fail_setup = [RuntimeError("boom")]
        self.call("run", "--name", "reg")
        (row,) = self.rows()
        self.assertEqual(row["eval_events"], {})
        self.assertEqual((row["turns"], row["duration"]), (0, 0.0))
        self.assertEqual(row["case"], "reg")


class LockOrderTest(EvalCliBase):
    """W2.4: `eval.lock` first, then the night and contest checks (they cannot change under a held lock)."""

    def test_the_checks_run_under_the_eval_lock(self):
        seen = []
        real_held = locks.held

        def held_spy(name):
            seen.append((name, real_held("eval")))
            return real_held(name)

        def contests_spy(*a, **kw):
            seen.append(("contests", real_held("eval")))
            return []

        with mock.patch.object(cli.locks, "held", held_spy), \
                mock.patch.object(cli.registry, "contests_in_progress", contests_spy):
            self.assertEqual(self.call("run")[0], 0)
        self.assertEqual(seen, [("night", True), ("contests", True)])

    def test_a_refusal_releases_the_eval_lock(self):
        with locks.hold("night"):
            self.assertEqual(self.call("run")[0], 1)
        self.assertFalse(locks.held("eval"))

    def test_eval_lock_busy_wins_before_any_check(self):
        with locks.hold("eval"), mock.patch.object(cli.registry, "contests_in_progress") as c:
            self.assertEqual(self.call("run")[0], 1)
        c.assert_not_called()

    def test_night_does_not_look_at_the_eval_lock(self):
        import inspect
        self.assertNotIn('"eval"', inspect.getsource(cli.cmd_night).replace("evals", ""))
        self.assertIn("eval.lock", cli.cmd_night.__doc__)  # documented as deliberate


class CodexGateTest(EvalCliBase):
    """W2.1: Codex reads the disk under workspace-write, so Codex cases need `--allow-codex`."""

    def setUp(self):
        super().setUp()
        self.write_case("cx", agent="coder-codex", tool="codex", kind="capability")

    def test_skipped_with_a_clear_line_when_selected_by_default(self):
        code, out, err = self.call("run")
        self.assertEqual(code, 0)
        self.assertEqual(self.runs, ["alpha:None"])
        self.assertIn("skipped cx", err)
        self.assertIn("--allow-codex", err)

    def test_named_codex_case_without_the_flag_exits_2(self):
        code, out, err = self.call("run", "--name", "cx")
        self.assertEqual(code, 2)
        self.assertIn("--allow-codex", err)
        self.assertEqual(self.runs, [])

    def test_the_flag_runs_it(self):
        code, out, err = self.call("run", "--allow-codex")
        self.assertEqual(code, 0)
        self.assertEqual(self.runs, ["alpha:None", "cx:None"])
        self.assertNotIn("skipped", err)
        self.assertEqual(self.call("run", "--name", "cx", "--allow-codex")[0], 0)

    def test_a_skipped_regression_case_is_inconclusive(self):
        self.write_case("cxr", agent="coder-codex", tool="codex", kind="regression")
        self.assertEqual(self.call("run", "--name", "alpha")[0], 0)
        code, out, err = self.call("run")
        self.assertEqual(code, 3)
        self.assertIn("skipped cxr", err)
        self.assertIn("inconclusive", err)
        self.assertIn("cxr", err.split("inconclusive", 1)[1])

    def test_a_failing_regression_case_still_wins_over_a_skipped_one(self):
        self.write_case("cxr", agent="coder-codex", tool="codex", kind="regression")
        self.write_case("alpha", kind="regression")
        self.verdicts["alpha"] = ["fail"]
        self.assertEqual(self.call("run")[0], 1)

    def test_a_skipped_capability_case_is_not_inconclusive(self):
        self.assertEqual(self.call("run")[0], 0)


class UpFrontTest(EvalCliBase):
    """W2.7 and the signal and sweep wiring of W2.2."""

    def test_unknown_fixture_exits_2_before_any_run(self):
        self.write_case("badfx", fixture={"name": "no_such_fixture"})
        code, out, err = self.call("run")
        self.assertEqual(code, 2)
        self.assertIn("badfx", err)
        self.assertIn("no_such_fixture", err)
        self.assertIn("small_build", err)  # names the known ones
        self.assertEqual(self.runs, [])
        self.sweeps.assert_not_called()

    def test_dirty_tree_warns_but_runs(self):
        (self.root / "core/dirty.txt").write_text("x")
        code, out, err = self.call("run")
        self.assertEqual(code, 0)
        self.assertIn("uncommitted changes are not tested", err)
        self.assertIn("--commit", err)
        self.assertEqual(len(self.runs), 1)

    def test_clean_tree_and_changes_outside_the_code_dirs_do_not_warn(self):
        self.assertNotIn("uncommitted", self.call("run")[2])
        (self.root / "notes.txt").write_text("x")
        (self.root / "evals/cases/alpha.json").write_text(json.dumps({**CASE, "kind": "capability", "prompt": "new"}))
        self.assertNotIn("uncommitted", self.call("run")[2])

    def test_sweep_runs_once_under_the_lock_before_the_first_run(self):
        order = []
        self.sweeps.side_effect = lambda: order.append(("sweep", locks.held("eval"))) or ["removed stale container x"]
        orig = self.fake_run
        with mock.patch.object(cli.evalrunner, "run_case",
                               side_effect=lambda *a, **kw: order.append(("run", None)) or orig(*a, **kw)):
            code, out, err = self.call("run", "--repeat", "2")
        self.assertEqual(order, [("sweep", True), ("run", None), ("run", None)])
        self.assertIn("removed stale container x", err)

    def test_sweep_is_skipped_when_the_lock_is_busy(self):
        with locks.hold("eval"):
            self.call("run")
        self.sweeps.assert_not_called()

    def test_term_and_hup_are_interrupts_only_during_the_run(self):
        seen = {}
        before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP)}
        orig = self.fake_run

        def spy(*a, **kw):
            seen.update({s: signal.getsignal(s) for s in before})
            return orig(*a, **kw)

        with mock.patch.object(cli.evalrunner, "run_case", side_effect=spy):
            self.call("run")
        for sig, handler in seen.items():
            self.assertNotEqual(handler, before[sig])
        self.assertEqual({s: signal.getsignal(s) for s in before}, before)

    def test_sigterm_during_a_run_unwinds_with_cleanup_and_releases_the_lock(self):
        def killed(*a, **kw):
            os.kill(os.getpid(), signal.SIGTERM)
            import time
            time.sleep(1)

        with mock.patch.object(cli.evalrunner, "run_case", side_effect=killed), self.assertRaises(KeyboardInterrupt):
            self.call("run")
        self.assertFalse(locks.held("eval"))

    def test_out_dir_removal_goes_through_remove_tree(self):
        with mock.patch.object(cli.evalsandbox, "remove_tree") as rm:
            self.call("run")
        rm.assert_called_once_with(self.out_dirs[0])


class StatusTest(EvalCliBase):
    def put(self, case, digest, *outcomes, kind="capability", commit="a1b2c3d4e5f6", model="sonnet"):
        for i, r in enumerate(outcomes):
            results.append(self.results_path, {"case": case, "case_hash": digest, "kind": kind, "result": r,
                                               "commit": commit, "model": model,
                                               "date": f"2026-11-17T10:00:{i:02d}+01:00"})

    def test_empty_results(self):
        code, out, _ = self.call("status")
        self.assertEqual(code, 0)
        self.assertIn("no results", out)

    def test_latest_hash_only_unless_all_hashes(self):
        self.put("a", "oldhash11", "fail", "fail")
        self.put("a", "newhash22", "pass", "pass", "pass", kind="regression")
        code, out, _ = self.call("status")
        self.assertEqual(code, 0)
        self.assertIn("newhash2", out)
        self.assertNotIn("oldhash1", out)
        self.assertIn("regression", out)
        code, out, _ = self.call("status", "--all-hashes")
        self.assertIn("oldhash1", out)
        self.assertIn("newhash2", out)

    def test_all_hashes_shows_old_hashes_at_their_own_latest_commit(self):
        self.put("a", "oldhash11", "fail", commit="1111111111aa")
        self.put("a", "oldhash11", "pass", commit="2222222222bb")
        self.put("a", "newhash22", "pass", commit="3333333333cc")
        out = self.call("status", "--all-hashes")[1]
        self.assertIn("oldhash1", out)
        self.assertIn("2222222", out)
        self.assertNotIn("1111111", out)  # not every commit: that is --all
        self.assertIn("newhash2", out)
        self.assertNotIn("oldhash1", self.call("status")[1])

    def test_flaky_and_zero_flags_and_pass_k(self):
        self.put("flaky-case", "h1", "pass", "fail", "pass")
        self.put("broken", "h2", "fail", "fail")
        self.put("solid", "h3", "pass", "pass", "pass", "infra_error")
        code, out, _ = self.call("status")
        lines = {ln.split()[0]: ln for ln in out.splitlines()}
        self.assertIn("flaky", lines["flaky-case"])
        self.assertNotIn("0/n", lines["flaky-case"])
        self.assertIn("0/2", lines["broken"])
        self.assertNotIn("flaky", lines["broken"])
        self.assertNotIn("flaky", lines["solid"])
        self.assertIn("yes", lines["solid"])  # pass^3
        self.assertIn("100%", lines["solid"])
        self.assertIn("67%", lines["flaky-case"])

    def test_name_filter_and_k(self):
        self.put("a", "h1", "pass", "pass")
        self.put("b", "h2", "fail")
        code, out, _ = self.call("status", "--name", "a", "--k", "2")
        self.assertIn("a ", out)
        self.assertNotIn("\nb ", "\n" + out)
        self.assertIn("pass^2", out)

    def test_default_shows_each_cases_latest_commit_with_the_short_commit_and_model(self):
        self.put("a", "h", "pass", "pass", commit="1111111111aa")
        self.put("a", "h", "fail", commit="2222222222bb")
        self.put("b", "h", "pass", commit="3333333333cc")
        code, out, _ = self.call("status")
        lines = {ln.split()[0]: ln for ln in out.splitlines()}
        self.assertEqual(len([ln for ln in out.splitlines() if ln.startswith("a ")]), 1)
        self.assertIn("2222222", lines["a"])
        self.assertNotIn("1111111", out)
        self.assertIn("3333333", lines["b"])
        self.assertNotIn("2222222222bb", out)  # the short form
        self.assertIn("sonnet", lines["a"])
        self.assertIn("last", out.splitlines()[0])

    def test_all_shows_every_commit_and_hash(self):
        self.put("a", "old", "pass", commit="1111111111aa")
        self.put("a", "new", "pass", commit="2222222222bb")
        out = self.call("status", "--all")[1]
        self.assertIn("1111111", out)
        self.assertIn("2222222", out)
        self.assertIn("old", out)

    def test_commit_filter_takes_a_prefix(self):
        self.put("a", "h", "pass", commit="1111111111aa")
        self.put("a", "h", "fail", commit="2222222222bb")
        out = self.call("status", "--commit", "11111")[1]
        self.assertIn("1111111", out)
        self.assertNotIn("2222222", out)
        self.assertIn("no results for that selection", self.call("status", "--commit", "9999")[1])

    def test_model_filter_and_models_are_separate_lines(self):
        self.put("a", "h", "pass", model="sonnet")
        self.put("a", "h", "fail", model="opus")
        out = self.call("status")[1]
        self.assertIn("sonnet", out)
        self.assertIn("opus", out)
        only = self.call("status", "--model", "opus")[1]
        self.assertIn("opus", only)
        self.assertNotIn("sonnet", only)

    def test_streak_column(self):
        self.put("a", "h", "fail", "pass", "infra_error", "pass")
        out = self.call("status")[1]
        header = out.splitlines()[0].split()
        row = [ln for ln in out.splitlines() if ln.startswith("a ")][0].split()
        self.assertIn("streak", header)
        self.assertEqual(row[header.index("streak")], "2")

    def test_status_does_not_take_the_lock_or_refuse(self):
        self.put("a", "h", "pass")
        with locks.hold("night"), locks.hold("eval"):
            self.assertEqual(self.call("status")[0], 0)


def hashlib_sha(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


if __name__ == "__main__":
    unittest.main()
