"""`houselib.signal`: thin wrapper around signal-cli (injectable runner, no real network in tests)."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from houselib import signal as sig
from tests import helpers  # noqa: F401  (isolates Signal config)


class FakeRun:
    def __init__(self, stdout: str = "", returncode: int = 0):
        self.calls, self.stdout, self.returncode, self.kw = [], stdout, returncode, None

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        self.kw = kw
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "INFO noise\n")


class FakePopen:
    """Stands in for subprocess.Popen: `lines` on stdout (bytes), optional death after `die_after` lines."""

    def __init__(self, lines, die_after=None, rc=0, stderr=""):
        import io
        self.raw = io.BytesIO("".join(f"{x}\n" for x in lines).encode())
        self.die_after, self.rc, self.stderr_text = die_after, rc, stderr
        self.argv = self.kw = None
        self.terminated, self.killed, self.returncode = 0, False, None

    def __call__(self, argv, **kw):
        self.argv, self.kw = argv, kw
        kw["stderr"].write(self.stderr_text.encode())
        self.stdout = self._lines()
        return self

    def _lines(self):
        for i, line in enumerate(self.raw):
            if self.die_after is not None and i >= self.die_after:
                raise OSError("pipe broken")
            yield line

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = self.rc
        return self.rc

    def terminate(self):
        self.terminated += 1

    def kill(self):
        self.killed = True


class SignalTest(unittest.TestCase):
    def setUp(self):
        import os
        from unittest import mock
        self._tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"HOUSE_DATA_DIR": str(Path(self._tmp.name) / "data")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self._tmp.cleanup()

    def out_events(self):
        from houselib import signalstore
        return [e for e in signalstore.iter_events() if e["dir"] == "out"]

    def test_send_logs_out_event(self):
        sig.Signal("+48111", "sc", run=FakeRun("1794571935365\n")).send("🤖 [house] hej", group_id="gT")
        (e,) = self.out_events()
        self.assertEqual((e["kind"], e["chat"], e["ts"], e["text"], e["status"]),
                         ("message", "gT", 1794571935365, "🤖 [house] hej", "ok"))
        self.assertEqual(e["meta"], {"origin": "python"})
        self.assertEqual(e["sender"], {"name": "house", "number": "+48111", "uuid": None})
        self.assertEqual(e["key"], "gT:+48111:1794571935365")
        self.assertIsNone(e["raw"])

    def test_note_to_self_logged_as_self(self):
        sig.Signal("+48111", "sc", run=FakeRun("5\n")).send("x", meta={"origin": "ask", "question": "Q-001"})
        (e,) = self.out_events()
        self.assertEqual((e["chat"], e["chat_alias"], e["meta"]),
                         ("self", "self", {"origin": "ask", "question": "Q-001"}))

    def test_failed_send_logged(self):
        self.assertIsNone(sig.Signal("+48111", "sc", run=FakeRun("", 1)).send("x"))
        (e,) = self.out_events()
        self.assertEqual((e["status"], e["ts"]), ("failed", None))
        self.assertIn(":out-failed-", e["key"])

    def test_send_quote_and_attachments_argv(self):
        from houselib import signalstore
        signalstore.append({"v": 1, "key": "gT:u-kuba:50", "kind": "message", "dir": "in", "chat": "gT", "ts": 50,
                            "text": "Spoko, a jaki wynik?", "sender": {"name": "Kuba Nowak"}})
        f = Path(self._tmp.name) / "lb.png"
        f.write_bytes(b"x" * 2048)
        run = FakeRun("7\n")
        sig.Signal("+48111", "sc", run=run).send("wykres", group_id="gT", attachments=[str(f)], quote=(50, "u-kuba"))
        self.assertEqual(run.calls[0], ["sc", "-a", "+48111", "send", "-g", "gT", "-m", "wykres", "--attachment",
                                        str(f), "--quote-timestamp", "50", "--quote-author", "u-kuba"])
        (e,) = self.out_events()
        self.assertEqual(e["quote"], {"ts": 50, "author": "u-kuba", "text": "Spoko, a jaki wynik?"})
        self.assertEqual(e["attachments"], [{"content_type": "image/png", "filename": "lb.png", "size": 2048,
                                             "id": None, "path": str(f)}])

    def test_log_failure_never_breaks_send(self):
        from unittest import mock
        with mock.patch("houselib.signalstore.record_out", side_effect=OSError("disk")):
            self.assertEqual(sig.Signal("+1", "sc", run=FakeRun("9\n")).send("x"), 9)

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

    def test_receive_streams_each_object(self):
        popen = FakePopen(['{"envelope": {"timestamp": 1}}', '{"envelope": {"timestamp": 2}}'])
        seen = []
        res = sig.Signal("+1", "sc", popen=popen).receive(lambda o: seen.append(o["envelope"]["timestamp"]), timeout=3)
        self.assertEqual(seen, [1, 2])
        self.assertEqual(res.objects, 2)
        self.assertIsNone(res.error)
        self.assertEqual(popen.argv, ["sc", "-a", "+1", "-o", "json", "receive", "--timeout", "3", "--ignore-stories"])
        self.assertIs(popen.kw["stdin"], subprocess.DEVNULL)

    def test_receive_max_messages(self):
        popen = FakePopen([])
        sig.Signal("+1", "sc", popen=popen).receive(lambda o: None, max_messages=200)
        self.assertEqual(popen.argv[-3:], ["--max-messages", "200", "--ignore-stories"])

    def test_receive_counts_bad_lines(self):
        popen = FakePopen(['{"envelope": {}}', "not json", "[1]", ""])
        seen = []
        res = sig.Signal("+1", "sc", popen=popen).receive(seen.append)
        self.assertEqual((len(seen), res.objects, res.bad_lines), (1, 1, 2))

    def test_receive_process_dies_midway(self):
        lines = [f'{{"envelope": {{"timestamp": {i}}}}}' for i in range(5)]
        seen = []
        res = sig.Signal("+1", "sc", popen=FakePopen(lines, die_after=3)).receive(seen.append)
        self.assertEqual(len(seen), 3)
        self.assertTrue(res.error.startswith("read:"), res.error)

    def test_receive_drains_after_stop(self):
        lines = [f'{{"envelope": {{"timestamp": {i}}}}}' for i in range(3)]
        popen = FakePopen(lines)
        seen = []
        res = sig.Signal("+1", "sc", popen=popen).receive(seen.append, should_stop=lambda: bool(seen))
        self.assertEqual(len(seen), 3)
        self.assertEqual(popen.terminated, 1)
        self.assertTrue(res.stopped)

    def test_receive_error_line(self):
        popen = FakePopen([], rc=1, stderr="INFO x\nERROR User +1 is not registered.\n" + "y" * 300)
        res = sig.Signal("+1", "sc", popen=popen).receive(lambda o: None)
        self.assertEqual(res.error, "ERROR User +1 is not registered.")
        self.assertEqual(res.returncode, 1)

    def test_receive_error_line_is_cut(self):
        res = sig.Signal("+1", "sc", popen=FakePopen([], rc=1, stderr="E" * 300)).receive(lambda o: None)
        self.assertEqual(len(res.error), 200)

    def test_receive_missing_binary(self):
        def popen(argv, **kw):
            raise FileNotFoundError(argv[0])
        seen = []
        res = sig.Signal("+1", "sc", popen=popen).receive(seen.append)
        self.assertEqual((seen, res.error), ([], "signal-cli not found"))

    def test_receive_bad_bytes(self):
        import io
        popen = FakePopen([])
        popen.raw = io.BytesIO(b'{"envelope": {"timestamp": 9, "x": "\xff\xfe"}}\n')
        seen = []
        sig.Signal("+1", "sc", popen=popen).receive(seen.append)
        self.assertEqual(seen[0]["envelope"]["timestamp"], 9)

    def test_receive_handler_error_kills_process(self):
        popen = FakePopen(['{"envelope": {}}', '{"envelope": {}}'])
        def boom(o):
            raise RuntimeError("disk full")
        with self.assertRaises(RuntimeError):
            sig.Signal("+1", "sc", popen=popen).receive(boom)
        self.assertTrue(popen.killed)

    def test_receive_handler_oserror_propagates(self):
        popen = FakePopen(['{"envelope": {}}'])
        def boom(o):
            raise OSError("no space left")
        with self.assertRaises(OSError):
            sig.Signal("+1", "sc", popen=popen).receive(boom)
        self.assertTrue(popen.killed)

    def test_send_closes_stdin(self):
        run = FakeRun("1\n")
        sig.Signal("+1", "sc", run=run).send("x")
        self.assertIs(run.kw["stdin"], subprocess.DEVNULL)

    def test_list_groups(self):
        run = FakeRun('[{"id": "g1", "name": "Team", "isMember": true}]')
        self.assertEqual(sig.Signal("+1", "sc", run=run).list_groups(), [{"id": "g1", "name": "Team"}])

    def test_tests_never_see_the_real_config(self):
        self.assertIsNone(sig.from_config())

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
