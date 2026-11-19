"""`house out`: a long command runs into a log, the agent sees a short envelope."""
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from houselib import exp, outlog
from tests.helpers import git

PY = sys.executable


def py(code: str) -> list[str]:
    return [PY, "-c", code]


class OutlogTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.contest = Path(self._tmp.name) / "contest"
        (self.contest / ".house").mkdir(parents=True)

    def logs(self) -> list[Path]:
        return sorted((self.contest / ".house/tmp/out").glob("*.log"))

    def run_out(self, code: str, **kw):
        return outlog.run(py(code), self.contest, **kw)

    def only_log(self) -> Path:
        logs = self.logs()
        self.assertEqual(len(logs), 1)
        return logs[0]


class EnvelopeTest(OutlogTest):
    def test_short_output(self):
        code, env = self.run_out("print('hello'); print('world')")
        self.assertEqual(code, 0)
        log = self.only_log()
        self.assertEqual(log.read_text(), "hello\nworld\n")
        self.assertIn("exit 0", env)
        self.assertIn("12 bytes", env)
        self.assertIn("2 lines", env)
        self.assertRegex(env, r"\d+\.\ds")
        self.assertIn("hello\nworld", env)
        self.assertIn(f"house out show {log.stem} --grep '<pattern>'", env)

    def test_id_format_and_path(self):
        fixed = datetime(2026, 11, 17, 9, 8, 7)
        code, env = self.run_out("print(1)", now=lambda: fixed)
        self.assertRegex(self.only_log().name, r"^20261117-090807-[0-9a-f]{4}\.log$")
        self.assertEqual(self.only_log().parent, self.contest / ".house/tmp/out")

    def test_two_runs_in_one_second_get_distinct_logs(self):
        fixed = datetime(2026, 11, 17, 9, 8, 7)
        for _ in range(3):
            self.run_out("print(1)", now=lambda: fixed)
        self.assertEqual(len(self.logs()), 3)

    def test_long_output_keeps_last_40_lines(self):
        code, env = self.run_out("for i in range(500): print('line', i)")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.only_log().read_text().splitlines()), 500)
        self.assertIn("500 lines", env)
        self.assertIn("line 499", env)
        self.assertIn("line 460", env)
        self.assertNotIn("line 459\n", env)
        self.assertNotIn("line 0\n", env)

    def test_envelope_fits_budget_even_with_wide_lines(self):
        code, env = self.run_out("for i in range(100): print(str(i) * 600)")
        self.assertLessEqual(len(env), outlog.ENVELOPE_MAX)
        self.assertLessEqual(outlog.ENVELOPE_MAX, 4000)
        self.assertIn("exit 0", env)
        self.assertIn("--grep", env)
        self.assertIn("99" * 10, env)  # the last line survives

    def test_envelope_function_is_bounded_for_hostile_input(self):
        lines = ["x" * 100_000] * 50
        env = outlog.envelope(exit_code=1, size=5_000_000, lines=50, seconds=3.0, log_id="ID",
                              tail="\n".join(lines))
        self.assertLessEqual(len(env), outlog.ENVELOPE_MAX)
        self.assertIn("exit 1", env)
        self.assertIn("house out show ID --grep '<pattern>'", env)

    def test_stderr_merged_in_order(self):
        code, env = self.run_out(
            "import sys\nprint('a', flush=True)\nprint('b', file=sys.stderr, flush=True)\nprint('c', flush=True)")
        self.assertEqual(self.only_log().read_text(), "a\nb\nc\n")

    def test_no_output(self):
        code, env = self.run_out("pass")
        self.assertEqual(code, 0)
        self.assertEqual(self.only_log().read_text(), "")
        self.assertIn("0 bytes", env)
        self.assertIn("0 lines", env)
        self.assertIn("(no output)", env)


class ExitCodeTest(OutlogTest):
    def test_exit_code_passed_on(self):
        code, env = self.run_out("import sys; print('boom'); sys.exit(3)")
        self.assertEqual(code, 3)
        self.assertIn("exit 3", env)
        self.assertIn("boom", env)

    def test_signal_is_128_plus_n(self):
        code, env = self.run_out("import os, signal; os.kill(os.getpid(), signal.SIGTERM)")
        self.assertEqual(code, 128 + signal.SIGTERM)
        self.assertIn(f"exit {128 + signal.SIGTERM}", env)

    def test_command_not_found(self):
        code, env = outlog.run(["definitely-not-a-command-xyz"], self.contest)
        self.assertEqual(code, 127)
        self.assertIn("exit 127", env)
        self.assertIn("not found", env)
        self.assertIn("definitely-not-a-command-xyz", self.only_log().read_text())

    def test_not_executable(self):
        f = self.contest / "script.sh"
        f.write_text("#!/bin/sh\n")
        code, env = outlog.run([str(f)], self.contest)
        self.assertEqual(code, 126)
        self.assertIn("cannot", env)

    def test_empty_command_is_refused(self):
        with self.assertRaises(ValueError):
            outlog.run([], self.contest)


class LongLinesTest(OutlogTest):
    def test_one_megabyte_line_log_keeps_tail_envelope_cuts(self):
        code, env = self.run_out(
            "import sys; sys.stdout.write('A' * 1_500_000 + 'END\\n')")
        self.assertEqual(code, 0)
        text = self.only_log().read_text()
        self.assertEqual(len(text), outlog.LOG_CAP)
        self.assertTrue(text.endswith("AAAEND\n"))
        self.assertIn("1500004 bytes", env)
        self.assertLessEqual(len(env), outlog.ENVELOPE_MAX)
        longest = max(len(line) for line in env.splitlines())
        self.assertLessEqual(longest, outlog.LINE_MAX + 60)
        self.assertIn("more characters", env)

    def test_log_larger_than_compaction_threshold_is_capped(self):
        with mock.patch.object(outlog, "LOG_CAP", 2000):
            code, env = self.run_out(
                "for i in range(20000): print('row', i)")
            text = self.only_log().read_text()
        self.assertEqual(len(text), 2000)
        self.assertTrue(text.endswith("row 19999\n"))
        self.assertIn("20000 lines", env)
        self.assertIn("log keeps the last 2000 characters", env)

    def test_multibyte_output_capped_by_characters(self):
        with mock.patch.object(outlog, "LOG_CAP", 1000):
            self.run_out("print('ą' * 5000)")
            text = self.only_log().read_text(encoding="utf-8")
        self.assertEqual(len(text), 1000)
        self.assertEqual(set(text) - {"\n"}, {"ą"})


class RobustnessTest(OutlogTest):
    def test_stop_signals_ignored_during_cleanup_and_restored(self):
        before = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        with outlog.signals_interrupt():
            inside = {s: signal.getsignal(s) for s in before}
            with outlog._signals_ignored():
                for sig in before:
                    os.kill(os.getpid(), sig)
                time.sleep(0.05)  # would raise KeyboardInterrupt / Interrupted here
            self.assertEqual({s: signal.getsignal(s) for s in before}, inside)
        self.assertEqual({s: signal.getsignal(s) for s in before}, before)

    def test_second_ctrl_c_does_not_escape(self):
        for delay in (0.5, 0.7, 0.9):
            t = threading.Timer(delay, lambda: os.kill(os.getpid(), signal.SIGINT))
            t.start()
            self.addCleanup(t.cancel)
        code, env = self.run_out(
            "import signal, time\nsignal.signal(signal.SIGINT, signal.SIG_IGN)\n"
            "print('stubborn', flush=True)\ntime.sleep(60)", grace=0.6)
        time.sleep(0.5)  # a late third signal must have found the handler restored or no signal pending
        self.assertEqual(code, 130)
        self.assertIn("stubborn", self.only_log().read_text())

    def test_tail_of_a_fast_exiting_command_is_not_lost(self):
        for _ in range(15):
            self.run_out("import sys; sys.stdout.write('x' * 200000 + 'LAST\\n')")
        for log in self.logs():
            self.assertTrue(log.read_text().endswith("xLAST\n"), log)

    def test_sink_handle_closed_when_the_pump_fails(self):
        handles = []
        real = outlog._LogSink.__init__

        def spy(sink, path):
            real(sink, path)
            handles.append(sink._f)
        with mock.patch.object(outlog._LogSink, "__init__", spy), \
                mock.patch.object(outlog, "_pump", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.run_out("import time; time.sleep(30)", grace=0.2)
        self.assertTrue(handles and handles[0].closed)

    def test_unchanged_log_is_not_rewritten(self):
        sink = outlog._LogSink(self.contest / "a.log")
        sink.write(b"hello\n")
        with mock.patch.object(Path, "write_bytes") as wb, mock.patch.object(os, "replace") as rep:
            sink.finish()
        wb.assert_not_called()
        rep.assert_not_called()

    def test_capped_log_is_replaced_atomically(self):
        with mock.patch.object(outlog, "LOG_CAP", 10):
            sink = outlog._LogSink(self.contest / "a.log")
            sink.write(b"0123456789abcdef\n")
            with mock.patch.object(os, "replace", wraps=os.replace) as rep:
                sink.finish()
        rep.assert_called_once()
        self.assertEqual((self.contest / "a.log").read_text(), "6789abcdef\n"[-10:])
        self.assertEqual([p.name for p in self.contest.iterdir() if p.name.startswith("a.log")], ["a.log"])


class ProgressTest(OutlogTest):
    def test_carriage_return_collapsed_to_last_state(self):
        code, env = self.run_out(
            "import sys\n"
            "for i in range(0, 101, 10): sys.stdout.write(f'\\rprogress {i}%')\n"
            "sys.stdout.write('\\ndone\\n')")
        self.assertIn("progress 100%", env)
        self.assertNotIn("progress 50%", env)
        self.assertNotIn("\r", env)
        self.assertIn(b"\rprogress 50%", self.only_log().read_bytes())  # the log stays raw

    def test_crlf_lines_are_not_blank(self):
        code, env = self.run_out("import sys; sys.stdout.write('one\\r\\ntwo\\r\\n')")
        self.assertIn("one\ntwo", env)


class CapTest(unittest.TestCase):
    def test_cap_tail_keeps_last_characters(self):
        self.assertEqual(outlog.cap_tail("abcdef", cap=3), "def")
        self.assertEqual(outlog.cap_tail("abc", cap=3), "abc")
        self.assertEqual(len(outlog.cap_tail("x" * (outlog.LOG_CAP + 5))), outlog.LOG_CAP)

    def test_exp_shares_cap_and_helper(self):
        self.assertIs(exp.LOG_CAP, outlog.LOG_CAP)
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "e.log"
            exp._write_log(p, ["a" * outlog.LOG_CAP, "tail"])
            text = p.read_text()
        self.assertEqual(len(text), outlog.LOG_CAP)
        self.assertTrue(text.endswith("\ntail"))


class InterruptTest(OutlogTest):
    def _interrupt_after(self, delay: float) -> threading.Timer:
        t = threading.Timer(delay, lambda: os.kill(os.getpid(), signal.SIGINT))
        t.start()
        self.addCleanup(t.cancel)
        return t

    def test_ctrl_c_keeps_log_and_prints_envelope(self):
        self._interrupt_after(1.0)
        start = time.monotonic()
        code, env = self.run_out("import time; print('before', flush=True); time.sleep(60)")
        self.assertLess(time.monotonic() - start, 4)
        self.assertEqual(code, 130)
        self.assertTrue(self.only_log().read_text().startswith("before\n"))  # then the child's KeyboardInterrupt
        self.assertIn("exit 130", env)
        self.assertIn("interrupted", env)
        self.assertIn("before", env)

    def test_sigint_ignoring_child_is_killed_after_grace(self):
        self._interrupt_after(0.8)
        code, env = self.run_out(
            "import signal, time\nsignal.signal(signal.SIGINT, signal.SIG_IGN)\n"
            "print('stubborn', flush=True)\ntime.sleep(60)", grace=0.4)
        self.assertEqual(code, 130)
        self.assertEqual(self.only_log().read_text(), "stubborn\n")

    def test_sigint_reaches_grandchildren_in_the_group(self):
        marker = self.contest / "grandchild.pid"
        code_src = (
            "import subprocess, sys, time\n"
            f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            f"open({str(marker)!r}, 'w').write(str(p.pid))\n"
            "print('spawned', flush=True)\ntime.sleep(60)")
        self._interrupt_after(1.0)
        code, env = self.run_out(code_src, grace=0.4)
        self.assertEqual(code, 130)
        pid = int(marker.read_text())
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        self.fail("grandchild survived the interrupt")

    def test_child_runs_in_its_own_group_with_unbuffered_python(self):
        code, env = self.run_out(
            "import os, sys\nprint(os.getpgrp() != os.getppid(), os.getpgrp() == os.getpid(), "
            "os.environ.get('PYTHONUNBUFFERED'))")
        self.assertEqual(self.only_log().read_text().split(), ["True", "True", "1"])


class ContestResolutionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def make_contest(self) -> Path:
        c = self.tmp / "contest"
        c.mkdir()
        git(c, "init", "-q", "-b", "main")
        git(c, "config", "user.name", "T")
        git(c, "config", "user.email", "t@example.com")
        (c / ".house").mkdir()
        (c / ".house" / "keep").write_text("x")
        (c / "f").write_text("x")
        git(c, "add", "-A")
        git(c, "commit", "-q", "-m", "init")
        return c

    def test_house_dir_env_wins(self):
        house = self.tmp / "elsewhere" / ".house"
        house.mkdir(parents=True)
        got = outlog.resolve_contest(self.tmp, environ={"HOUSE_DIR": str(house)})
        self.assertEqual(got, house.parent.resolve())

    def test_from_a_subdirectory(self):
        c = self.make_contest()
        (c / "sub").mkdir()
        self.assertEqual(outlog.resolve_contest(c / "sub", environ={}), c.resolve())

    def test_from_a_worker_worktree_log_lands_in_main_contest(self):
        c = self.make_contest()
        wt = c / ".worktrees" / "T001"
        git(c, "worktree", "add", "-q", "-b", "task/T001", str(wt))
        contest = outlog.resolve_contest(wt, environ={})
        self.assertEqual(contest, c.resolve())
        code, env = outlog.run(py("print('from worker')"), contest)
        logs = list((c / ".house/tmp/out").glob("*.log"))
        self.assertEqual(len(logs), 1)
        self.assertFalse((wt / ".house/tmp/out").exists())

    def test_outside_any_contest(self):
        with self.assertRaises(ValueError):
            outlog.resolve_contest(self.tmp, environ={})


class ShowTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.contest = Path(self._tmp.name) / "contest"
        (self.contest / ".house/tmp/out").mkdir(parents=True)
        (self.contest / ".house/experiments").mkdir()

    def out_log(self, text: str, log_id: str = "20261117-100000-abcd") -> str:
        (self.contest / ".house/tmp/out" / f"{log_id}.log").write_text(text)
        return log_id

    def test_literal_grep_with_context(self):
        lid = self.out_log("\n".join(f"row {i}" for i in range(30)) + "\n")
        text = outlog.show(self.contest, lid, "row 15")
        self.assertIn("16: row 15", text)
        for ctx in ("row 13", "row 14", "row 16", "row 17"):
            self.assertIn(ctx, text)
        self.assertNotIn("row 12\n", text)
        self.assertNotIn("row 18", text)
        self.assertNotIn("more hits", text)

    def test_literal_means_literal(self):
        lid = self.out_log("a.b\naxb\n")
        text = outlog.show(self.contest, lid, "a.b")
        self.assertIn("1: a.b", text)
        self.assertNotIn("2: axb", text)

    def test_regex(self):
        lid = self.out_log("a.b\naxb\nzzz\n")
        text = outlog.show(self.contest, lid, "a.b", regex=True)
        self.assertIn("1: a.b", text)
        self.assertIn("2: axb", text)

    def test_bad_regex_is_an_error(self):
        lid = self.out_log("x\n")
        with self.assertRaisesRegex(ValueError, "regex"):
            outlog.show(self.contest, lid, "(", regex=True)

    def test_empty_pattern_is_an_error(self):
        lid = self.out_log("x\n")
        with self.assertRaises(ValueError):
            outlog.show(self.contest, lid, "")

    def test_no_hits(self):
        lid = self.out_log("x\n")
        self.assertIn("no hits", outlog.show(self.contest, lid, "nope"))

    def test_twenty_hit_cap_with_more_hits_note(self):
        lid = self.out_log("\n".join(f"hit {i}" for i in range(50)) + "\n")
        text = outlog.show(self.contest, lid, "hit")
        self.assertEqual(len(re.findall(r"^\d+: hit", text, re.M)), 20)
        self.assertIn("20: hit 19", text)
        self.assertNotIn("22: hit 21", text)  # beyond the 20th hit only its trailing context is printed
        self.assertIn("more hits", text)
        self.assertIn("more hits: 30", text)

    def test_exactly_twenty_hits_has_no_note(self):
        lid = self.out_log("\n".join(f"hit {i}" for i in range(20)) + "\n")
        self.assertNotIn("more hits", outlog.show(self.contest, lid, "hit"))

    def test_line_cap_keeps_the_match_visible(self):
        lid = self.out_log("x" * 3000 + "NEEDLE" + "y" * 3000 + "\n")
        text = outlog.show(self.contest, lid, "NEEDLE")
        self.assertIn("NEEDLE", text)
        self.assertLessEqual(max(len(line) for line in text.splitlines()), outlog.LINE_MAX + 80)

    def test_total_cap(self):
        lid = self.out_log("\n".join(f"{'k' * 495} {i}" for i in range(100)) + "\n")
        text = outlog.show(self.contest, lid, "kkk")
        self.assertLessEqual(len(text), outlog.SHOW_MAX)
        self.assertIn("more hits", text)

    def test_large_log_with_one_huge_line_is_bounded(self):
        lid = self.out_log("A" * 900_000 + "NEEDLE\n")
        self.assertLessEqual(len(outlog.show(self.contest, lid, "NEEDLE")), outlog.SHOW_MAX)

    def test_progress_lines_collapsed(self):
        lid = self.out_log("p 1%\rp 50%\rp 100%\ndone\n")
        text = outlog.show(self.contest, lid, "p ")
        self.assertIn("p 100%", text)
        self.assertNotIn("\r", text)

    def test_experiment_log(self):
        (self.contest / ".house/experiments/E-007.log").write_text("epoch 1\nloss 0.5\nepoch 2\n")
        text = outlog.show(self.contest, "E-007", "loss")
        self.assertIn("2: loss 0.5", text)

    def test_experiment_without_log_is_a_clear_error(self):
        with self.assertRaisesRegex(ValueError, "E-009"):
            outlog.show(self.contest, "E-009", "x")

    def test_unknown_id_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "20261117-100000-ffff"):
            outlog.show(self.contest, "20261117-100000-ffff", "x")

    def test_id_must_match_exactly(self):
        (self.contest / "secret.log").write_text("secret\n")
        for bad in ("../x", "../../secret", "20261117-100000-abcd/../../../secret", "E-1/../../x", "e-001",
                    "20261117-100000-ABCD", "20261117-100000-abcd.log", " E-001", "E-001\n", "", "E-"):
            with self.assertRaisesRegex(ValueError, "id", msg=repr(bad)):
                outlog.show(self.contest, bad, "secret")

    def test_short_experiment_ids_are_normalised(self):
        (self.contest / ".house/experiments/E-007.log").write_text("loss 0.5\n")
        for ref in ("E-7", "E-07", "E-0007"):
            text = outlog.show(self.contest, ref, "loss")
            self.assertIn("E-007: 1 hits", text)

    def test_non_ascii_digits_are_not_ids(self):
        with self.assertRaisesRegex(ValueError, "id"):
            outlog.show(self.contest, "E-\u0667", "x")
        with self.assertRaisesRegex(ValueError, "id"):
            outlog.show(self.contest, "\u0662\u0660261117-100000-abcd", "x")

    def test_unreadable_bytes_do_not_crash(self):
        (self.contest / ".house/tmp/out/20261117-100000-abcd.log").write_bytes(b"ok \xff\xfe line\n")
        self.assertIn("ok", outlog.show(self.contest, "20261117-100000-abcd", "ok"))

    def test_show_from_a_worker_worktree(self):
        c = Path(self._tmp.name) / "git-contest"
        c.mkdir()
        git(c, "init", "-q", "-b", "main")
        git(c, "config", "user.name", "T")
        git(c, "config", "user.email", "t@example.com")
        (c / ".house").mkdir()
        (c / ".house" / "keep").write_text("x")
        git(c, "add", "-A")
        git(c, "commit", "-q", "-m", "init")
        wt = c / ".worktrees" / "T001"
        git(c, "worktree", "add", "-q", "-b", "task/T001", str(wt))
        code, env = outlog.run(py("print('needle in worker')"), outlog.resolve_contest(wt, environ={}))
        log_id = re.search(r"house out show (\S+) --grep", env).group(1)
        text = outlog.show(outlog.resolve_contest(wt, environ={}), log_id, "needle")
        self.assertIn("needle in worker", text)


class PruneTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.contest = Path(self._tmp.name)
        self.out = self.contest / ".house/tmp/out"
        self.out.mkdir(parents=True)
        self.now = datetime(2026, 11, 17, 12, 0, 0)

    def log(self, name: str, age_days: float) -> Path:
        p = self.out / name
        p.write_text("x")
        t = self.now.timestamp() - age_days * 86400
        os.utime(p, (t, t))
        return p

    def test_deletes_only_old_logs(self):
        old = self.log("20261101-100000-aaaa.log", 16)
        edge_old = self.log("20261109-100000-bbbb.log", 8)
        fresh = self.log("20261116-100000-cccc.log", 1)
        n = outlog.prune(self.contest, 7, now=lambda: self.now)
        self.assertEqual(n, 2)
        self.assertFalse(old.exists())
        self.assertFalse(edge_old.exists())
        self.assertTrue(fresh.exists())

    def test_default_is_seven_days(self):
        six = self.log("20261111-100000-aaaa.log", 6)
        eight = self.log("20261109-100000-bbbb.log", 8)
        self.assertEqual(outlog.prune(self.contest, now=lambda: self.now), 1)
        self.assertTrue(six.exists())
        self.assertFalse(eight.exists())

    def test_days_zero_deletes_everything(self):
        self.log("20261117-110000-aaaa.log", 0.04)
        self.assertEqual(outlog.prune(self.contest, 0, now=lambda: self.now), 1)

    def test_leaves_other_files_and_experiment_logs(self):
        other = self.log("notes.txt", 30)
        exp_dir = self.contest / ".house/experiments"
        exp_dir.mkdir()
        (exp_dir / "E-001.log").write_text("x")
        self.assertEqual(outlog.prune(self.contest, 1, now=lambda: self.now), 0)
        self.assertTrue(other.exists())
        self.assertTrue((exp_dir / "E-001.log").exists())

    def test_symlinks_are_skipped(self):
        target = self.contest / "precious.log"
        target.write_text("keep")
        old = self.log("20261101-100000-aaaa.log", 30)
        old.unlink()
        link = self.out / "20261101-100000-aaaa.log"
        link.symlink_to(target)
        os.utime(link, (0, 0), follow_symlinks=False)
        os.utime(target, (0, 0))
        self.assertEqual(outlog.prune(self.contest, 1, now=lambda: self.now), 0)
        self.assertTrue(link.is_symlink())
        self.assertTrue(target.exists())

    def test_orphan_tmp_files_removed_when_old(self):
        old = self.log(".20261101-100000-aaaa.log.tmp", 30)
        fresh = self.log(".20261117-110000-bbbb.log.tmp", 0.01)
        unrelated = self.log(".hidden.tmp", 30)
        self.assertEqual(outlog.prune(self.contest, 7, now=lambda: self.now), 1)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())
        self.assertTrue(unrelated.exists())

    def test_no_out_dir(self):
        self.assertEqual(outlog.prune(self.contest / "nowhere", 7), 0)

    def test_negative_days_is_an_error(self):
        with self.assertRaises(ValueError):
            outlog.prune(self.contest, -1)


def gone(pid: int, wait: float = 2.0) -> bool:
    """True once `pid` no longer runs (missing or a zombie), waiting up to `wait` seconds."""
    deadline = time.monotonic() + wait
    while True:
        try:
            with open(f"/proc/{pid}/stat") as f:
                if f.read().rsplit(")", 1)[1].split()[0] == "Z":
                    return True
        except (FileNotFoundError, ProcessLookupError):
            return True
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)


class PostExitTest(OutlogTest):
    """W1.4: what happens to the command's group once its leader has exited, and to interrupts."""

    def setUp(self):
        super().setUp()
        self.marker = self.contest / "bg.pid"
        # safety net: a regression that hangs is turned into an interrupt (exit 130) instead of a stuck suite
        t = threading.Timer(8.0, lambda: os.kill(os.getpid(), signal.SIGINT))
        t.start()
        self.addCleanup(t.cancel)

    def background(self, child: str, leader_tail: str = "") -> str:
        return ("import subprocess, sys\n"
                f"p = subprocess.Popen([sys.executable, '-c', {child!r}]{leader_tail})\n"
                f"open({str(self.marker)!r}, 'w').write(str(p.pid))\n"
                "print('leader done', flush=True)\nsys.exit(3)")

    def test_writing_background_child_is_stopped_and_the_leader_code_kept(self):
        start = time.monotonic()
        code, env = self.run_out(self.background(
            "import time\nwhile True:\n    print('tick', flush=True)\n    time.sleep(0.02)"))
        self.assertLess(time.monotonic() - start, 4.0)
        self.assertEqual(code, 3)
        self.assertIn("background processes stopped", env)
        self.assertIn("leader done", self.only_log().read_text())
        self.assertTrue(gone(int(self.marker.read_text())), "background child left running")

    def test_silent_background_child_ignoring_sigterm_is_killed(self):
        start = time.monotonic()
        code, env = self.run_out(self.background(
            "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)",
            ", stdout=subprocess.DEVNULL"))
        self.assertLess(time.monotonic() - start, 4.5)
        self.assertEqual(code, 3)
        self.assertIn("background processes stopped", env)
        self.assertTrue(gone(int(self.marker.read_text())), "background child left running")

    def test_late_output_of_a_finishing_child_is_kept(self):
        code, env = self.run_out(self.background("import time\ntime.sleep(0.3)\nprint('late result', flush=True)"))
        self.assertEqual(code, 3)
        self.assertIn("late result", self.only_log().read_text())
        self.assertNotIn("background processes stopped", env)
        self.assertTrue(gone(int(self.marker.read_text())))

    def test_big_report_on_interrupt_is_fully_captured(self):
        threading.Timer(0.8, lambda: os.kill(os.getpid(), signal.SIGINT)).start()
        code, env = self.run_out(
            "import os, signal, sys, time\n"
            "def report(*a):\n"
            "    sys.stdout.write('r' * 300000 + '\\nREPORT END\\n'); sys.stdout.flush(); os._exit(0)\n"
            "signal.signal(signal.SIGINT, report)\nprint('up', flush=True)\ntime.sleep(60)", grace=3.0)
        self.assertEqual(code, 130)
        self.assertTrue(self.only_log().read_text().endswith("REPORT END\n"))

    def test_sigterm_and_sighup_report_128_plus_n(self):
        for sig, expected, label in ((signal.SIGTERM, 143, "SIGTERM"), (signal.SIGHUP, 129, "SIGHUP")):
            before = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
            seen = {}
            real_finish = outlog._LogSink.finish

            def finish(sink):
                seen.update({s: signal.getsignal(s) for s in before})
                return real_finish(sink)
            t = threading.Timer(0.6, lambda: os.kill(os.getpid(), sig))
            t.start()
            self.addCleanup(t.cancel)
            with outlog.signals_interrupt(), mock.patch.object(outlog._LogSink, "finish", finish):
                code, env = self.run_out("import time; print('up', flush=True); time.sleep(60)", grace=0.5)
            self.assertEqual(code, expected, label)
            self.assertIn(f"exit {expected}", env)
            self.assertIn(label, env)
            self.assertEqual(set(seen.values()), {signal.SIG_IGN}, label)  # no handler can fire during finish
            self.assertEqual({s: signal.getsignal(s) for s in before}, before)

    def test_interrupt_during_popen_still_stops_the_command(self):
        real = subprocess.Popen

        def popen(*a, **kw):
            proc = real(*a, **kw)
            signal.pthread_kill(threading.main_thread().ident, signal.SIGTERM)  # lands before `proc` is returned
            for _ in range(1000):
                pass  # bytecodes where an unblocked handler would run
            return proc
        start = time.monotonic()
        with outlog.signals_interrupt(), mock.patch.object(outlog.subprocess, "Popen", popen):
            code, env = self.run_out(
                f"import os, time\nopen({str(self.marker)!r}, 'w').write(str(os.getpid()))\ntime.sleep(60)", grace=0.5)
        self.assertEqual(code, 143)
        self.assertLess(time.monotonic() - start, 4.0)
        deadline = time.monotonic() + 2
        while not self.marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        if self.marker.exists():  # the child got as far as starting: it must be gone now
            self.assertTrue(gone(int(self.marker.read_text())), "command left running")

    def test_child_does_not_inherit_blocked_signals(self):
        code, env = self.run_out(
            "print([l for l in open('/proc/self/status') if l.startswith('SigBlk')][0].split()[1])")
        mask = int(self.only_log().read_text().strip(), 16)
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            self.assertFalse(mask & (1 << (sig - 1)), sig)

    def test_interrupted_carries_the_signal(self):
        self.assertEqual(outlog.Interrupted(signal.SIGTERM).signum, signal.SIGTERM)
        self.assertIsInstance(outlog.Interrupted(signal.SIGHUP), KeyboardInterrupt)


class ContainmentTest(OutlogTest):
    """W1.5: out logs never leave `.house/tmp/out`, and prune leaves logs that are being written alone."""

    def elsewhere(self) -> Path:
        d = Path(self._tmp.name) / "elsewhere"
        d.mkdir(exist_ok=True)
        return d

    def test_symlinked_tmp_or_out_directory_is_refused(self):
        for rel in (".house/tmp", ".house/tmp/out"):
            with self.subTest(rel=rel):
                target = self.elsewhere()
                (self.contest / ".house/tmp").mkdir(exist_ok=True)
                link = self.contest / rel
                if link.is_dir() and not link.is_symlink():
                    link.rmdir()
                link.symlink_to(target, target_is_directory=True)
                (target / "20261101-100000-aaaa.log").write_text("other contest\n")
                (target / "out").mkdir(exist_ok=True)
                (target / "out/20261101-100000-aaaa.log").write_text("other contest\n")
                with self.assertRaisesRegex(ValueError, "symlink"):
                    self.run_out("print(1)")
                with self.assertRaisesRegex(ValueError, "symlink"):
                    outlog.show(self.contest, "20261101-100000-aaaa", "other")
                with self.assertRaisesRegex(ValueError, "symlink"):
                    outlog.prune(self.contest, 0)
                self.assertTrue((target / "20261101-100000-aaaa.log").exists())
                self.assertTrue((target / "out/20261101-100000-aaaa.log").exists())
                self.assertEqual(sorted(p.name for p in target.rglob("*.log")),
                                 ["20261101-100000-aaaa.log", "20261101-100000-aaaa.log"])
                link.unlink()
                for f in target.rglob("*.log"):
                    f.unlink()

    def test_finish_temp_file_has_no_predictable_name(self):
        victim = Path(self._tmp.name) / "victim.txt"
        victim.write_text("precious\n")
        out = self.contest / ".house/tmp/out"
        out.mkdir(parents=True)
        log = out / "20261117-100000-abcd.log"
        log.touch()
        (out / f".{log.name}.tmp").symlink_to(victim)
        with mock.patch.object(outlog, "LOG_CAP", 10):
            sink = outlog._LogSink(log)
            sink.write(b"0123456789abcdef\n")
            sink.finish()
        self.assertEqual(victim.read_text(), "precious\n")
        self.assertEqual(log.read_text(), "6789abcdef\n"[-10:])
        self.assertEqual(sorted(p.name for p in out.iterdir()), [f".{log.name}.tmp", log.name])

    def test_show_refuses_a_symlinked_log(self):
        secret = Path(self._tmp.name) / "secret.txt"
        secret.write_text("password=hunter2\n")
        out = self.contest / ".house/tmp/out"
        out.mkdir(parents=True)
        (out / "20261117-100000-abcd.log").symlink_to(secret)
        with self.assertRaises(ValueError) as ctx:
            outlog.show(self.contest, "20261117-100000-abcd", "password")
        self.assertNotIn("hunter2", str(ctx.exception))

    def test_show_refuses_a_fifo_without_blocking(self):
        out = self.contest / ".house/tmp/out"
        out.mkdir(parents=True)
        fifo = out / "20261117-100000-abcd.log"
        os.mkfifo(fifo)
        result = {}

        def call():
            try:
                outlog.show(self.contest, "20261117-100000-abcd", "x")
            except ValueError as e:
                result["error"] = str(e)
        t = threading.Thread(target=call, daemon=True)
        t.start()
        t.join(2.0)
        if t.is_alive():  # unblock the reader of the old code
            os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
            t.join(1.0)
            self.fail("show blocked on a FIFO")
        self.assertIn("error", result)

    def test_missing_log_error_names_the_id_not_a_path(self):
        for ref in ("20261117-100000-ffff", "E-009"):
            with self.assertRaises(ValueError) as ctx:
                outlog.show(self.contest, ref, "x")
            self.assertIn(ref, str(ctx.exception))
            self.assertNotIn(str(self.contest), str(ctx.exception))
            self.assertNotIn("/", str(ctx.exception))

    def test_prune_skips_a_log_being_written(self):
        out = self.contest / ".house/tmp/out"
        out.mkdir(parents=True)
        log = out / "20261101-100000-aaaa.log"
        sink = outlog._LogSink(log)
        sink.write(b"still running\n")
        os.utime(log, (0, 0))
        self.assertEqual(outlog.prune(self.contest, 0), 0)
        self.assertTrue(log.exists())
        sink.finish()
        self.assertEqual(outlog.prune(self.contest, 0), 1)
        self.assertFalse(log.exists())

    def test_prune_skips_a_temp_file_being_written(self):
        import fcntl
        out = self.contest / ".house/tmp/out"
        out.mkdir(parents=True)
        tmp = out / ".20261101-100000-aaaa.log.k2j4_x9q.tmp"
        tmp.write_text("x")
        os.utime(tmp, (0, 0))
        fd = os.open(tmp, os.O_RDONLY)
        fcntl.flock(fd, fcntl.LOCK_SH)
        try:
            self.assertEqual(outlog.prune(self.contest, 0), 0)
        finally:
            os.close(fd)
        self.assertEqual(outlog.prune(self.contest, 0), 1)

    def test_run_holds_a_shared_lock_while_the_command_runs(self):
        import fcntl
        seen = {}

        def probe():
            time.sleep(0.5)
            log = self.only_log()
            fd = os.open(log, os.O_RDONLY)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                seen["locked"] = False
            except BlockingIOError:
                seen["locked"] = True
            finally:
                os.close(fd)
            seen["pruned"] = outlog.prune(self.contest, 0)
        t = threading.Thread(target=probe)
        t.start()
        self.run_out("import time; print('a', flush=True); time.sleep(1.2); print('b')")
        t.join()
        self.assertEqual(seen, {"locked": True, "pruned": 0})
        self.assertEqual(self.only_log().read_text(), "a\nb\n")


class EnvelopeLogPathTest(OutlogTest):
    def test_envelope_names_the_log_by_house_dir(self):
        code, env = self.run_out("print('x')")
        log = self.only_log()
        self.assertIn(f"\nlog: $HOUSE_DIR/tmp/out/{log.name}\n", env)
        self.assertNotIn(str(self.contest), env)
        self.assertNotIn(str(Path.home()), env)

    def test_envelope_function_has_the_log_line_within_budget(self):
        env = outlog.envelope(exit_code=0, size=1, lines=1, seconds=0.1, log_id="20261117-100000-abcd",
                              tail="y" * 100000, log_cut=True)
        self.assertLessEqual(len(env), outlog.ENVELOPE_MAX)
        self.assertIn("log: $HOUSE_DIR/tmp/out/20261117-100000-abcd.log", env)


if __name__ == "__main__":
    unittest.main()
