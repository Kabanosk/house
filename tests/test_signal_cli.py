"""`house signal …` commands (fake Signal, temporary data dir; never the real account or config)."""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from houselib import cli, signalstore
from tests import helpers  # noqa: F401  (never the real Signal account)


class CliCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.env = mock.patch.dict(os.environ, {"HOUSE_DATA_DIR": str(self.tmp / "data"),
                                                "HOUSE_SIGNAL_CONFIG": str(self.tmp / "cfg/signal.toml")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self._tmp.cleanup()

    def run_cli(self, *argv) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(["signal", *argv])
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()


class PullMigrateTest(CliCase):
    def test_pull_without_signal(self):
        with mock.patch("houselib.signal.from_config", return_value=None):
            code, out, _ = self.run_cli("pull")
        self.assertEqual(code, 0)
        self.assertIn("Signal wyłączony", out)

    def test_pull_reports_counts(self):
        from tests import signal_fixtures as fx
        from tests.test_signalstore import FakeSig
        sig = FakeSig([fx.self_note(100, "a"), fx.dm(101, "b")])
        with mock.patch("houselib.signal.from_config", return_value=sig):
            code, out, _ = self.run_cli("pull", "--timeout", "5")
        self.assertEqual(code, 0)
        self.assertIn("odebrano 1, odrzucono 1 (spoza dozwolonych)", out)

    def test_migrate_command(self):
        from houselib import notify
        (notify.data_dir() / "inbox.jsonl").write_text('{"ts": 1, "chat": "self", "sender": "W", "text": "x"}\n')
        code, out, _ = self.run_cli("migrate")
        self.assertEqual(code, 0)
        self.assertEqual(len(list(signalstore.iter_events())), 1)
        code, out, _ = self.run_cli("migrate")
        self.assertIn("nic do migracji", out)


if __name__ == "__main__":
    unittest.main()


class FakeRun:
    def __init__(self, stdout="1794822101442\n", rc=0):
        self.calls, self.stdout, self.rc = [], stdout, rc

    def __call__(self, argv, **kw):
        import subprocess
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, self.rc, self.stdout, "")


class SendTest(CliCase):
    def setUp(self):
        super().setUp()
        from houselib.signal import Signal
        self.run = FakeRun()
        self.sig = Signal("+48111", "sc", run=self.run)
        self.patch = mock.patch("houselib.signal.from_config", return_value=self.sig)
        self.patch.start()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.home_patch = mock.patch("pathlib.Path.home", return_value=self.home)
        self.home_patch.start()

    def tearDown(self):
        self.home_patch.stop()
        self.patch.stop()
        super().tearDown()

    def argv(self):
        return self.run.calls[-1]

    def kuba(self, ts=50, author="u-kuba", name="Kuba Nowak", text="Spoko, a jaki wynik ma teraz baseline?"):
        from datetime import datetime
        t = datetime(2026, 11, 16, 12, 18).astimezone().isoformat()
        signalstore.append({"v": 1, "key": f"self:{author}:{ts}", "kind": "message", "dir": "in", "chat": "self",
                            "ts": ts, "time": t, "text": text,
                            "sender": {"name": name, "number": "+48999", "uuid": author}})

    def test_send_prefixes_and_logs(self):
        code, out, _ = self.run_cli("send", "self", "hej", "tam")
        self.assertEqual(code, 0)
        self.assertEqual(self.argv()[3:], ["send", "--note-to-self", "--notify-self", "-m", "🤖 [house] hej tam"])
        self.assertEqual(out.strip(), "✓ self 1794822101442")
        out_events = [e for e in signalstore.iter_events() if e["dir"] == "out"]
        self.assertEqual(out_events[0]["meta"], {"origin": "cli", "contest": None})

    def test_prefix_not_doubled(self):
        self.run_cli("send", "self", "🤖 [house] hej")
        self.assertEqual(self.argv()[-1], "🤖 [house] hej")

    def test_prefix_in_contest_repo(self):
        c = self.tmp / "fr-2026"
        (c / ".house").mkdir(parents=True)
        (c / ".git").mkdir()
        (c / ".house/spec.yaml").write_text("notify:\n  alias: fr\n")
        old = os.getcwd()
        os.chdir(c)
        try:
            self.run_cli("send", "self", "hej")
        finally:
            os.chdir(old)
        self.assertEqual(self.argv()[-1], "🤖 [fr] hej")

    def test_reply_to(self):
        self.kuba()
        code, out, _ = self.run_cli("send", "self", "LB 0.806", "--reply-to", "50")
        self.assertEqual(code, 0)
        self.assertEqual(self.argv()[-4:], ["--quote-timestamp", "50", "--quote-author", "u-kuba"])
        self.assertIn("(cytat: Kuba Nowak 12:18 „Spoko, a jaki wynik ma teraz…”)", out)

    def test_reply_to_unknown_ts(self):
        code, _, err = self.run_cli("send", "self", "x", "--reply-to", "77")
        self.assertEqual(code, 2)
        self.assertIn("77", err)
        self.assertEqual(self.run.calls, [])

    def test_reply_to_ambiguous_ts(self):
        self.kuba()
        self.kuba(author="u-ola", name="Ola")
        code, _, err = self.run_cli("send", "self", "x", "--reply-to", "50")
        self.assertEqual(code, 2)
        self.assertIn("--from", err)
        self.assertEqual(self.run.calls, [])
        code, _, _ = self.run_cli("send", "self", "x", "--reply-to", "50", "--from", "ola")
        self.assertEqual(code, 0)
        self.assertEqual(self.argv()[-1], "u-ola")

    def test_phone_number_refused(self):
        code, _, err = self.run_cli("send", "+48123456789", "x")
        self.assertEqual(code, 2)
        self.assertIn("DM", err)

    def test_unknown_alias(self):
        code, _, err = self.run_cli("send", "nieznana", "x")
        self.assertEqual(code, 2)
        self.assertIn("nieznana", err)
        self.assertEqual(self.run_cli("send", "all", "x")[0], 2)

    def test_file_rules(self):
        ok = self.tmp / "lb.png"
        ok.write_bytes(b"x" * 86016)
        code, out, _ = self.run_cli("send", "self", "wykres", "--file", str(ok))
        self.assertEqual(code, 0)
        self.assertIn("--attachment", self.argv())
        self.assertIn("(📎 lb.png, 84 KB)", out)
        big = self.tmp / "big.bin"
        with big.open("wb") as f:
            f.truncate(100 * 1024 * 1024 + 1)
        bad = [big, self.home / ".ssh/id", self.home / ".config/x", self.home / ".local/share/signal-cli/x",
               self.tmp / "a.env", self.tmp / ".env", self.tmp / ".env.local", self.tmp / "k.pem", self.tmp / "k.key",
               self.tmp / "missing.txt"]
        for p in bad[1:-1]:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("s")
        calls = len(self.run.calls)
        for p in bad:
            code, _, _ = self.run_cli("send", "self", "x", "--file", str(p))
            self.assertEqual(code, 2, p)
        self.assertEqual(len(self.run.calls), calls)

    def test_file_without_text(self):
        f = self.tmp / "a.csv"
        f.write_text("1")
        self.assertEqual(self.run_cli("send", "self", "--file", str(f))[0], 0)
        self.assertEqual(self.argv()[self.argv().index("-m") + 1], "🤖 [house]")

    def test_empty_message_refused(self):
        self.assertEqual(self.run_cli("send", "self")[0], 2)

    def test_failed_send(self):
        self.run.rc = 1
        code, out, _ = self.run_cli("send", "self", "x")
        self.assertEqual(code, 1)
        self.assertIn("✗ nie wysłano", out)

    def test_not_configured(self):
        with mock.patch("houselib.signal.from_config", return_value=None):
            code, _, err = self.run_cli("send", "self", "x")
        self.assertEqual(code, 1)
        self.assertIn("Signal nie jest skonfigurowany", err)
