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
