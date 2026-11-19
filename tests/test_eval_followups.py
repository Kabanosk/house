"""Follow-up user turns, hidden tests in a second container and the per-predicate score (K5). Fakes only."""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from houselib import agentdocker as dk
from houselib.evals import cases, fixtures, grade, results, runner
from houselib.evals.cases import Variant
from houselib.evals.runner import RunResult
from tests import test_eval_cli as tcli
from tests import test_eval_grade as tgrade
from tests import test_eval_runner as trun
from tests import test_eval_variants as tvar
from tests.test_agentdocker import FakeDocker

HIDDEN = {"type": "hidden_tests", "dir": "chain", "min_passed": 2,
          "command": ["python3", "-m", "unittest", "discover", "-s", "{hidden}", "-t", "{hidden}"]}
UNITTEST_OK = "..\n" + "-" * 70 + "\nRan 3 tests in 0.002s\n\nOK\n"
UNITTEST_FAILED = "F.E\n" + "-" * 70 + "\nRan 5 tests in 0.1s\n\nFAILED (failures=1, errors=1)\n"


def case_json(**extra) -> dict:
    base = {"agent": "coder", "model": "haiku", "fixture": {"name": "small_build"}, "prompt": "go"}
    return {**base, **extra}


def load(data: dict) -> cases.Case:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "c.json"
        path.write_text(json.dumps(data))
        return cases.load(path)


class LoadTest(unittest.TestCase):
    def test_followups_default_to_none(self):
        self.assertEqual(load(case_json()).followups, [])

    def test_followups_are_strings(self):
        self.assertEqual(load(case_json(followups=["one", "two"])).followups, ["one", "two"])

    def test_followups_must_be_a_list_of_non_empty_strings(self):
        for bad in ("one", [1], [""], ["ok", "  "], {"a": "b"}):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "c.json: followups"):
                load(case_json(followups=bad))

    def test_followups_are_rejected_for_codex(self):
        with self.assertRaisesRegex(ValueError, "c.json: followups.*codex"):
            load(case_json(agent="coder-codex", model=None, tool="codex", followups=["x"]))

    def test_hidden_tests_are_rejected_for_codex(self):
        with self.assertRaisesRegex(ValueError, r"c.json: outcome\[1\].*hidden_tests.*codex"):
            load(case_json(agent="coder-codex", model=None, tool="codex",
                           outcome=[{"type": "git_clean"}, dict(HIDDEN)]))

    def test_hidden_tests_predicate(self):
        case = load(case_json(outcome=[{**HIDDEN, "timeout_seconds": 60}]))
        self.assertEqual(case.outcome[0]["min_passed"], 2)
        self.assertIn("hidden_tests", cases.PREDICATES)

    def test_hidden_tests_params_are_checked(self):
        bad = [{k: v for k, v in HIDDEN.items() if k != "command"}, {k: v for k, v in HIDDEN.items() if k != "dir"},
               {k: v for k, v in HIDDEN.items() if k != "min_passed"}, {**HIDDEN, "command": []},
               {**HIDDEN, "command": "python3"}, {**HIDDEN, "command": ["python3", 3]},
               {**HIDDEN, "min_passed": 0}, {**HIDDEN, "min_passed": "2"}, {**HIDDEN, "timeout_seconds": 0},
               {**HIDDEN, "dir": "../x"}, {**HIDDEN, "dir": "/abs"}, {**HIDDEN, "dir": "."}, {**HIDDEN, "extra": 1}]
        for pred in bad:
            with self.subTest(pred=pred), self.assertRaisesRegex(ValueError, r"c.json: outcome\[0\]"):
                load(case_json(outcome=[pred]))


class SummaryTest(unittest.TestCase):
    def test_unittest_ok(self):
        self.assertEqual(runner.parse_test_summary(UNITTEST_OK), (3, 0))

    def test_unittest_failed(self):
        self.assertEqual(runner.parse_test_summary(UNITTEST_FAILED), (3, 2))

    def test_unittest_skipped_is_not_passed(self):
        self.assertEqual(runner.parse_test_summary("Ran 4 tests in 0.1s\n\nOK (skipped=1)\n"), (3, 0))
        self.assertEqual(runner.parse_test_summary("Ran 6 tests in 0.1s\n\nFAILED (failures=2, skipped=1)\n"), (3, 2))

    def test_unittest_expected_failures_pass_and_unexpected_successes_fail(self):
        self.assertEqual(runner.parse_test_summary("Ran 4 tests in 0s\n\nOK (expected failures=1)\n"), (4, 0))
        self.assertEqual(runner.parse_test_summary(
            "Ran 4 tests in 0s\n\nFAILED (unexpected successes=1)\n"), (3, 1))

    def test_one_test_is_singular(self):
        self.assertEqual(runner.parse_test_summary("Ran 1 test in 0.0s\n\nOK\n"), (1, 0))

    def test_pytest_summary(self):
        text = "collected 6 items\n=== 3 passed, 2 failed, 1 skipped in 0.12s ===\n"
        self.assertEqual(runner.parse_test_summary(text), (3, 2))
        self.assertEqual(runner.parse_test_summary("== 4 passed in 0.01s ==\n"), (4, 0))
        self.assertEqual(runner.parse_test_summary("== 1 failed, 1 error in 1s ==\n"), (0, 2))

    def test_no_summary_is_zero(self):
        for text in ("", "Traceback (most recent call last):\n  boom\n", "OK\n"):
            self.assertEqual(runner.parse_test_summary(text), (0, 0))

    def test_the_last_summary_wins(self):
        text = "Ran 99 tests in 0s\n\nOK\n" + UNITTEST_FAILED
        self.assertEqual(runner.parse_test_summary(text), (3, 2))


def result_line(subtype="success", text="ok") -> str:
    return json.dumps({"type": "result", "subtype": subtype, "is_error": subtype != "success", "result": text}) + "\n"


class Stdin:
    """A child's stdin that behaves like a CLI answering each user line with a `result` event, a moment later.

    `received` are the lines taken so far, `emitted` the results written to the child's stdout so far; a line that
    arrives before the result of the previous one was emitted is a `violation` (the CLI would fold it into one turn).
    """

    def __init__(self, stdout, results, silent=False, split=False):
        self.stdout, self.results, self.silent, self.split = stdout, list(results), silent, split
        self.received, self.emitted, self.violations = [], [], []
        self.closed = threading.Event()
        self.lock = threading.Lock()

    def write(self, data):
        if self.closed.is_set():
            raise ValueError("write to closed file")
        with self.lock:
            if len(self.emitted) != len(self.received):
                self.violations.append(len(self.received))
            self.received.append(data.decode())
            k = len(self.received)
        threading.Timer(0.05, self.answer, args=(k,)).start()
        return len(data)

    def answer(self, k):
        if self.silent:
            return
        text = self.results[k - 1] if k <= len(self.results) else result_line()
        with self.lock:
            if self.split:  # the result arrives in two pieces, a moment apart
                self.stdout.write(text[:10])
                self.stdout.flush()
                self.lock.release()
                threading.Event().wait(0.15)
                self.lock.acquire()
                text = text[10:]
            self.stdout.write(text)
            self.stdout.flush()
            self.emitted.append(k)

    def flush(self):
        pass

    def close(self):
        self.closed.set()

    def lines(self) -> list[dict]:
        return [json.loads(line) for line in self.received]


class StdinProc(trun.FakeProc):
    """Waits until its stdin is closed (like the CLI after its last turn), or hangs when told to."""

    def __init__(self, argv, kw, spec):
        super().__init__(argv, kw, spec)
        self.stdin = Stdin(kw["stdout"], spec.get("results", []), spec.get("silent", False),
                                spec.get("split", False)) if kw.get("stdin") == subprocess.PIPE else None

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.spec.get("hang"):
            self.spec["hang"] = False
            raise subprocess.TimeoutExpired(self.argv, timeout)
        if self.stdin is not None:
            self.stdin.closed.wait(5)
        self.returncode = self.spec.get("code", 0)
        return self.returncode


class StdinPopens(trun.Popens):
    def __call__(self, argv, **kw):
        proc = StdinProc(argv, kw, self.spec)
        self.procs.append(proc)
        return proc


class FollowupRunTest(trun.RunnerCase):
    def run_turns(self, case, **spec):
        result, popens, _, _ = self.go(case, StdinPopens(**spec))
        return result, popens.procs[0]

    def test_each_turn_is_sent_only_after_the_result_of_the_previous_one(self):
        case = trun.make_case(followups=["second ünï", "third"])
        result, proc = self.run_turns(case)
        self.assertIsNone(result.infra)
        self.assertEqual([m["message"]["content"] for m in proc.stdin.lines()], ["do the thing", "second ünï", "third"])
        self.assertEqual(proc.stdin.lines()[0], {"type": "user", "message": {"role": "user", "content": "do the thing"}})
        self.assertEqual(proc.stdin.violations, [])
        self.assertEqual(proc.stdin.emitted, [1, 2, 3])
        self.assertTrue(proc.stdin.closed.is_set())  # closed after the last result
        self.assertEqual(proc.kw["stdin"], subprocess.PIPE)

    def test_a_result_that_is_not_a_success_stops_the_feed(self):
        case = trun.make_case(followups=["second", "third"])
        result, proc = self.run_turns(case, results=[result_line(), result_line("error_max_turns")])
        self.assertEqual([m["message"]["content"] for m in proc.stdin.lines()], ["do the thing", "second"])
        self.assertTrue(proc.stdin.closed.is_set())

    def test_an_error_result_stops_the_feed_after_the_first_turn(self):
        result, proc = self.run_turns(trun.make_case(followups=["second"]), results=[result_line("error_during_execution")])
        self.assertEqual(len(proc.stdin.received), 1)
        self.assertTrue(proc.stdin.closed.is_set())

    def test_a_silent_child_still_times_out_and_gets_nothing_more(self):
        case = trun.make_case(followups=["second"])
        result, popens, docker, _ = self.go(case, StdinPopens(hang=True, silent=True))
        proc = popens.procs[0]
        self.assertTrue(result.timed_out)
        self.assertEqual(len(proc.stdin.received), 1)  # the follow-up was never sent
        self.assertTrue(proc.stdin.closed.is_set())  # the feeder is done: nothing outlives the run
        self.assertIn(["docker", "rm", "-f", trun.option(proc.argv, "--name")], docker.calls)

    def test_argv_uses_stream_json_input_and_no_prompt(self):
        case = trun.make_case(followups=["more"])
        _, proc = self.run_turns(case)
        argv = proc.argv
        self.assertEqual(trun.option(argv, "--input-format"), "stream-json")
        self.assertEqual(trun.option(argv, "--output-format"), "stream-json")
        self.assertEqual(trun.option(argv, "--max-turns"), "7")
        self.assertNotIn("do the thing", argv)
        self.assertNotIn("--", argv[argv.index("claude"):])

    def test_skill_prompt_keeps_its_slash_command(self):
        case = trun.make_case(agent=None, skill="house-plan", followups=["more"])
        _, proc = self.run_turns(case)
        self.assertEqual(proc.stdin.lines()[0]["message"]["content"], "/house-plan do the thing")

    def test_variant_prompt_is_the_first_turn(self):
        self.root.commit_patch = lambda: None
        case = trun.make_case(followups=["more"])
        _, popens, _, _ = self.go(case, StdinPopens(), variant=Variant("v", prompt="variant prompt"))
        self.assertEqual([m["message"]["content"] for m in popens.procs[0].stdin.lines()], ["variant prompt", "more"])

    def test_without_followups_the_prompt_is_a_one_line_feed(self):
        _, proc = self.run_turns(trun.make_case())
        self.assertEqual(proc.kw["stdin"], subprocess.PIPE)
        self.assertEqual(trun.option(proc.argv, "--input-format"), "stream-json")
        self.assertEqual([m["message"]["content"] for m in proc.stdin.lines()], ["do the thing"])
        self.assertTrue(proc.stdin.closed.is_set())

    def test_a_broken_stdin_pipe_does_not_break_the_run(self):
        class BrokenPopens(StdinPopens):
            def __call__(self, argv, **kw):
                proc = super().__call__(argv, **kw)

                def broken(data):
                    raise BrokenPipeError

                proc.stdin.write = broken
                return proc

        result, _, _, _ = self.go(trun.make_case(followups=["x"]), BrokenPopens())
        self.assertIsNone(result.infra)

    def test_a_result_split_over_two_reads_is_still_seen(self):
        _, proc = self.run_turns(trun.make_case(followups=["second"]), split=True)
        self.assertEqual(len(proc.stdin.received), 2)
        self.assertEqual(proc.stdin.violations, [])

    def test_agent_container_cannot_see_the_hidden_dir(self):
        _, popens, _, _ = self.go(trun.make_case(), trun.Popens(stdout=trun.ASSISTANT + "\n"))
        argv = popens.procs[0].argv
        mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
        self.assertIn(f"{self.tmpdir}:{self.tmpdir}", mounts)  # the temp dir is mounted read-write ...
        masks = [argv[i + 1] for i, a in enumerate(argv) if a == "--tmpfs"]
        hidden = str(self.tmpdir / "hidden")
        self.assertTrue(any(m.startswith(hidden + ":") for m in masks), masks)  # ... with the hidden dir masked
        self.assertFalse(any(hidden in m for m in mounts))


class HiddenPopens(trun.Popens):
    """The first process is the agent, every later one a hidden-tests container with its own spec."""

    def __init__(self, hidden=(), **spec):
        super().__init__(**spec)
        self.hidden = list(hidden)

    def __call__(self, argv, **kw):
        spec = self.spec if not self.procs else (self.hidden.pop(0) if self.hidden else {})
        proc = trun.FakeProc(argv, kw, spec)
        self.procs.append(proc)
        return proc


class HiddenRunTest(trun.RunnerCase):
    def setUp(self):
        super().setUp()
        (self.tmpdir / "hidden/chain").mkdir(parents=True)
        (self.tmpdir / "hidden/chain/test_a.py").write_text("# hidden\n")
        self.case = trun.make_case(outcome=[dict(HIDDEN)])

    def run_hidden(self, hidden=({"stderr": UNITTEST_OK},), case=None, **kw):
        return self.go(case or self.case, HiddenPopens(hidden=hidden, stdout=trun.ASSISTANT + "\n"), **kw)

    def test_container_argv(self):
        result, popens, _, _ = self.run_hidden()
        self.assertEqual(len(popens.procs), 2)
        argv = popens.procs[1].argv
        self.assertEqual(argv[:2], ["docker", "run"])
        self.assertEqual(trun.option(argv, "--network"), "none")
        self.assertNotIn(dk.INTERNAL_NET, argv)
        self.assertNotIn("--env-file", argv)
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", " ".join(argv))
        self.assertNotIn("SECRET", " ".join(argv))
        for flag in ("--read-only", "--cap-drop", "no-new-privileges", "--memory", "--pids-limit", "--init"):
            self.assertIn(flag, argv)
        contest, hidden = str(self.root.contest), str(self.tmpdir / "hidden/chain")
        mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
        self.assertEqual(sorted(mounts), sorted([f"{contest}:{contest}:ro", f"{hidden}:{hidden}:ro"]))
        self.assertEqual(trun.option(argv, "-w"), contest)
        envs = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", envs)
        self.assertIn("HOME=/home/agent", envs)
        self.assertIn("TMPDIR=/tmp", envs)
        self.assertTrue(all(not e.startswith("HOUSE_") for e in envs))
        image = argv.index(dk.image_name())
        self.assertEqual(argv[image + 1:image + 5], ["timeout", "-k", "10", "330"])
        self.assertEqual(argv[image + 5:], ["python3", "-m", "unittest", "discover", "-s", hidden, "-t", hidden])

    def test_it_starts_after_the_agent_and_in_its_own_container(self):
        result, popens, _, _ = self.run_hidden()
        names = [trun.option(p.argv, "--name") for p in popens.procs]
        self.assertEqual(names[0], runner.CONTAINER_PREFIX + result.session_id)
        self.assertTrue(names[1].startswith(runner.CONTAINER_PREFIX + "hidden-"))
        self.assertNotEqual(names[0], names[1])
        self.assertEqual(popens.procs[1].kw["stdin"], subprocess.DEVNULL)

    def test_timeout_param_sets_both_timeouts(self):
        case = trun.make_case(outcome=[{**HIDDEN, "timeout_seconds": 50}])
        _, popens, _, _ = self.run_hidden(case=case)
        argv = popens.procs[1].argv
        self.assertEqual(argv[argv.index(dk.image_name()) + 4], "80")
        self.assertEqual(popens.procs[1].waits, [50])

    def test_counts_are_recorded_per_predicate_index(self):
        case = trun.make_case(outcome=[{"type": "git_clean"}, dict(HIDDEN), dict(HIDDEN)])
        result, popens, _, _ = self.run_hidden(({"stderr": UNITTEST_OK}, {"stderr": UNITTEST_FAILED, "code": 1}),
                                               case=case)
        self.assertIsNone(result.infra)
        self.assertEqual(result.hidden, {1: {"exit": 0, "passed": 3, "failed": 0},
                                         2: {"exit": 1, "passed": 3, "failed": 2}})

    def test_summary_on_stdout_is_read_too(self):
        result, _, _, _ = self.run_hidden(({"stdout": "== 4 passed in 0.1s ==\n"},))
        self.assertEqual(result.hidden[0], {"exit": 0, "passed": 4, "failed": 0})

    def test_no_hidden_predicate_starts_no_second_container(self):
        result, popens, _, _ = self.go(trun.make_case(outcome=[{"type": "git_clean"}]),
                                       HiddenPopens(stdout=trun.ASSISTANT + "\n"))
        self.assertEqual(len(popens.procs), 1)
        self.assertEqual(result.hidden, {})

    def test_timeout_is_exit_none_and_removes_the_container(self):
        result, popens, docker, killpg = self.run_hidden(({"hang": True},))
        self.assertIsNone(result.infra)
        self.assertEqual(result.hidden[0], {"exit": None, "passed": 0, "failed": 0})
        name = trun.option(popens.procs[1].argv, "--name")
        self.assertIn(["docker", "rm", "-f", name], docker.calls)
        killpg.assert_called()

    def test_the_in_container_timeout_is_exit_none_too(self):
        for code in (124, 137):
            with self.subTest(code=code):
                result, _, _, _ = self.run_hidden(({"code": code, "stderr": UNITTEST_FAILED},))
                self.assertEqual(result.hidden[0], {"exit": None, "passed": 0, "failed": 0})

    def test_docker_failure_is_infra(self):
        for code in (125, 126, 127):
            with self.subTest(code=code):
                result, _, _, _ = self.run_hidden(({"code": code, "stderr": "docker: no such image\n"},))
                self.assertIn("hidden tests", result.infra)
                self.assertFalse(runner.is_setup_error(result.infra))

    def test_cannot_start_docker_is_infra(self):
        class Fails(HiddenPopens):
            def __call__(self, argv, **kw):
                if self.procs:
                    raise OSError(2, "No such file or directory")
                return super().__call__(argv, **kw)

        result, _, _, _ = self.go(self.case, Fails(stdout=trun.ASSISTANT + "\n"))
        self.assertIn("hidden tests: cannot start", result.infra)

    def test_missing_hidden_dir_is_infra_and_nothing_starts(self):
        case = trun.make_case(outcome=[{**HIDDEN, "dir": "absent"}])
        result, popens, _, _ = self.run_hidden(case=case)
        self.assertEqual(len(popens.procs), 1)
        self.assertIn("hidden tests: dir 'absent'", result.infra)

    def test_hidden_dir_escaping_the_hidden_root_is_infra(self):
        (self.tmpdir / "hidden/link").symlink_to(self.tmpdir / "data", target_is_directory=True)
        (self.tmpdir / "data").mkdir()
        case = trun.make_case(outcome=[{**HIDDEN, "dir": "link"}])
        result, popens, _, _ = self.run_hidden(case=case)
        self.assertEqual(len(popens.procs), 1)
        self.assertIn("hidden tests", result.infra)

    def test_no_docker_for_the_second_container_is_infra(self):
        class GoesAway(FakeDocker):
            """Docker answers the first `docker info` (before the agent starts) and is gone afterwards."""

            def __call__(self, argv, **kw):
                if argv[:2] == ["docker", "info"] and any(c[:2] == ["docker", "info"] for c in self.calls):
                    self.calls.append(argv)
                    return subprocess.CompletedProcess(argv, 1, "", "")
                return super().__call__(argv, **kw)

        result, popens, _, _ = self.run_hidden(docker=GoesAway())
        self.assertEqual(len(popens.procs), 1)
        self.assertIn("hidden tests: docker", result.infra)

    def test_not_started_when_the_agent_run_is_infra(self):
        result, popens, _, _ = self.go(self.case, HiddenPopens(code=1, stderr="usage limit reached\n"))
        self.assertIsNotNone(result.infra)
        self.assertEqual(len(popens.procs), 1)
        self.assertEqual(result.hidden, {})

    def test_not_started_when_the_agent_container_was_not_removed(self):
        class NoRemove(FakeDocker):
            def __call__(self, argv, **kw):
                if argv[:3] == ["docker", "rm", "-f"]:
                    return subprocess.CompletedProcess(argv, 1, "", "boom")
                return super().__call__(argv, **kw)

        popens = HiddenPopens(hang=True, stdout=trun.ASSISTANT + "\n")
        with contextlib.redirect_stderr(io.StringIO()):  # the runner reports the failed removal there
            result, popens, _, _ = self.go(self.case, popens, docker=NoRemove())
        self.assertTrue(result.cleanup_failed)
        self.assertEqual(len(popens.procs), 1)

    def test_runs_after_an_agent_timeout(self):
        result, popens, _, _ = self.go(self.case, HiddenPopens(hidden=({"stderr": UNITTEST_OK},), hang=True,
                                                               stdout=trun.ASSISTANT + "\n"))
        self.assertTrue(result.timed_out)
        self.assertEqual(len(popens.procs), 2)
        self.assertEqual(result.hidden[0]["passed"], 3)

    def test_interrupt_removes_the_hidden_container(self):
        popens = HiddenPopens(hidden=({"interrupt": True},))  # (the fake agent writes no log: it is waited on twice)
        docker = FakeDocker()
        self.install(self.case)
        with mock.patch("houselib.evals.runner.os.killpg"), self.assertRaises(KeyboardInterrupt):
            runner.run_case(self.case, self.root, run=docker, popen=popens, clock=iter(range(0, 1000, 5)).__next__)
        name = trun.option(popens.procs[1].argv, "--name")
        self.assertIn(["docker", "rm", "-f", name], docker.calls)
        self.assertEqual(list(self.outbase.iterdir()), [])  # the out-dir is gone

    def test_output_files_live_in_the_out_dir_not_in_the_contest(self):
        result, popens, _, _ = self.run_hidden()
        self.assertEqual(popens.procs[1].kw["stdout"], subprocess.PIPE)  # drained into the out-dir (C6 of K5)
        self.assertTrue((result.out_dir / "hidden-0.err").is_file())
        self.assertFalse(list(self.root.contest.rglob("hidden*")))


class ScoreTest(tgrade.GradeBase):
    def case4(self, **kw):
        """Three outcome predicates and one expect: 4 scored entries."""
        return tgrade.make_case(
            expect=[tgrade.call("merge", "Bash", "house merge")],
            outcome=[{"type": "file_exists", "path": "keep.txt", "exists": True},
                     {"type": "git_clean"}, {"type": "git_branch", "name": "main", "exists": True}], **kw)

    def test_all_passed_is_one(self):
        g = self.grade(self.case4(), tgrade.Log().bash("house merge c T001"))
        self.assertEqual((g.result, g.score), ("pass", 1.0))

    def test_one_failed_predicate_is_three_quarters(self):
        (self.contest / "dirty.txt").write_text("x")
        g = self.grade(self.case4(), tgrade.Log().bash("house merge c T001"))
        self.assertEqual((g.result, g.failed_predicates, g.score), ("fail", ["outcome[1] git_clean"], 0.75))

    def test_a_missing_expect_counts_as_one_failed_entry(self):
        g = self.grade(self.case4(), tgrade.Log().bash("ls"))
        self.assertEqual(g.score, 0.75)

    def test_forbid_counts_as_one_entry(self):
        case = tgrade.make_case(forbid=[tgrade.call("push", "Bash", "git push")],
                                outcome=[{"type": "git_clean"}])
        g = self.grade(case, tgrade.Log().bash("git push"))
        self.assertEqual((g.forbidden, g.score), (["push: 1"], 0.5))

    def test_order_pairs_are_not_scored(self):
        case = tgrade.make_case(expect=[tgrade.call("a", "Bash", "aaa"), tgrade.call("b", "Bash", "bbb")],
                                order=[("b", "a")])
        g = self.grade(case, tgrade.Log().bash("aaa").bash("bbb"))
        self.assertEqual((g.order_violations, g.score), (["b<a"], 1.0))

    def test_no_predicates_has_no_score(self):
        self.assertIsNone(self.grade(tgrade.make_case(), tgrade.Log().bash("ls")).score)

    def test_infra_and_incomplete_have_no_score(self):
        case = self.case4()
        self.assertIsNone(self.grade(case, infra="docker: missing").score)
        self.assertIsNone(self.grade(case, tgrade.Log(terminal=False)).score)
        self.assertIsNone(self.grade(case, tgrade.Log().result("usage limit", True)).score)

    def test_timeout_and_max_turns_are_scored(self):
        log = tgrade.Log().bash("house merge c T001")
        g = self.grade(self.case4(), log, timed_out=True, exit=None)
        self.assertEqual((g.result, g.note, g.score), ("fail", "timeout", 1.0))
        (self.contest / "dirty.txt").write_text("x")
        g = self.grade(self.case4(max_turns=1), tgrade.Log().bash("ls").bash("ls"))
        self.assertEqual((g.result, g.note, g.failed_predicates, g.missing, g.score),
                         ("fail", "max_turns", ["outcome[1] git_clean"], ["merge: 0 (min 1)"], 0.5))

    def test_paused_run_is_scored(self):
        (self.contest / ".house/QUESTIONS.md").write_text(
            "# Questions\n\n## Q-001 [open] old\n- **Poziom:** zwykły\n\n## Q-003 [open] new\n- **Poziom:** zwykły\n")
        case = tgrade.make_case(expect=[tgrade.call("merge", "Bash", "house merge")],
                                outcome=[{"type": "file_exists", "path": "keep.txt", "exists": True}])
        g = self.grade(case, tgrade.Log().bash("house merge c T001"))
        self.assertEqual((g.result, g.score), ("paused", 1.0))

    def test_predicates_map_every_scored_entry_to_a_boolean(self):
        (self.contest / "dirty.txt").write_text("x")
        case = tgrade.make_case(
            expect=[tgrade.call("merge", "Bash", "house merge"), tgrade.call("check", "Bash", "house check")],
            forbid=[tgrade.call("push", "Bash", "git push")],
            order=[("check", "merge")],
            outcome=[{"type": "file_exists", "path": "keep.txt", "exists": True}, {"type": "git_clean"}])
        g = self.grade(case, tgrade.Log().bash("house merge c T001").bash("git push"))
        self.assertEqual(g.predicates, {"merge": True, "check": False, "push": False,
                                        "outcome[0] file_exists": True, "outcome[1] git_clean": False})

    def test_predicates_empty_without_evaluation(self):
        case = self.case4()
        self.assertEqual(self.grade(case, infra="docker: missing").predicates, {})
        self.assertEqual(self.grade(case, tgrade.Log(terminal=False)).predicates, {})

    def test_predicates_present_for_a_timeout(self):
        g = self.grade(self.case4(), tgrade.Log().bash("house merge c T001"), timed_out=True, exit=None)
        self.assertEqual(g.predicates["merge"], True)

    def test_git_tampering_fails_the_git_predicates(self):
        with (self.contest / ".git/config").open("a") as f:
            f.write("[include]\n\tpath = /etc/passwd\n")
        g = self.grade(self.case4(), tgrade.Log().bash("house merge c T001"))
        self.assertEqual((g.result, g.score), ("fail", 0.5))  # file_exists holds; git_clean, git_branch fail


class HiddenGradeTest(tgrade.GradeBase):
    def hidden_case(self, **p):
        return tgrade.make_case(outcome=[{**HIDDEN, **p}])

    def grade_hidden(self, hidden, **p):
        return self.grade(self.hidden_case(**p), hidden=hidden)

    def test_exit_zero_and_enough_passed(self):
        g = self.grade_hidden({0: {"exit": 0, "passed": 2, "failed": 0}})
        self.assertEqual((g.result, g.score), ("pass", 1.0))
        self.assertEqual(g.hidden, [{"outcome": 0, "exit": 0, "passed": 2, "failed": 0}])

    def test_too_few_passed_fails(self):
        g = self.grade_hidden({0: {"exit": 0, "passed": 1, "failed": 0}})
        self.assertEqual((g.result, g.failed_predicates), ("fail", ["outcome[0] hidden_tests"]))
        self.assertIn("passed 1 < 2", g.note)

    def test_non_zero_exit_fails_even_with_enough_passed(self):
        g = self.grade_hidden({0: {"exit": 1, "passed": 5, "failed": 1}})
        self.assertEqual(g.failed_predicates, ["outcome[0] hidden_tests"])
        self.assertEqual((g.score, g.hidden[0]["failed"]), (0.0, 1))

    def test_timeout_fails_with_a_note(self):
        g = self.grade_hidden({0: {"exit": None, "passed": 0, "failed": 0}})
        self.assertEqual(g.failed_predicates, ["outcome[0] hidden_tests"])
        self.assertIn("timed out", g.note)

    def test_result_that_never_came_fails(self):
        g = self.grade_hidden({})
        self.assertEqual(g.failed_predicates, ["outcome[0] hidden_tests"])
        self.assertIn("did not run", g.note)

    def test_min_passed_is_per_predicate(self):
        case = tgrade.make_case(outcome=[{**HIDDEN, "min_passed": 1}, {**HIDDEN, "min_passed": 4}])
        g = self.grade(case, hidden={0: {"exit": 0, "passed": 2, "failed": 0}, 1: {"exit": 0, "passed": 2, "failed": 0}})
        self.assertEqual((g.failed_predicates, g.score), (["outcome[1] hidden_tests"], 0.5))

    def test_a_hidden_infra_run_is_infra_error(self):
        g = self.grade(self.hidden_case(), infra="hidden tests: docker exit 125")
        self.assertEqual((g.result, g.score), ("infra_error", None))

    def test_grade_holds_counts_only(self):
        g = self.grade_hidden({0: {"exit": 0, "passed": 2, "failed": 0}})
        self.assertNotIn(tgrade.CANARY, json.dumps(g.__dict__, default=str))


class FixtureTest(unittest.TestCase):
    def test_write_hidden_puts_the_files_outside_the_contest(self):
        with tempfile.TemporaryDirectory() as tmp:
            contest = Path(tmp) / "house/contests/calc"
            contest.mkdir(parents=True)
            out = fixtures.write_hidden(contest, "chain", {"test_a.py": "a\n", "sub/test_b.py": "b\n"})
            self.assertEqual(out, Path(tmp) / "hidden/chain")
            self.assertEqual((out / "sub/test_b.py").read_text(), "b\n")
            self.assertFalse(any(p.name.startswith("test_") for p in contest.rglob("*")))

    def test_write_hidden_refuses_paths_leaving_the_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            contest = Path(tmp) / "house/contests/calc"
            contest.mkdir(parents=True)
            for dir_name, rel in (("../x", "a.py"), ("chain", "../a.py"), ("/abs", "a.py"), ("chain", "/a.py")):
                with self.subTest(dir=dir_name, rel=rel), self.assertRaises(ValueError):
                    fixtures.write_hidden(contest, dir_name, {rel: "x"})


class RowTest(tcli.EvalCliBase):
    def test_row_has_score_and_hidden(self):
        hidden = [{"outcome": 2, "exit": 0, "passed": 3, "failed": 0}]

        def graded(case, run, root):
            return grade.Grade("fail", failed_predicates=["outcome[1] git_clean"], score=0.75, hidden=hidden)

        with mock.patch.object(tcli.cli.evalgrade, "grade", side_effect=graded):
            self.call("run", "--name", "alpha")
        row = self.rows()[-1]
        self.assertEqual((row["score"], row["hidden"]), (0.75, hidden))

    def test_infra_row_has_a_null_score(self):
        self.verdicts["alpha"] = ["infra_error"]
        self.call("run", "--name", "alpha")
        row = self.rows()[-1]
        self.assertIsNone(row["score"])
        self.assertEqual(row["hidden"], [])


class VariantPromptReadErrorTest(unittest.TestCase):
    """R5: a `{file:}` target that cannot be read is `variant <name>: ...` infra, never another exception."""

    def setUp(self):
        self.fc = tvar.FakeContest(self)

    def resolve(self, ref):
        return runner.variant_prompt(self.fc.root, Variant("v", prompt="x {file:%s}" % ref), "d")

    def test_directory_is_infra(self):
        (self.fc.contest / "docs").mkdir()
        with self.assertRaisesRegex(runner._Infra, "variant v: prompt: cannot read 'docs'"):
            self.resolve("docs")

    @unittest.skipIf(os.geteuid() == 0, "root reads everything")
    def test_unreadable_file_is_infra(self):
        path = self.fc.write("secret.md", "x")
        path.chmod(0)
        self.addCleanup(path.chmod, 0o600)
        with self.assertRaisesRegex(runner._Infra, "variant v: prompt: cannot read 'secret.md'"):
            self.resolve("secret.md")

    def test_os_error_while_reading_is_infra(self):
        self.fc.write("a.md", "x")
        with mock.patch.object(Path, "read_text", side_effect=OSError(5, "I/O error")):
            with self.assertRaisesRegex(runner._Infra, "variant v: prompt: cannot read 'a.md'"):
                self.resolve("a.md")

    def test_nul_in_the_path_is_infra(self):
        with self.assertRaisesRegex(runner._Infra, "variant v"):
            self.resolve("a\x00b.md")


class RunCasePromptErrorTest(trun.RunnerCase):
    def test_unreadable_prompt_file_is_an_infra_result_not_an_exception(self):
        self.root.commit_patch = lambda: None
        (self.root.contest / "docs").mkdir()
        result, popens, _, _ = self.go(trun.make_case(), variant=Variant("v", prompt="{file:docs}"))
        self.assertEqual(popens.procs, [])
        self.assertIn("variant v: prompt", result.infra)
        self.assertFalse(runner.is_setup_error(result.infra))


if __name__ == "__main__":
    unittest.main()
