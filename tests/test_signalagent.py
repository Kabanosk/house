"""`houselib.signalagent`: @claude requests from Signal (parsing, routing, jobs, launch, tick, replies)."""
import unittest
from datetime import datetime

from houselib import signalagent as agent
from houselib import signalparse
from tests import helpers  # noqa: F401  (never the real Signal account)
from tests import signal_fixtures as fx

CHATS = {"self": "self", fx.GID: "techarena"}
NOW = datetime(2026, 11, 16, 12, 40)


def ev(obj, seq=1):
    e, _ = signalparse.extract(obj, fx.OWN, CHATS, now=NOW)
    e["seq"] = seq
    return e


class ParseTest(unittest.TestCase):
    def test_trigger_positions(self):
        for text in ("@claude popraw testy", "hej @claude popraw testy", "popraw testy @claude", "@Claude x",
                     "@CLAUDE x", "(@claude) x"):
            self.assertIsInstance(agent.parse(ev(fx.group_in(1000, text))), agent.Request, text)
        for text in ("napisz na email@claude.ai", "x@claude", "@claudette", "claude zrób"):
            self.assertIsNone(agent.parse(ev(fx.group_in(1000, text))), text)

    def test_request_fields(self):
        obj = fx.group_in(1000, "@claude popraw testy", quote=(900, "u-own", "🤖 [fr] wynik"))
        obj["envelope"]["serverReceivedTimestamp"] = 1234
        r = agent.parse(ev(obj))
        self.assertEqual((r.key, r.chat, r.chat_alias, r.ts, r.author, r.author_name, r.own),
                         (f"{fx.GID}:u-kuba:1000", fx.GID, "techarena", 1000, "u-kuba", "Kuba Nowak", False))
        self.assertEqual((r.text, r.alias_hint, r.quote_ts, r.received_ms), ("popraw testy", None, 900, 1234))

    def test_mention_placeholder_is_not_a_trigger(self):
        obj = fx.group_in(1000, "￼ zobacz to", mentions=[{"name": "claude", "number": "+1", "uuid": "u1",
                                                               "start": 0, "length": 1}])
        e = ev(obj)
        self.assertEqual(e["text"], "@claude zobacz to")
        self.assertIsNone(agent.parse(e))

    def test_quote_text_does_not_trigger(self):
        self.assertIsNone(agent.parse(ev(fx.group_in(1000, "ok", quote=(900, "u-x", "@claude zrób")))))

    def test_house_messages_never_trigger(self):
        own_house = ev(fx.group_own(1000, "🤖 [house] przyjąłem: @claude popraw testy"))
        self.assertIsNone(agent.parse(own_house))
        out = {"v": 1, "seq": 2, "key": "gT:+48111:5", "kind": "message", "dir": "out", "chat": fx.GID,
               "chat_alias": "techarena", "ts": 5, "text": "🤖 [house] @claude", "raw": None,
               "sender": {"name": "house", "number": fx.OWN, "uuid": None}}
        self.assertIsNone(agent.parse(out))

    def test_edit_never_triggers(self):
        self.assertIsNone(agent.parse(ev(fx.edit(1100, 1000, "@claude zrób"))))

    def test_alias_hint(self):
        r = agent.parse(ev(fx.group_in(1000, "@claude fr: popraw X")))
        self.assertEqual((r.alias_hint, r.text), ("fr", "popraw X"))
        r = agent.parse(ev(fx.group_in(1000, "@claude Uwaga: zrób X")))
        self.assertEqual((r.alias_hint, r.text), ("uwaga", "zrób X"))
        self.assertEqual(r.full_text, "Uwaga: zrób X")

    def test_own_request_in_self(self):
        r = agent.parse(ev(fx.self_note(1000, "@claude sprawdź testy")))
        self.assertTrue(r.own)
        self.assertEqual(r.author, "u-own")

    def test_commands(self):
        c = agent.parse(ev(fx.group_in(1000, "/status")))
        self.assertEqual((type(c), c.name, c.chat, c.own), (agent.Command, "status", fx.GID, False))
        self.assertEqual(agent.parse(ev(fx.group_in(1000, " /LB "))).name, "lb")
        c = agent.parse(ev(fx.group_in(1000, "/stop", quote=(900, "u-own", "🤖 przyjąłem"))))
        self.assertEqual((c.name, c.quote_ts), ("stop", 900))
        self.assertEqual(agent.parse(ev(fx.self_note(1000, "/agent off"))).name, "agent-off")
        self.assertEqual(agent.parse(ev(fx.self_note(1000, "/agent on"))).name, "agent-on")
        self.assertIsNone(agent.parse(ev(fx.group_in(1000, "/agent off"))))
        self.assertIsNone(agent.parse(ev(fx.group_in(1000, "/statusy"))))

    def test_reaction_and_other_kinds_ignored(self):
        self.assertIsNone(agent.parse(ev(fx.reaction(1000, "👍", 900, "u-own"))))
        self.assertIsNone(agent.parse(ev(fx.sticker(1000))))


if __name__ == "__main__":
    unittest.main()
