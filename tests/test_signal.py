"""`houselib.signal`: thin wrapper around signal-cli (injectable runner, no real network in tests)."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from houselib import signal as sig


class FakeRun:
    def __init__(self, stdout: str = "", returncode: int = 0):
        self.calls, self.stdout, self.returncode = [], stdout, returncode

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "INFO noise\n")


class SignalTest(unittest.TestCase):
    def test_send_note_to_self(self):
        run = FakeRun("1794571935365\n")
        s = sig.Signal("+48111", "/bin/signal-cli", run=run)
        self.assertEqual(s.send("hi"), 1794571935365)
        self.assertEqual(run.calls[0], ["/bin/signal-cli", "-a", "+48111", "send", "--note-to-self",
                                        "--notify-self", "-m", "hi"])

    def test_send_group(self):
        run = FakeRun("17\n")
        sig.Signal("+48111", "sc", run=run).send("hi", group_id="abc=")
        self.assertEqual(run.calls[0], ["sc", "-a", "+48111", "send", "-g", "abc=", "-m", "hi"])

    def test_send_failure_returns_none(self):
        self.assertIsNone(sig.Signal("+1", "sc", run=FakeRun("", 1)).send("x"))

    def test_receive_skips_junk(self):
        run = FakeRun('{"envelope": {"timestamp": 1}}\nnot json\n\n{"envelope": {"timestamp": 2}}\n')
        out = sig.Signal("+1", "sc", run=run).receive(timeout=3)
        self.assertEqual([e["envelope"]["timestamp"] for e in out], [1, 2])
        self.assertEqual(run.calls[0], ["sc", "-a", "+1", "-o", "json", "receive", "--timeout", "3"])

    def test_list_groups(self):
        run = FakeRun('[{"id": "g1", "name": "Team", "isMember": true}]')
        self.assertEqual(sig.Signal("+1", "sc", run=run).list_groups(), [{"id": "g1", "name": "Team"}])

    def test_from_config(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "notify.env"
            self.assertIsNone(sig.from_config(cfg, which=lambda _: "/x/signal-cli"))
            cfg.write_text("# c\nHOUSE_NOTIFY_CHANNEL=signal\nHOUSE_SIGNAL_ACCOUNT=+48111\n")
            s = sig.from_config(cfg, which=lambda _: "/x/signal-cli")
            self.assertEqual((s.account, s.binary), ("+48111", "/x/signal-cli"))
            self.assertIsNone(sig.from_config(cfg, which=lambda _: None, fallback=Path(d) / "none"))


if __name__ == "__main__":
    unittest.main()
