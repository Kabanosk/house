"""Process locks (`locks`), the contest-in-progress check (`registry.in_progress`) and the `house night` lock."""
import argparse
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from houselib import HOUSE_ROOT, cli, locks, registry
from tests import helpers  # noqa: F401  (sets HOUSE_DATA_DIR to a temp dir)

HOLDER = (
    "import sys, time\n"
    "from houselib import locks\n"
    "with locks.hold(sys.argv[1]):\n"
    "    print('ready', flush=True)\n"
    "    sys.stdin.readline()\n"
)


def start_holder(name: str) -> subprocess.Popen:
    """Start another process that holds lock `name` until its stdin is closed."""
    p = subprocess.Popen([sys.executable, "-c", HOLDER, name], cwd=HOUSE_ROOT, stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, text=True, env=os.environ.copy())
    assert p.stdout.readline().strip() == "ready"
    return p


def stop_holder(p: subprocess.Popen) -> None:
    p.stdin.close()
    p.wait(timeout=10)
    p.stdout.close()


class LocksTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"HOUSE_DATA_DIR": self._tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self._tmp.cleanup()

    def test_second_holder_in_same_process_gets_busy(self):
        with locks.hold("night"), self.assertRaises(locks.Busy) as cm, locks.hold("night"):
            self.fail("must not be entered")
        self.assertEqual(cm.exception.name, "night")
        self.assertEqual(cm.exception.pid, os.getpid())
        self.assertIn("night", str(cm.exception))
        self.assertIn(str(os.getpid()), str(cm.exception))

    def test_other_names_are_independent(self):
        with locks.hold("night"), locks.hold("eval"):
            self.assertTrue(locks.held("eval"))

    def test_lock_file_has_pid_and_start_time_while_held(self):
        with locks.hold("night"):
            pid, iso = (Path(self._tmp.name) / "night.lock").read_text().split()
            self.assertEqual(int(pid), os.getpid())
            datetime.fromisoformat(iso)

    def test_released_on_exception(self):
        with self.assertRaises(RuntimeError), locks.hold("night"):
            raise RuntimeError("boom")
        self.assertFalse(locks.held("night"))
        with locks.hold("night"):
            pass

    def test_held_false_when_never_taken(self):
        self.assertFalse(locks.held("night"))

    def test_busy_and_held_across_processes(self):
        p = start_holder("night")
        try:
            self.assertTrue(locks.held("night"))
            with self.assertRaises(locks.Busy) as cm, locks.hold("night"):
                self.fail("must not be entered")
            self.assertEqual(cm.exception.pid, p.pid)
            self.assertIsNotNone(cm.exception.since)
            self.assertIn(str(p.pid), str(cm.exception))
            self.assertTrue(locks.held("night"))  # probing did not disturb the holder
            self.assertFalse(locks.held("eval"))
        finally:
            stop_holder(p)
        self.assertFalse(locks.held("night"))
        with locks.hold("night"):
            pass

    def test_killed_holder_leaves_no_stale_lock(self):
        p = start_holder("night")
        p.kill()
        p.wait(timeout=10)
        p.stdin.close()
        p.stdout.close()
        self.assertFalse(locks.held("night"))


class NightLockTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"HOUSE_DATA_DIR": self._tmp.name})
        self.env.start()
        self.args = argparse.Namespace(name="c1", hours=1.0, max_tasks=None, dry_run=False)

    def tearDown(self):
        self.env.stop()
        self._tmp.cleanup()

    def _run(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "contest_dir", return_value=Path(self._tmp.name)), \
                mock.patch.object(cli.night, "preflight", return_value=([], [])), \
                mock.patch.object(cli.night, "run_night") as run_night, \
                redirect_stdout(out), redirect_stderr(err):
            code = cli.cmd_night(self.args)
        return code, out.getvalue(), err.getvalue(), run_night

    def test_night_refuses_when_lock_is_held(self):
        p = start_holder("night")
        try:
            code, _, err, run_night = self._run()
        finally:
            stop_holder(p)
        self.assertEqual(code, 1)
        self.assertIn("night is already running", err)
        self.assertIn(str(p.pid), err)
        run_night.assert_not_called()

    def test_night_holds_the_lock_during_the_run_and_releases_it(self):
        seen = []

        def fake_night(*a, **kw):
            seen.append(locks.held("night"))
            return mock.Mock(attempts=[], stopped_because="nothing ready", report_path="r.md")

        out = io.StringIO()
        with mock.patch.object(cli, "contest_dir", return_value=Path(self._tmp.name)), \
                mock.patch.object(cli.night, "preflight", return_value=([], [])), \
                mock.patch.object(cli.night, "run_night", side_effect=fake_night), redirect_stdout(out):
            self.assertEqual(cli.cmd_night(self.args), 0)
        self.assertEqual(seen, [True])
        self.assertFalse(locks.held("night"))

    def test_dry_run_does_not_take_the_lock(self):
        self.args.dry_run = True
        p = start_holder("night")
        try:
            out = io.StringIO()
            with mock.patch.object(cli, "contest_dir", return_value=Path(self._tmp.name)), \
                    mock.patch.object(cli.night, "preflight", return_value=([], [])), \
                    mock.patch.object(cli.night, "pick_ready", return_value=[]), redirect_stdout(out):
                self.assertEqual(cli.cmd_night(self.args), 0)
        finally:
            stop_holder(p)


def write_spec(contest: Path, start=None, end=None) -> None:
    house = contest / ".house"
    house.mkdir(parents=True, exist_ok=True)
    lines = ["timeline:"]
    if start is not None:
        lines.append(f"  start: {start}")
    if end is not None:
        lines.append(f"  end: {end}")
    (house / "spec.yaml").write_text("\n".join(lines) + "\n")


class InProgressTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.c = Path(self._tmp.name) / "c"
        self.now = datetime(2026, 6, 15, 12, 0)

    def tearDown(self):
        self._tmp.cleanup()

    def check(self, start, end, now=None):
        write_spec(self.c, start, end)
        return registry.in_progress(self.c, now or self.now)

    def test_inside_window(self):
        self.assertTrue(self.check("2026-06-01T00:00:00", "2026-07-01T00:00:00"))

    def test_before_start_and_after_end(self):
        self.assertFalse(self.check("2026-06-16T00:00:00", "2026-07-01T00:00:00"))
        self.assertFalse(self.check("2026-05-01T00:00:00", "2026-06-01T00:00:00"))

    def test_bounds_are_inclusive(self):
        self.assertTrue(self.check("2026-06-15T12:00:00", "2026-07-01T00:00:00"))
        self.assertTrue(self.check("2026-06-01T00:00:00", "2026-06-15T12:00:00"))

    def test_bare_dates_cover_the_whole_end_day(self):
        self.assertTrue(self.check("2026-06-15", "2026-06-15"))
        self.assertFalse(self.check("2026-06-16", "2026-06-20"))
        self.assertFalse(self.check("2026-06-01", "2026-06-14"))

    def test_empty_or_missing_end_is_not_in_progress(self):
        self.assertFalse(self.check("2026-06-01T00:00:00", ""))
        self.assertFalse(self.check("2026-06-01T00:00:00", "null"))
        self.assertFalse(self.check("2026-06-01T00:00:00", None))

    def test_empty_or_missing_start_is_not_in_progress(self):
        self.assertFalse(self.check(None, "2026-07-01T00:00:00"))
        self.assertFalse(self.check("", "2026-07-01T00:00:00"))

    def test_unparsable_is_not_in_progress(self):
        self.assertFalse(self.check("soon", "2026-07-01T00:00:00"))
        self.assertFalse(self.check("2026-06-01T00:00:00", "someday"))

    def test_no_spec_or_no_timeline(self):
        self.assertFalse(registry.in_progress(self.c, self.now))
        (self.c / ".house").mkdir(parents=True)
        (self.c / ".house/spec.yaml").write_text("mode: copilot\n")
        self.assertFalse(registry.in_progress(self.c, self.now))

    def test_timezone_aware_values(self):
        # 12:00 UTC is inside [10:00Z, 14:00Z] but outside [10:00+05:00 (= 05:00Z), 11:00Z]
        now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
        self.assertTrue(self.check("2026-06-15T10:00:00Z", "2026-06-15T14:00:00Z", now))
        self.assertFalse(self.check("2026-06-15T05:00:00+05:00", "2026-06-15T11:00:00Z", now))
        self.assertTrue(self.check("2026-06-15T17:00:00+05:00", "2026-06-15T20:00:00+05:00", now))
        self.assertFalse(self.check("2026-06-15T18:00:00+05:00", "2026-06-15T20:00:00+05:00", now))

    def test_naive_times_are_local(self):
        now = datetime(2026, 6, 15, 12, 0)  # naive now is local too
        self.assertTrue(self.check("2026-06-15T11:59:00", "2026-06-15T12:01:00", now))
        aware_now = datetime.now().astimezone()
        start = (aware_now - timedelta(minutes=1)).replace(tzinfo=None).isoformat(timespec="seconds")
        end = (aware_now + timedelta(minutes=5)).replace(tzinfo=None).isoformat(timespec="seconds")
        write_spec(self.c, start, end)
        self.assertTrue(registry.in_progress(self.c))  # default now = current local time

    def test_does_not_change_inbox_running(self):
        from houselib import inbox
        write_spec(self.c, "2026-06-01T00:00:00", "")
        self.assertEqual(inbox.running([self.c], self.now), [self.c])


class ContestsInProgressTest(unittest.TestCase):
    def test_lists_only_in_progress_contests_skipping_missing_dirs(self):
        now = datetime(2026, 6, 15, 12, 0)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "contests.yaml").write_text("live:\nold:\nopen:\nghost: git@x:ghost.git\n")
            write_spec(root / "contests/live", "2026-06-01", "2026-06-30")
            write_spec(root / "contests/old", "2026-01-01", "2026-02-01")
            write_spec(root / "contests/open", "2026-06-01", "")
            self.assertEqual(registry.contests_in_progress(root, now), ["live"])

    def test_missing_registry(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(registry.contests_in_progress(Path(d)), [])


if __name__ == "__main__":
    unittest.main()
