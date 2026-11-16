"""`house setup --signal-timer`: systemd user units that pull Signal every 5 minutes (fake systemctl only)."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from houselib import doctor, setup


class FakeRun:
    def __init__(self, active="active\n"):
        self.calls, self.active = [], active

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        out = self.active if "is-active" in argv else ""
        return subprocess.CompletedProcess(argv, 0, out, "")


class TimerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.root = self.home / "house"

    def tearDown(self):
        self._tmp.cleanup()

    def test_units_written_and_enabled(self):
        run = FakeRun()
        paths = setup.install_signal_timer(self.root, home=self.home, run=run)
        units = self.home / ".config/systemd/user"
        self.assertEqual(paths, [units / "house-signal-pull.service", units / "house-signal-pull.timer"])
        service = paths[0].read_text()
        self.assertIn(f"ExecStart={self.root.resolve()}/bin/house signal pull --timeout 5 --maintenance", service)
        self.assertIn("Type=oneshot", service)
        timer = paths[1].read_text()
        for line in ("OnUnitActiveSec=5min", "OnBootSec=1min", "Persistent=true", "WantedBy=timers.target"):
            self.assertIn(line, timer)
        self.assertEqual(run.calls, [["systemctl", "--user", "daemon-reload"],
                                     ["systemctl", "--user", "enable", "--now", "house-signal-pull.timer"]])

    def test_idempotent_rewrite(self):
        setup.install_signal_timer(self.root, home=self.home, run=FakeRun())
        first = (self.home / ".config/systemd/user/house-signal-pull.timer").read_text()
        setup.install_signal_timer(self.root, home=self.home, run=FakeRun())
        self.assertEqual((self.home / ".config/systemd/user/house-signal-pull.timer").read_text(), first)

    def test_interval_parameter(self):
        setup.install_signal_timer(self.root, home=self.home, run=FakeRun(), every="1min")
        self.assertIn("OnUnitActiveSec=1min", (self.home / ".config/systemd/user/house-signal-pull.timer").read_text())

    def test_doctor_timer_line(self):
        self.assertIsNone(doctor.signal_timer_status(home=self.home, run=FakeRun()))
        setup.install_signal_timer(self.root, home=self.home, run=FakeRun())
        self.assertEqual(doctor.signal_timer_status(home=self.home, run=FakeRun()), "✓ signal: timer odbioru działa")
        line = doctor.signal_timer_status(home=self.home, run=FakeRun(active="inactive\n"))
        self.assertTrue(line.startswith("! signal: timer zainstalowany, ale nieaktywny"), line)

    def test_setup_signal_timer_cli_only_installs_timer(self):
        import contextlib
        import io

        from houselib import cli
        out = io.StringIO()
        with mock.patch("houselib.setup.install_signal_timer", return_value=[Path("/x.service"), Path("/x.timer")]) \
                as inst, mock.patch("houselib.setup.install_user_skills") as skills, contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["setup", "--signal-timer"]), 0)
        inst.assert_called_once()
        skills.assert_not_called()
        self.assertIn("timer", out.getvalue())


if __name__ == "__main__":
    unittest.main()


class TimerReviewTest(TimerTest):
    def test_service_has_path_and_long_timeout(self):
        paths = setup.install_signal_timer(self.root, home=self.home, run=FakeRun(), claude="/opt/c/bin/claude")
        service = paths[0].read_text()
        self.assertIn("TimeoutStartSec=900", service)
        self.assertIn("Environment=PATH=/opt/c/bin:", service)

    def test_missing_systemctl_raises(self):
        def run(argv, **kw):
            raise FileNotFoundError("systemctl")
        with self.assertRaises(RuntimeError):
            setup.install_signal_timer(self.root, home=self.home, run=run)

    def test_failed_enable_raises(self):
        def run(argv, **kw):
            return subprocess.CompletedProcess(argv, 1, "", "Failed to connect to bus")
        with self.assertRaises(RuntimeError):
            setup.install_signal_timer(self.root, home=self.home, run=run)

    def test_doctor_warns_without_linger(self):
        setup.install_signal_timer(self.root, home=self.home, run=FakeRun())

        def run(argv, **kw):
            out = "active\n" if "is-active" in argv else "Linger=no\n"
            return subprocess.CompletedProcess(argv, 0, out, "")
        line = doctor.signal_timer_status(home=self.home, run=run)
        self.assertIn("linger", line)
