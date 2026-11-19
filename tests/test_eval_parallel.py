"""`house eval run --parallel N` and repeated `--variant` (K5, Task P): overlap, interleaving, isolation, interrupt.

Fakes only: the runner, the grader and the temp root are the ones of `test_eval_cli` (no `claude`, no Docker)."""
import contextlib
import json
import os
import signal
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from houselib import cli, locks
from houselib.evals import results
from houselib.evals.runner import RunResult
from tests import test_eval_cli as tcli

VARIANTS = {"ctl": {"settings": {"env": {"X": "1"}}}, "other": {"remove": ["x"]}}


class ParallelBase(tcli.EvalCliBase):
    """Thread-safe replacement of the base fake runner, and a recorder of what each run saw."""

    def setUp(self):
        super().setUp()
        self.write_case("alpha", variants=VARIANTS)
        self.lock = threading.Lock()
        self.seen: list = []  # (case name, variant name or None) in the order the runs started
        self.threads: set[int] = set()
        self.prepared = mock.Mock()
        p = mock.patch.object(cli, "_eval_prepare_docker", self.prepared)
        p.start()
        self.addCleanup(p.stop)

    def fake_run(self, case, root, model=None, **kw):
        variant = kw.get("variant")
        with self.lock:
            self.seen.append((case.name, variant.name if variant else None))
            self.threads.add(threading.get_ident())
        out = Path(tempfile.mkdtemp(dir=self._tmp.name))
        (out / "run.jsonl").write_text('{"type":"system"}\n')
        with self.lock:
            self.out_dirs.append(out)
        return RunResult(out / "run.jsonl", 0, 12.4, None, False, "sid", out)

    def raw_lines(self):
        return self.results_path.read_text().splitlines()


class OverlapTest(ParallelBase):
    def test_n_runs_really_overlap(self):
        barrier = threading.Barrier(3, timeout=10)
        orig = self.fake_run

        def meet(*a, **kw):
            barrier.wait()  # raises BrokenBarrierError unless three runs are inside at the same time
            return orig(*a, **kw)

        with mock.patch.object(cli.evalrunner, "run_case", side_effect=meet):
            code, out, err = self.call("run", "--name", "alpha", "--repeat", "3", "--parallel", "3")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.rows()), 3)
        self.assertEqual(len(self.threads), 3)

    def test_never_more_than_n_at_once(self):
        live, peak = [0], [0]
        orig = self.fake_run

        def counted(*a, **kw):
            with self.lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            time.sleep(0.05)
            try:
                return orig(*a, **kw)
            finally:
                with self.lock:
                    live[0] -= 1

        with mock.patch.object(cli.evalrunner, "run_case", side_effect=counted):
            code, _, _ = self.call("run", "--name", "alpha", "--repeat", "6", "--parallel", "2")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.rows()), 6)
        self.assertEqual(peak[0], 2)

    def test_the_eval_lock_is_taken_once_and_released(self):
        held = []
        orig = self.fake_run

        def spy(*a, **kw):
            held.append(locks.held("eval"))
            return orig(*a, **kw)

        with mock.patch.object(cli.evalrunner, "run_case", side_effect=spy):
            self.call("run", "--name", "alpha", "--repeat", "3", "--parallel", "3")
        self.assertEqual(held, [True] * 3)
        self.assertFalse(locks.held("eval"))

    def test_rows_of_all_runs_are_complete_json_lines(self):
        big = {"note": "x" * 20000}
        orig = self.fake_run

        def fat(*a, **kw):
            return orig(*a, **kw)

        real_grade = self.fake_grade

        def fat_grade(case, run, root):
            g = real_grade(case, run, root)
            g.note = big["note"]
            return g

        with mock.patch.object(cli.evalrunner, "run_case", side_effect=fat), \
                mock.patch.object(cli.evalgrade, "grade", side_effect=fat_grade):
            self.call("run", "--name", "alpha", "--repeat", "8", "--parallel", "4")
        lines = self.raw_lines()
        self.assertEqual(len(lines), 8)
        for line in lines:
            self.assertEqual(json.loads(line)["case"], "alpha")

    def test_prepares_docker_once_before_the_threads_start(self):
        self.call("run", "--name", "alpha", "--repeat", "2", "--parallel", "2")
        self.prepared.assert_called_once()

    def test_parallel_1_does_not_prepare_docker_and_runs_in_the_calling_thread(self):
        with mock.patch.object(cli.evalrunner, "run_case", side_effect=self.fake_run):
            self.call("run", "--name", "alpha", "--repeat", "2")
        self.prepared.assert_not_called()
        self.assertEqual(self.threads, {threading.get_ident()})


class AppendTest(unittest.TestCase):
    def test_concurrent_appends_never_interleave(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "evals/results.jsonl"
            barrier = threading.Barrier(8)

            def put(i):
                barrier.wait()
                for j in range(20):
                    results.append(path, {"i": i, "j": j, "pad": "é" * 5000})

            threads = [threading.Thread(target=put, args=(i,)) for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 160)
            self.assertEqual(len({(json.loads(x)["i"], json.loads(x)["j"]) for x in lines}), 160)


class VariantListTest(ParallelBase):
    def run_variants(self, *argv):
        with mock.patch.object(cli.evalrunner, "run_case", side_effect=self.fake_run):
            return self.call("run", "--name", "alpha", *argv)

    def test_one_variant_keeps_todays_semantics(self):
        code, _, _ = self.run_variants("--variant", "ctl", "--repeat", "2")
        self.assertEqual(code, 0)
        self.assertEqual(self.seen, [("alpha", "ctl")] * 2)
        self.assertEqual([r["variant"] for r in self.rows()], ["ctl", "ctl"])

    def test_no_variant_means_default(self):
        self.run_variants()
        self.assertEqual(self.seen, [("alpha", None)])
        self.assertEqual(self.rows()[0]["variant"], "default")

    def test_variants_interleave_per_repeat(self):
        code, _, _ = self.run_variants("--variant", "default", "--variant", "ctl", "--variant", "other",
                                       "--repeat", "2")
        self.assertEqual(code, 0)
        self.assertEqual([v for _, v in self.seen], [None, "ctl", "other"] * 2)
        self.assertEqual([r["variant"] for r in self.rows()], ["default", "ctl", "other"] * 2)

    def test_interleaving_covers_every_selected_case_in_turn(self):
        self.write_case("beta", variants=VARIANTS, tags=["t"])
        self.write_case("alpha", variants=VARIANTS, tags=["t"])
        with mock.patch.object(cli.evalrunner, "run_case", side_effect=self.fake_run):
            self.call("run", "--tag", "t", "--variant", "ctl", "--variant", "other", "--repeat", "2")
        self.assertEqual(self.seen, [("alpha", "ctl"), ("alpha", "other")] * 2
                         + [("beta", "ctl"), ("beta", "other")] * 2)

    def test_max_runs_caps_the_total_and_stays_balanced(self):
        self.run_variants("--variant", "default", "--variant", "ctl", "--variant", "other", "--repeat", "3",
                          "--max-runs", "5")
        self.assertEqual([v for _, v in self.seen], [None, "ctl", "other", None, "ctl"])

    def test_max_runs_caps_parallel_runs_too(self):
        self.run_variants("--variant", "default", "--variant", "ctl", "--repeat", "5", "--max-runs", "4",
                          "--parallel", "3")
        self.assertEqual(len(self.rows()), 4)
        self.assertEqual(sorted(v or "default" for _, v in self.seen), ["ctl", "ctl", "default", "default"])

    def test_a_repeated_variant_is_run_once_per_repeat(self):
        self.run_variants("--variant", "ctl", "--variant", "ctl")
        self.assertEqual(self.seen, [("alpha", "ctl")])

    def test_unknown_variant_in_the_list_is_exit_2_before_any_run(self):
        code, _, err = self.run_variants("--variant", "ctl", "--variant", "nope")
        self.assertEqual(code, 2)
        self.assertIn("nope", err)
        self.assertEqual(self.seen, [])


class OutputTest(ParallelBase):
    def test_parallel_1_single_variant_output_is_unchanged(self):
        with mock.patch.object(cli.evalrunner, "run_case", side_effect=self.fake_run):
            code, out, _ = self.call("run", "--name", "alpha", "--repeat", "2")
        first = out.splitlines()[0]
        self.assertEqual(first, "alpha 1/2 pass 12s")

    def test_line_names_variant_score_and_weighted_cost(self):
        def rate(case, run, root):
            g = self.fake_grade(case, run, root)
            g.score = 0.75
            return g

        with mock.patch.object(cli.evalrunner, "run_case", side_effect=self.fake_run), \
                mock.patch.object(cli.evalgrade, "grade", side_effect=rate), \
                mock.patch.object(cli.evalresults, "run_metrics",
                                  return_value={"weighted": 123456, "peak": 1, "calls": 1, "compactions": 0,
                                                "output": 1}):
            code, out, _ = self.call("run", "--name", "alpha", "--variant", "default", "--variant", "ctl")
        lines = out.splitlines()
        self.assertEqual(lines[0], "alpha 1/1 pass 12s variant=default score=0.75 weighted=123K")
        self.assertEqual(lines[1], "alpha 1/1 pass 12s variant=ctl score=0.75 weighted=123K")

    def test_parallel_lines_appear_as_the_runs_finish_and_the_summary_follows(self):
        orig = self.fake_run
        started = []

        def slow_first(case, root, model=None, **kw):
            with self.lock:
                started.append(1)
                first = len(started) == 1
            if first:
                time.sleep(0.3)
            return orig(case, root, model, **kw)

        self.verdicts["alpha"] = ["fail", "pass"]
        with mock.patch.object(cli.evalrunner, "run_case", side_effect=slow_first):
            code, out, _ = self.call("run", "--name", "alpha", "--repeat", "2", "--parallel", "2")
        lines = out.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].startswith("alpha 2/2"), lines)  # the first-started run finished last
        self.assertTrue(lines[1].startswith("alpha 1/2"), lines)
        self.assertTrue(lines[2].startswith("summary: alpha"), lines)
        self.assertIn("variant=default", lines[0])


class IsolationTest(ParallelBase):
    def test_a_crashing_run_does_not_stop_the_others(self):
        real_grade = self.fake_grade
        calls = []

        def grade(case, run, root):
            with self.lock:
                calls.append(1)
                n = len(calls)
            if n == 2:
                raise RuntimeError("boom")
            return real_grade(case, run, root)

        with mock.patch.object(cli.evalrunner, "run_case", side_effect=self.fake_run), \
                mock.patch.object(cli.evalgrade, "grade", side_effect=grade):
            code, out, err = self.call("run", "--name", "alpha", "--repeat", "5", "--parallel", "2")
        self.assertEqual(code, 1)
        self.assertEqual(len(self.rows()), 4)
        self.assertEqual(len(self.seen), 5)
        self.assertIn("boom", err)
        self.assertIn("summary: alpha", out)
        self.assertTrue(all(not d.exists() for d in self.out_dirs))

    def test_an_infra_error_does_not_stop_the_others(self):
        self.verdicts["alpha"] = ["infra_error", "pass", "pass"]
        with mock.patch.object(cli.evalrunner, "run_case", side_effect=self.fake_run):
            code, out, err = self.call("run", "--name", "alpha", "--repeat", "3", "--parallel", "3")
        self.assertEqual(code, 0)
        self.assertEqual(sorted(r["result"] for r in self.rows()), ["infra_error", "pass", "pass"])
        self.assertIn("infra_error run(s) not counted", err)

    def test_a_crashed_regression_run_is_not_counted_as_a_pass(self):
        self.write_case("reg", kind="regression")
        with mock.patch.object(cli.evalrunner, "run_case", side_effect=self.fake_run), \
                mock.patch.object(cli.evalgrade, "grade", side_effect=RuntimeError("boom")):
            code, _, err = self.call("run", "--name", "reg", "--repeat", "2", "--parallel", "2")
        self.assertEqual(code, 1)
        self.assertEqual(self.rows(), [])
        self.assertIn("boom", err)


class InterruptTest(ParallelBase):
    def test_sigterm_aborts_every_running_run_and_starts_no_more(self):
        barrier = threading.Barrier(3, timeout=10)
        aborted: list[int] = []
        orig = self.fake_run

        def killed(*a, **kw):
            orig(*a, **kw)
            try:
                if barrier.wait() == 0:
                    os.kill(os.getpid(), signal.SIGTERM)
                for _ in range(500):  # wait to be interrupted, like `proc.wait` polling inside `run_case`
                    time.sleep(0.01)
                raise AssertionError("not interrupted")
            except BaseException:
                aborted.append(threading.get_ident())  # run_case's `_abort` path
                raise

        with mock.patch.object(cli.evalrunner, "run_case", side_effect=killed), self.assertRaises(KeyboardInterrupt):
            self.call("run", "--name", "alpha", "--repeat", "9", "--parallel", "3")
        self.assertEqual(len(set(aborted)), 3)
        self.assertEqual(len(self.seen), 3)  # nothing new was started
        self.assertEqual(len(tcli.FakeRoot.exited), 3)  # every temp root was cleaned up
        self.assertEqual(self.rows(), [])
        self.assertFalse(locks.held("eval"))
        self.assertEqual([t.name for t in threading.enumerate() if t.name.startswith("eval-run-")], [])

    def interrupted_batch(self, *, patches=()):
        """Three runs wait for SIGTERM (as in the test above); returns the thread ids that were aborted."""
        barrier = threading.Barrier(3, timeout=10)
        aborted: list[int] = []
        orig = self.fake_run

        def killed(*a, **kw):
            orig(*a, **kw)
            try:
                if barrier.wait() == 0:
                    os.kill(os.getpid(), signal.SIGTERM)
                for _ in range(500):
                    time.sleep(0.01)
                raise AssertionError("not interrupted")
            except BaseException:
                aborted.append(threading.get_ident())
                raise

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(mock.patch.object(cli.evalrunner, "run_case", side_effect=killed))
            with self.assertRaises(KeyboardInterrupt):
                self.call("run", "--name", "alpha", "--repeat", "9", "--parallel", "3")
        return aborted

    def test_the_interrupted_batch_sweeps_leftovers_once_everything_has_unwound(self):
        self.interrupted_batch()
        self.assertEqual(self.sweeps.call_count, 2)  # at the start, and after the interrupt

    def test_a_second_interrupt_during_the_interrupt_loop_still_aborts_and_joins_all(self):
        real = cli._eval_interrupt_thread
        delivered: list[int] = []
        again = [True]

        def second_sigterm(thread):
            if again:
                again.clear()
                raise KeyboardInterrupt  # a second Ctrl-C before the first worker was interrupted
            delivered.append(thread.ident)
            real(thread)

        aborted = self.interrupted_batch(patches=[mock.patch.object(cli, "_eval_interrupt_thread", second_sigterm)])
        self.assertEqual(len(set(aborted)), 3)
        self.assertEqual(len(delivered), 3)  # each worker interrupted exactly once
        self.assertEqual(self.sweeps.call_count, 2)
        self.assertEqual(len(tcli.FakeRoot.exited), 3)
        self.assertEqual([t.name for t in threading.enumerate() if t.name.startswith("eval-run-")], [])
        self.assertFalse(locks.held("eval"))

    def test_an_interrupt_during_the_sweep_repeats_the_sweep(self):
        self.sweeps.side_effect = [[], KeyboardInterrupt, ["removed stale container x"]]
        self.interrupted_batch()
        self.assertEqual(self.sweeps.call_count, 3)

    def test_signal_handlers_are_restored_after_an_interrupted_parallel_run(self):
        before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP)}

        def killed(*a, **kw):
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(2)

        with mock.patch.object(cli.evalrunner, "run_case", side_effect=killed), self.assertRaises(KeyboardInterrupt):
            self.call("run", "--name", "alpha", "--repeat", "2", "--parallel", "2")
        self.assertEqual({s: signal.getsignal(s) for s in before}, before)


class ValidationTest(ParallelBase):
    def test_parallel_must_be_1_to_4(self):
        for bad in ("0", "5", "-1"):
            code, _, err = self.call("run", "--name", "alpha", "--parallel", bad)
            self.assertEqual(code, 2, bad)
            self.assertIn("--parallel", err)
        self.assertEqual(self.seen, [])

    def test_default_is_one(self):
        self.assertEqual(cli.build_parser().parse_args(["eval", "run"]).parallel, 1)


if __name__ == "__main__":
    unittest.main()
