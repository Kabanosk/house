"""Signal at session start: summary lines in contests and in the house repo, never moving the unread cursor."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from houselib import adapters, hook, scaffold, signalchats, signalread, signalstore
from tests import signal_fixtures as fx
from tests.helpers import FAKE_TOOLS, git, make_root
from tests.test_signalstore import FakeSig


def now_ts(minutes_ago: float = 0) -> int:
    return int((datetime.now() - timedelta(minutes=minutes_ago)).timestamp() * 1000)


class SignalHookTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.env = mock.patch.dict(os.environ, {"HOUSE_DATA_DIR": str(self.tmp / "data"),
                                                "HOUSE_SIGNAL_CONFIG": str(self.tmp / "signal.toml")})
        self.env.start()
        signalchats.save_config({"techarena": {"id": fx.GID, "name": "techarena_claude", "added": "x"}})
        self.root = make_root(self.tmp)
        git(self.root, "commit", "-q", "--allow-empty", "-m", "base")
        self.incoming = [fx.group_in(now_ts(5), "Spoko, a jaki wynik ma teraz baseline?" + " bardzo" * 20,
                                     quote=(now_ts(30), "u-own", "🤖 [house] Cześć")),
                         fx.group_in(now_ts(4), "drugie", name="Ania K.", uuid="u-ania"),
                         fx.self_note(now_ts(3), "fr Q-012: B"),
                         fx.group_in(now_ts(2), "czwarte", name="Ola", uuid="u-ola")]
        self.sig = FakeSig(self.incoming)
        self.sig.list_groups = list
        self.sig.send = lambda *a, **k: 1
        self.patch = mock.patch("houselib.signal.from_config", return_value=self.sig)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.env.stop()
        self._tmp.cleanup()

    def start(self, cwd: Path) -> str:
        return hook.session_start({"cwd": str(cwd)})

    def test_house_repo_session_shows_signal_only(self):
        out = self.start(self.root)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("signal: 4 nowe (techarena 3, self 1) · `house signal unread` · "
                                            "ostatni odbiór "), lines)
        self.assertEqual(len(lines), 4)
        self.assertTrue(lines[1].startswith("signal:   techarena ["), lines[1])
        self.assertIn("Ola: czwarte", lines[1])
        self.assertIn("Wojciech Fiołka: fr Q-012: B", lines[2])
        self.assertNotIn("contest", out)

    def test_signal_lines_limits(self):
        for line in self.start(self.root).splitlines():
            self.assertLessEqual(len(line), 100, line)

    def test_house_worktree_counts_as_house(self):
        wt = self.tmp / "wt"
        git(self.root, "worktree", "add", "-q", str(wt), "-b", "x")
        self.assertIn("signal: 4 nowe", self.start(wt))

    def test_hook_does_not_move_cursor(self):
        self.start(self.root)
        msgs, _ = signalread.unread("house", peek=True)
        self.assertEqual(len(msgs), 4)

    def test_no_new_line(self):
        self.start(self.root)
        signalread.unread("house")
        out = self.start(self.root)
        self.assertRegex(out.strip(), r"^signal: brak nowych · ostatni odbiór \d\d:\d\d$")

    def test_not_configured_prints_nothing(self):
        with mock.patch("houselib.signal.from_config", return_value=None):
            self.assertEqual(self.start(self.root), "")

    def test_hook_fail_open(self):
        with mock.patch("houselib.inbox.process", side_effect=RuntimeError("boom")):
            self.assertEqual(self.start(self.root).strip(), "signal: pominięto (RuntimeError)")

    def test_unrelated_directory_is_silent(self):
        other = self.tmp / "elsewhere"
        other.mkdir()
        self.assertEqual(self.start(other), "")

    def test_contest_session_total_limit(self):
        c = scaffold.new_contest("c1", None, root=self.root, detect=lambda: FAKE_TOOLS)
        out = self.start(c)
        lines = out.splitlines()
        self.assertLessEqual(len(lines), 25)
        self.assertTrue(lines[0].startswith("house: contest c1"))
        self.assertEqual(sum(1 for x in lines if x.startswith("signal:")), 4)
        self.assertEqual(signalread.reader_name(c), "c1")

    def test_write_house_settings_merges(self):
        path = self.root / ".claude/settings.local.json"
        path.parent.mkdir()
        path.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "mine"}]}]},
                                    "model": "x"}))
        adapters.write_house_settings(self.root)
        s = json.loads(path.read_text())
        commands = [h["command"] for e in s["hooks"]["SessionStart"] for h in e["hooks"]]
        self.assertEqual(commands[0], "mine")
        self.assertTrue(commands[1].endswith("bin/house-hook session-start"))
        self.assertIn("pre-tool", s["hooks"]["PreToolUse"][0]["hooks"][0]["command"])
        self.assertNotIn("PostToolUse", s["hooks"])
        self.assertEqual(s["model"], "x")
        adapters.write_house_settings(self.root)
        s = json.loads(path.read_text())
        self.assertEqual(len([h for e in s["hooks"]["SessionStart"] for h in e["hooks"]]), 2)
        self.assertIn(".claude/settings.local.json", (self.root / ".git/info/exclude").read_text())

    def test_store_written_before_summary(self):
        self.start(self.root)
        self.assertEqual(len(list(signalstore.iter_events())), 4)


if __name__ == "__main__":
    unittest.main()


class SetupSignalHookTest(unittest.TestCase):
    def test_setup_signal_hook_only_writes_settings(self):
        import contextlib
        import io

        from houselib import cli
        with tempfile.TemporaryDirectory() as d:
            root = make_root(Path(d))
            out = io.StringIO()
            with mock.patch.object(cli, "HOUSE_ROOT", root), mock.patch("houselib.setup.install_user_skills") as inst, \
                    contextlib.redirect_stdout(out):
                self.assertEqual(cli.main(["setup", "--signal-hook"]), 0)
            inst.assert_not_called()
            self.assertTrue((root / ".claude/settings.local.json").exists())
            self.assertIn("settings.local.json", out.getvalue())
