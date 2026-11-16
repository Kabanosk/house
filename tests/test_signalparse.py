"""`houselib.signalparse`: one signal-cli envelope → one store event, privacy filter before anything is kept."""
import unittest
from datetime import datetime

from houselib import signalparse
from tests import helpers  # noqa: F401  (never the real Signal account)
from tests import signal_fixtures as fx

CHATS = {"self": "self", "gT": "techarena"}
NOW = datetime(2026, 11, 16, 12, 40)


def ex(obj):
    return signalparse.extract(obj, fx.OWN, CHATS, now=NOW)


class ExtractTest(unittest.TestCase):
    def test_group_message_from_teammate(self):
        obj = fx.group_in(1000, "Spoko, a jaki wynik?")
        ev, dropped = ex(obj)
        self.assertFalse(dropped)
        self.assertEqual((ev["v"], ev["kind"], ev["dir"], ev["chat"], ev["chat_alias"]),
                         (1, "message", "in", "gT", "techarena"))
        self.assertEqual(ev["sender"], {"name": "Kuba Nowak", "number": "+48999", "uuid": "u-kuba"})
        self.assertEqual(ev["key"], "gT:u-kuba:1000")
        self.assertEqual(ev["ts"], 1000)
        self.assertEqual(ev["text"], "Spoko, a jaki wynik?")
        self.assertEqual(datetime.fromisoformat(ev["time"]).timestamp(), 1.0)
        self.assertIsNotNone(datetime.fromisoformat(ev["time"]).tzinfo)
        self.assertEqual(ev["received_at"], NOW.astimezone().isoformat(timespec="seconds"))
        self.assertEqual(ev["raw"], obj)
        self.assertEqual((ev["status"], ev["quote"], ev["target"], ev["meta"], ev["expires_in"]),
                         ("ok", None, None, None, 0))
        self.assertEqual((ev["mentions"], ev["attachments"]), ([], []))
        self.assertNotIn("seq", ev)

    def test_quote_text_and_author_kept(self):
        ev, _ = ex(fx.group_in(1000, "ok", quote=(100, "u-own", "🤖 [house] Cześć")))
        self.assertEqual(ev["quote"], {"ts": 100, "author": "u-own", "text": "🤖 [house] Cześć"})

    def test_quote_author_by_number(self):
        ev, _ = ex(fx.group_in(1000, "ok", quote=(100, "+48111", None)))
        self.assertEqual(ev["quote"], {"ts": 100, "author": "+48111", "text": None})

    def test_note_to_self_is_own(self):
        ev, _ = ex(fx.self_note(1000, "fr Q-012: B"))
        self.assertEqual((ev["chat"], ev["chat_alias"], ev["dir"], ev["key"]),
                         ("self", "self", "own", "self:u-own:1000"))
        self.assertEqual(ev["sender"]["name"], fx.OWN_NAME)

    def test_own_message_in_group_is_own(self):
        ev, _ = ex(fx.group_own(1000, "hej"))
        self.assertEqual((ev["chat"], ev["dir"], ev["kind"]), ("gT", "own", "message"))

    def test_attachment_only_message(self):
        obj = fx.group_in(1000, None, attachments=[fx.attachment()])
        del obj["envelope"]["dataMessage"]["message"]
        ev, _ = ex(obj)
        self.assertEqual((ev["kind"], ev["text"]), ("message", ""))
        self.assertEqual(ev["attachments"], [{"content_type": "image/jpeg", "filename": "a.jpg", "size": 84000,
                                              "id": "x.jpg", "path": None}])

    def test_mention_replaced(self):
        obj = fx.group_in(1000, "￼ zobacz ￼", mentions=[
            {"name": "Ola", "number": "+1", "uuid": "u1", "start": 9, "length": 1},
            {"name": "Ania", "number": "+2", "uuid": "u2", "start": 0, "length": 1}])
        ev, _ = ex(obj)
        self.assertEqual(ev["text"], "@Ania zobacz @Ola")
        self.assertEqual([m["name"] for m in ev["mentions"]], ["Ania", "Ola"])
        self.assertIn("￼", ev["raw"]["envelope"]["dataMessage"]["message"])

    def test_reaction(self):
        ev, _ = ex(fx.reaction(1000, "👍", 100, "u-own"))
        self.assertEqual((ev["kind"], ev["text"]), ("reaction", ""))
        self.assertEqual(ev["reaction"], {"emoji": "👍", "remove": False})
        self.assertEqual(ev["target"], {"ts": 100, "author": "u-own"})

    def test_reaction_by_number_target(self):
        ev, _ = ex(fx.reaction(1000, "❤️", 100, "+48111"))
        self.assertEqual(ev["target"], {"ts": 100, "author": "+48111"})

    def test_reaction_remove(self):
        ev, _ = ex(fx.reaction(1000, "👍", 100, "u-own", remove=True))
        self.assertEqual(ev["reaction"], {"emoji": "👍", "remove": True})

    def test_reaction_in_note_to_self(self):
        ev, _ = ex(fx.reaction(1000, "❤️", 100, "+48111", gid="self"))
        self.assertEqual((ev["chat"], ev["dir"], ev["kind"]), ("self", "own", "reaction"))

    def test_edit(self):
        ev, _ = ex(fx.edit(1100, 1000, "poprawione"))
        self.assertEqual((ev["kind"], ev["text"], ev["ts"], ev["chat"]), ("edit", "poprawione", 1100, "gT"))
        self.assertEqual(ev["target"], {"ts": 1000, "author": "u-kuba"})

    def test_own_edit_through_sync(self):
        obj = fx.group_own(1100, "x")
        sent = obj["envelope"]["syncMessage"]["sentMessage"]
        data = {k: sent.pop(k) for k in ("timestamp", "message", "groupInfo")}
        sent["editMessage"] = {"targetSentTimestamp": 1000, "dataMessage": data}
        ev, _ = ex(obj)
        self.assertEqual((ev["kind"], ev["dir"], ev["target"]), ("edit", "own", {"ts": 1000, "author": "u-own"}))

    def test_remote_delete(self):
        ev, _ = ex(fx.delete(1100, 1000))
        self.assertEqual((ev["kind"], ev["target"]), ("delete", {"ts": 1000, "author": "u-kuba"}))

    def test_sticker_is_other(self):
        ev, _ = ex(fx.sticker(1000))
        self.assertEqual((ev["kind"], ev["label"]), ("other", "[naklejka]"))

    def test_group_update_is_other(self):
        obj = fx.sticker(1000)
        del obj["envelope"]["dataMessage"]["sticker"]
        obj["envelope"]["dataMessage"]["groupInfo"]["type"] = "UPDATE"
        ev, _ = ex(obj)
        self.assertEqual(ev["label"], "[zmiana grupy]")

    def test_expiring(self):
        ev, _ = ex(fx.group_in(1000, "znika", expires=3600))
        self.assertEqual(ev["expires_in"], 3600)

    def test_dm_and_other_group_dropped(self):
        self.assertEqual(ex(fx.dm(1000, "sekret")), (None, True))
        self.assertEqual(ex(fx.group_in(1000, "x", gid="gX")), (None, True))
        self.assertEqual(ex(fx.dm(1000, None, attachments=[fx.attachment()])), (None, True))

    def test_own_dm_dropped(self):
        obj = fx.self_note(1000, "do kolegi")
        sent = obj["envelope"]["syncMessage"]["sentMessage"]
        sent["destination"] = sent["destinationNumber"] = "+48777"
        sent["destinationUuid"] = "u-friend"
        self.assertEqual(ex(obj), (None, True))

    def test_receipt_and_typing_ignored(self):
        self.assertEqual(ex(fx.receipt(1000)), (None, False))
        self.assertEqual(ex(fx.typing(1000)), (None, False))
        self.assertEqual(ex({"envelope": {"syncMessage": {"readMessages": [{"timestamp": 1}]}}}), (None, False))
        self.assertEqual(ex({"envelope": {}}), (None, False))
        self.assertEqual(ex({}), (None, False))

    def test_empty_data_message_ignored(self):
        obj = fx.group_in(1000, None)
        del obj["envelope"]["dataMessage"]["message"]
        self.assertEqual(ex(obj), (None, False))

    def test_privacy_no_trace(self):
        result = ex(fx.dm(1000, "UNIKALNY-sekret-42", number="+48555000111", name="Tajny Ktoś"))
        text = repr(result)
        for needle in ("UNIKALNY", "+48555000111", "Tajny"):
            self.assertNotIn(needle, text)

    def test_malformed_types_raise_value_or_type_error(self):
        obj = fx.group_in(1000, "x", quote=(100, "u-own", "q"))
        obj["envelope"]["dataMessage"]["quote"]["id"] = "abc"
        with self.assertRaises((TypeError, ValueError, AttributeError)):
            ex(obj)


if __name__ == "__main__":
    unittest.main()
