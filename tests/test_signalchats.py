"""`houselib.signalchats`: which Signal chats are allowed (Note to Self, running contests' groups, signal.toml)."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from houselib import notify, signalchats
from tests import helpers  # noqa: F401  (never the real Signal account)


class ChatsCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.env = mock.patch.dict(os.environ, {"HOUSE_DATA_DIR": str(self.tmp / "data"),
                                                "HOUSE_SIGNAL_CONFIG": str(self.tmp / "cfg/signal.toml")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self._tmp.cleanup()

    def contest(self, name, alias, group, end="null"):
        path = self.tmp / name
        (path / ".house").mkdir(parents=True)
        (path / ".house/spec.yaml").write_text(f"timeline:\n  end: {end}\nnotify:\n  alias: {alias}\n"
                                               f"  signal_group: {group}\n")
        return path

    def cache(self, **groups):
        (notify.data_dir() / "groups.json").write_text(json.dumps(groups))


class AllowedTest(ChatsCase):
    def test_allowed_self_and_contest_groups(self):
        fr = self.contest("fr-2026", "fr", "Team FR")
        old = self.contest("old", "old", "Old team", end="2020-01-01")
        self.cache(**{"Team FR": "gFR", "Old team": "gOLD"})
        chats = signalchats.allowed(None, [fr, old])
        self.assertEqual(set(chats), {"self", "gFR"})
        self.assertEqual((chats["gFR"].alias, chats["gFR"].name, chats["gFR"].source, chats["gFR"].contests),
                         ("fr", "Team FR", "konkurs fr-2026", (fr,)))
        self.assertEqual((chats["self"].alias, chats["self"].source), ("self", "zawsze"))

    def test_one_group_two_contests(self):
        fr = self.contest("fr-2026", "fr", "Team")
        fi = self.contest("fi-2026", "fi", "Team")
        self.cache(Team="gT")
        chat = signalchats.allowed(None, [fr, fi])["gT"]
        self.assertEqual(chat.contests, (fr, fi))
        self.assertEqual(chat.alias, "fr")

    def test_without_sig_never_calls_signal_cli(self):
        fr = self.contest("fr-2026", "fr", "Unknown")
        self.assertEqual(set(signalchats.allowed(None, [fr])), {"self"})

    def test_with_sig_resolves_group(self):
        fr = self.contest("fr-2026", "fr", "Team")

        class Sig:
            def list_groups(self):
                return [{"id": "gT", "name": "Team"}]
        self.assertIn("gT", signalchats.allowed(Sig(), [fr]))

    def test_alias_of(self):
        fr = self.contest("fr-2026", "fr", "Team")
        self.cache(Team="gT")
        with mock.patch("houselib.inbox.all_running", return_value=[fr]):
            self.assertEqual(signalchats.alias_of("gT"), "fr")
            self.assertEqual(signalchats.alias_of("self"), "self")
            self.assertIsNone(signalchats.alias_of("gX"))


if __name__ == "__main__":
    unittest.main()
