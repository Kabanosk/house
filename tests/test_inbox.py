"""`houselib.inbox`: answers from Signal (quote-replies, Q-### text), first answer wins, private chats dropped."""
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from houselib import inbox, notify, signalstore
from tests import helpers  # noqa: F401  (never the real Signal account)

OWN = "+48111"
QS = ("# Q\n\n## Q-001 [open] Finalne zgłoszenie?\n- **Opcje:** A) x B) y\n- **Blokuje:** nic\n"
      "- **Odpowiedź:** —\n\n## Q-003 [open] Batch size?\n- **Odpowiedź:** —\n")


def self_reply(ts, text, quote=None):
    sm = {"destinationNumber": OWN, "timestamp": ts, "message": text}
    if quote:
        sm["quote"] = {"id": quote}
    return {"envelope": {"sourceNumber": OWN, "sourceName": "Wojciech Fiołka", "timestamp": ts,
                         "syncMessage": {"sentMessage": sm}}}


def group_msg(ts, text, gid, name="Kuba Nowak", quote=None, own=False):
    m = {"timestamp": ts, "message": text, "groupInfo": {"groupId": gid}}
    if quote:
        m["quote"] = {"id": quote}
    if own:
        return {"envelope": {"sourceNumber": OWN, "sourceName": "Wojciech Fiołka", "timestamp": ts,
                             "syncMessage": {"sentMessage": {**m, "destinationNumber": None}}}}
    return {"envelope": {"sourceNumber": "+48999", "sourceName": name, "timestamp": ts, "dataMessage": m}}


def self_edit(ts, target_ts, text):
    return {"envelope": {"sourceNumber": OWN, "sourceName": "Wojciech Fiołka", "timestamp": ts,
                         "syncMessage": {"sentMessage": {"destinationNumber": OWN, "editMessage": {
                             "targetSentTimestamp": target_ts, "dataMessage": {"timestamp": ts, "message": text}}}}}}


def dm(ts, text):
    return {"envelope": {"sourceNumber": "+48777", "sourceName": "Friend", "timestamp": ts,
                         "dataMessage": {"timestamp": ts, "message": text}}}


class FakeSignal:
    def __init__(self, incoming, groups=None):
        self.account, self.incoming, self.sent, self.groups, self.ts = OWN, incoming, [], groups or [], 5000
        self.metas = []

    attachments_dir = Path("/nonexistent")

    def receive(self, handle, timeout=1, max_messages=None, should_stop=lambda: False):
        from houselib.signal import ReceiveResult
        out, self.incoming = self.incoming, []
        for obj in out:
            handle(obj)
        return ReceiveResult(objects=len(out))

    def send(self, text, group_id=None, meta=None, **kw):
        self.ts += 1
        self.sent.append((group_id, text))
        self.metas.append(meta)
        return self.ts

    def list_groups(self):
        return self.groups


class InboxCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.env = mock.patch.dict(os.environ, {"HOUSE_DATA_DIR": str(tmp / "data")})
        self.env.start()
        self.fr = self.contest(tmp / "fr-2026", "fr", "Team")
        self.fi = self.contest(tmp / "fi-2026", "fi", "Team")
        self.groups = [{"id": "gT", "name": "Team"}]

    def tearDown(self):
        self.env.stop()
        self._tmp.cleanup()

    def contest(self, path: Path, alias: str, group: str, end: str = "null") -> Path:
        (path / ".house/tasks").mkdir(parents=True)
        (path / ".house/QUESTIONS.md").write_text(QS)
        (path / ".house/DECISIONS.md").write_text("# D\n")
        (path / ".house/state.json").write_text("{}")
        (path / ".house/spec.yaml").write_text(f"timeline:\n  end: {end}\nnotify:\n  alias: {alias}\n"
                                               f"  signal_group: {group}\n")
        return path

    def qtext(self, c: Path) -> str:
        return (c / ".house/QUESTIONS.md").read_text()

    def sent_question(self, c: Path, qid: str, ts: int, chat="self"):
        signalstore.record_out(OWN, chat, f"🤖 [x] {qid} pytanie", ts,
                               meta={"origin": "ask", "contest": notify.contest_name(c), "question": qid})

    def run_inbox(self, incoming, contests=None):
        sig = FakeSignal(incoming, self.groups)
        log = inbox.process(contests or [self.fr, self.fi], sig)
        return sig, log

class InboxTest(InboxCase):
    def test_quote_reply_in_note_to_self(self):
        self.sent_question(self.fr, "Q-001", 100)
        sig, _ = self.run_inbox([self_reply(200, "B", quote=100)])
        self.assertIn("- **Odpowiedź:** B (Wojciech Fiołka, Signal)", self.qtext(self.fr))
        self.assertIn("[open] Finalne", self.qtext(self.fi))
        self.assertEqual(sig.sent, [(None, "🤖 [fr] ✓ Q-001: B (Wojciech Fiołka)")])

    def test_inbox_confirmation_meta(self):
        self.sent_question(self.fr, "Q-001", 100)
        sig, _ = self.run_inbox([self_reply(200, "B", quote=100)])
        self.assertEqual(sig.metas, [{"origin": "inbox-reply", "contest": "fr-2026"}])

    def test_deadline_messages_meta(self):
        (self.fr / ".house/QUESTIONS.md").write_text(
            "# Q\n\n## Q-005 [open] Finalne?\n- **Poziom:** krytyczny\n- **Termin:** 2026-11-20 23:50\n"
            "- **Domyślnie:** B\n- **Odpowiedź:** —\n")
        (self.fr / ".house/state.json").write_text(json.dumps({"sent_questions": {"Q-005": "2026-11-20T21:50:00"}}))
        sig = FakeSignal([], self.groups)
        inbox.deadlines(self.fr, sig, now=datetime(2026, 11, 20, 23, 0))
        self.assertTrue(sig.metas)
        self.assertTrue(all(m["origin"] == "deadline" for m in sig.metas), sig.metas)

    def test_teammate_answers_in_group_first_wins(self):
        self.sent_question(self.fr, "Q-001", 100, chat="gT")
        sig, _ = self.run_inbox([group_msg(300, "A", "gT", quote=100),
                                 group_msg(250, "B", "gT", name="Ola", quote=100)])
        self.assertIn("B (Ola, Signal)", self.qtext(self.fr))
        self.assertIn(("gT", "🤖 [fr] ✓ Q-001: B (Ola)"), sig.sent)
        self.assertTrue(any("już rozstrzygnięte" in t for _, t in sig.sent))

    def test_own_message_in_group_with_alias(self):
        sig, _ = self.run_inbox([group_msg(300, "fi Q-3: 64", "gT", own=True)])
        self.assertIn("64 (Wojciech Fiołka, Signal)", self.qtext(self.fi))
        self.assertIn("[open] Batch", self.qtext(self.fr))

    def test_ambiguous_without_alias_asks(self):
        sig, _ = self.run_inbox([group_msg(300, "Q3: 64", "gT")])
        self.assertIn("[open] Batch", self.qtext(self.fr))
        self.assertIn("[open] Batch", self.qtext(self.fi))
        self.assertTrue(any("fr Q-003" in t and "fi Q-003" in t for _, t in sig.sent), sig.sent)

    def test_private_chats_dropped_and_not_stored(self):
        self.run_inbox([dm(300, "Q-001: A"), group_msg(301, "Q-001: A", "gOther")])
        self.assertIn("[open] Finalne", self.qtext(self.fr))
        blobs = [p.read_bytes() for p in notify.data_dir().rglob("*") if p.is_file()]
        self.assertFalse(any(b"Friend" in b or b"gOther" in b for b in blobs))

    def test_group_chatter_is_not_an_answer(self):
        sig, _ = self.run_inbox([group_msg(300, "q1 wyniki wyglądają ok", "gT")])
        self.assertIn("[open] Finalne", self.qtext(self.fr))
        self.assertEqual(sig.sent, [])

    def test_unknown_quote_and_no_reference_ignored(self):
        sig, log = self.run_inbox([self_reply(300, "ok", quote=999), self_reply(301, "just a note")])
        self.assertEqual(sig.sent, [])
        self.assertIn("[open] Finalne", self.qtext(self.fr))

    def test_pull_without_running_contest(self):
        old = self.contest(Path(self._tmp.name) / "old", "old", "Team", end="2020-01-01")
        sig = FakeSignal([self_reply(200, "notatka")], self.groups)
        self.assertEqual(inbox.process([old], sig), [])
        self.assertEqual(sig.incoming, [])
        self.assertEqual([e["text"] for e in signalstore.iter_events()], ["notatka"])

    def test_toml_group_stored_not_routed(self):
        from houselib import signalchats
        with mock.patch.dict(os.environ, {"HOUSE_SIGNAL_CONFIG": str(Path(self._tmp.name) / "signal.toml")}):
            signalchats.save_config({"techarena": {"id": "gTA", "name": "techarena_claude", "added": "x"}})
            sig, log = self.run_inbox([group_msg(300, "Q-001: A", "gTA")])
        self.assertIn("[open] Finalne", self.qtext(self.fr))
        self.assertEqual(sig.sent, [])
        self.assertEqual([e["chat"] for e in signalstore.iter_events() if e["dir"] == "in"], ["gTA"])

    def test_done_key_is_chat_author_ts(self):
        self.sent_question(self.fr, "Q-001", 100)
        self.run_inbox([self_reply(200, "B", quote=100), self_reply(201, "zwykła notatka")])
        done = json.loads((signalstore.store_dir() / "inbox_done.json").read_text())
        self.assertEqual(done, {f"self:{OWN}:200": "fr-2026 Q-001", f"self:{OWN}:201": None})

    def test_edit_before_handling_uses_new_text(self):
        self.sent_question(self.fr, "Q-001", 100)
        sig = FakeSignal([self_reply(200, "A", quote=100)], self.groups)
        inbox.process([self.fr, self.fi], sig, scope=[self.fi])
        self.assertIn("[open] Finalne", self.qtext(self.fr))
        self.run_inbox([self_edit(210, 200, "B")])
        self.assertIn("- **Odpowiedź:** B (Wojciech Fiołka, Signal)", self.qtext(self.fr))

    def test_edit_kept_while_original_waits_for_its_contest(self):
        self.sent_question(self.fr, "Q-001", 100)
        sig = FakeSignal([self_reply(200, "A", quote=100), self_edit(210, 200, "B")], self.groups)
        inbox.process([self.fr, self.fi], sig, scope=[self.fi])
        self.assertIn("[open] Finalne", self.qtext(self.fr))
        self.run_inbox([])
        self.assertIn("- **Odpowiedź:** B (Wojciech Fiołka, Signal)", self.qtext(self.fr))

    def test_edit_after_handling_logged(self):
        self.sent_question(self.fr, "Q-001", 100)
        self.run_inbox([self_reply(200, "A", quote=100)])
        sig, log = self.run_inbox([self_edit(210, 200, "B")])
        self.assertIn("A (Wojciech Fiołka, Signal)", self.qtext(self.fr))
        self.assertIn("Q-001: odpowiedź edytowana po zapisaniu, decyzja bez zmian", log)
        self.assertEqual(sig.sent, [])
        sig, log = self.run_inbox([])
        self.assertEqual(log, [])

    def test_unread_line(self):
        from houselib import signalchats
        with mock.patch.dict(os.environ, {"HOUSE_SIGNAL_CONFIG": str(Path(self._tmp.name) / "signal.toml")}):
            signalchats.save_config({"techarena": {"id": "gTA", "name": "techarena_claude", "added": "x"}})
            sig = FakeSignal([group_msg(int(datetime.now().timestamp() * 1000), "cześć", "gTA"),
                              self_reply(int(datetime.now().timestamp() * 1000) + 1, "notatka")], self.groups)
            log = inbox.process([self.fr], sig, reader="proj")
        self.assertIn("signal: 1 nowa w grupach (house signal unread)", log)

    def test_old_inbox_file_is_migrated_and_answered(self):
        self.sent_question(self.fr, "Q-001", 100)
        (notify.data_dir() / "inbox.jsonl").write_text(json.dumps(
            {"ts": 150, "chat": "self", "sender": "Wojciech Fiołka", "text": "B", "quote_ts": 100}) + "\n")
        self.run_inbox([])
        self.assertIn("- **Odpowiedź:** B (Wojciech Fiołka, Signal)", self.qtext(self.fr))
        self.assertFalse((notify.data_dir() / "inbox.jsonl").exists())

    def test_ended_contest_skipped(self):
        old = self.contest(Path(self._tmp.name) / "old", "fr", "Team", end="2020-01-01")
        self.assertEqual(inbox.running([self.fr, old], now=datetime(2026, 11, 13)), [self.fr])

    def test_messages_handled_once(self):
        self.sent_question(self.fr, "Q-001", 100)
        self.run_inbox([self_reply(200, "B", quote=100)])
        sig, _ = self.run_inbox([])
        self.assertEqual(sig.sent, [])

    def test_deadlines_apply_default_and_remind(self):
        (self.fr / ".house/QUESTIONS.md").write_text(
            "# Q\n\n## Q-005 [open] Finalne?\n- **Poziom:** krytyczny\n- **Termin:** 2026-11-20 23:50\n"
            "- **Domyślnie:** B\n- **Odpowiedź:** —\n")
        (self.fr / ".house/state.json").write_text(json.dumps({"sent_questions": {"Q-005": "2026-11-20T21:50:00"}}))
        sig = FakeSignal([], self.groups)
        inbox.deadlines(self.fr, sig, now=datetime(2026, 11, 20, 23, 0))
        self.assertTrue(any("Q-005" in t and "23:50" in t for _, t in sig.sent))
        sig.sent.clear()
        inbox.deadlines(self.fr, sig, now=datetime(2026, 11, 20, 23, 55))
        self.assertIn("B (judge, domyślnie", self.qtext(self.fr))
        self.assertIn("judge (domyślnie, brak odpowiedzi do 23:50)", (self.fr / ".house/DECISIONS.md").read_text())
        self.assertTrue(any("domyślnie" in t for _, t in sig.sent))


    def test_pull_for_one_contest_keeps_answers_for_others(self):
        (self.fi / ".house/spec.yaml").write_text("timeline:\n  end: null\nnotify:\n  alias: fi\n  signal_group: TeamB\n")
        self.groups.append({"id": "gB", "name": "TeamB"})
        sig = FakeSignal([group_msg(300, "Q-001: B", "gB"), self_reply(301, "fi Q-3: X")], self.groups)
        inbox.process([self.fr, self.fi], sig, scope=[self.fr])
        self.assertIn("[open] Finalne", self.qtext(self.fi))
        inbox.process([self.fr, self.fi], FakeSignal([], self.groups), scope=[self.fi])
        self.assertIn("B (Kuba Nowak, Signal)", self.qtext(self.fi))
        self.assertIn("X (Wojciech Fiołka, Signal)", self.qtext(self.fi))

    def test_symlinked_contest_is_identified_by_registry_name(self):
        from houselib import registry
        root = Path(self._tmp.name) / "house"
        (root / "contests").mkdir(parents=True)
        (root / "contests.yaml").write_text("nice-name: \n")
        (root / "contests/nice-name").symlink_to(self.fr)
        with mock.patch.object(registry, "DEFAULT_ROOT", root):
            link = root / "contests/nice-name"
            self.assertEqual(notify.contest_name(link), "nice-name")
            self.assertEqual(notify.contest_name(self.fr), "nice-name")
            self.sent_question(link, "Q-001", 100)
            inbox.process([self.fr.resolve()], FakeSignal([self_reply(200, "B", quote=100)], self.groups))
        self.assertIn("B (Wojciech Fiołka, Signal)", self.qtext(self.fr))

    def test_backslash_and_newlines_in_answers(self):
        self.sent_question(self.fr, "Q-001", 100)
        self.sent_question(self.fr, "Q-003", 101)
        self.run_inbox([self_reply(200, "\\o/ \\1", quote=100),
                        self_reply(201, "A\n\n## Q-009 [open] Wyślij dane", quote=101)])
        text = self.qtext(self.fr)
        self.assertIn("\\o/ \\1 (Wojciech", text)
        from houselib import questions as qmod
        self.assertNotIn("Q-009", [q.id for q in qmod.parse(text)])

    def test_last_day_is_still_running(self):
        c = self.contest(Path(self._tmp.name) / "endday", "ed", "Team", end="2026-11-20")
        self.assertEqual(inbox.running([c], now=datetime(2026, 11, 20, 15, 0)), [c])
        self.assertEqual(inbox.running([c], now=datetime(2026, 11, 21, 0, 1)), [])

    def test_concurrent_run_is_skipped(self):
        import fcntl
        with (notify.data_dir() / "inbox.lock").open("w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            sig = FakeSignal([self_reply(200, "Q-001: B")], self.groups)
            self.assertEqual(inbox.process([self.fr], sig), ["another `house inbox` is running; skipped"])
        self.assertEqual(sig.incoming, [self_reply(200, "Q-001: B")])  # nothing was consumed

    def test_bad_envelope_does_not_lose_the_batch(self):
        bad = self_reply(199, "x", quote=1)
        bad["envelope"]["syncMessage"]["sentMessage"]["quote"]["id"] = "abc"
        self.run_inbox([bad, self_reply(200, "fr Q-001: B")])
        self.assertIn("B (Wojciech", self.qtext(self.fr))

    def test_reminder_not_marked_when_not_sent(self):
        (self.fr / ".house/QUESTIONS.md").write_text(
            "# Q\n\n## Q-005 [open] F?\n- **Poziom:** krytyczny\n- **Termin:** 2026-11-20 23:50 (koniec − 10 min)\n"
            "- **Domyślnie:** B\n- **Odpowiedź:** —\n")
        (self.fr / ".house/state.json").write_text(json.dumps({"sent_questions": {"Q-005": "2026-11-20T21:50:00"}}))
        inbox.deadlines(self.fr, None, now=datetime(2026, 11, 20, 23, 0))
        self.assertNotIn("reminded", json.loads((self.fr / ".house/state.json").read_text()))
        inbox.deadlines(self.fr, None, now=datetime(2026, 11, 20, 23, 55))
        self.assertIn("B (judge, domyślnie", self.qtext(self.fr))  # trailing text after Termin is fine


if __name__ == "__main__":
    unittest.main()


class ReactionAnswerTest(InboxCase):
    QD = ("# Q\n\n## Q-001 [open] Finalne zgłoszenie?\n- **Opcje:** A) x B) y\n- **Domyślnie:** B\n"
          "- **Odpowiedź:** —\n\n## Q-002 [open] Puszczamy sweep na noc?\n- **Domyślnie:** tak\n- **Odpowiedź:** —\n"
          "\n## Q-003 [open] Batch size?\n- **Odpowiedź:** —\n")

    def setUp(self):
        super().setUp()
        (self.fr / ".house/QUESTIONS.md").write_text(self.QD)
        self.sent_question(self.fr, "Q-001", 101)
        self.sent_question(self.fr, "Q-002", 102)
        self.sent_question(self.fr, "Q-003", 103)

    def react(self, ts, emoji, target, remove=False, gid="self", name="Wojciech Fiołka", uuid="u-own"):
        from tests import signal_fixtures as fx
        if gid == "self":
            return fx.reaction(ts, emoji, target, OWN, gid="self", remove=remove)
        return fx.reaction(ts, emoji, target, OWN, gid=gid, remove=remove, name=name, uuid=uuid)

    def test_reaction_variants(self):
        ra = inbox.reaction_answer
        self.assertEqual([ra(e) for e in ("👍", "👍🏽", "👎🏿", "❤️", "❤", "❤️‍🔥", "❤️‍🩹", "💔", "🩷", "🧡", "😂", "")],
                         ["yes", "yes", "no", "default", "default", "default", "default", "default", "default",
                          "default", None, None])

    def test_thumb_yes_no(self):
        sig, _ = self.run_inbox([self.react(300, "👍🏻", 102), self.react(301, "👎", 103)])
        self.assertIn("- **Odpowiedź:** tak (Wojciech Fiołka, Signal)", self.qtext(self.fr))
        self.assertIn("- **Odpowiedź:** nie (Wojciech Fiołka, Signal)", self.qtext(self.fr))
        self.assertIn((None, "🤖 [fr] ✓ Q-002: tak (Wojciech Fiołka)"), sig.sent)

    def test_thumb_on_options_question_replies(self):
        sig, _ = self.run_inbox([self.react(300, "👍", 101)])
        self.assertIn("[open] Finalne", self.qtext(self.fr))
        self.assertEqual(sig.sent, [(None, "🤖 [fr] Q-001: odpowiedz literą albo ❤ = domyślna")])

    def test_heart_takes_default(self):
        self.run_inbox([self.react(300, "❤️", 101)])
        self.assertIn("- **Odpowiedź:** B (Wojciech Fiołka, Signal)", self.qtext(self.fr))

    def test_heart_without_default_replies(self):
        sig, _ = self.run_inbox([self.react(300, "💙", 103)])
        self.assertIn("[open] Batch", self.qtext(self.fr))
        self.assertEqual(sig.sent, [(None, "🤖 [fr] Q-003: brak domyślnej, odpowiedz tekstem")])

    def test_removed_before_handling_cancels(self):
        sig, _ = self.run_inbox([self.react(300, "👍", 102), self.react(301, "👍", 102, remove=True)])
        self.assertIn("[open] Puszczamy", self.qtext(self.fr))
        self.assertEqual(sig.sent, [])

    def test_changed_reaction_counts_last(self):
        self.run_inbox([self.react(300, "👎", 102), self.react(301, "❤️", 102)])
        self.assertIn("- **Odpowiedź:** tak (Wojciech Fiołka, Signal)", self.qtext(self.fr))

    def test_removed_after_handling_noop(self):
        self.run_inbox([self.react(300, "👍", 102)])
        sig, _ = self.run_inbox([self.react(301, "👍", 102, remove=True)])
        self.assertIn("- **Odpowiedź:** tak", self.qtext(self.fr))
        self.assertEqual(sig.sent, [])

    def test_reaction_in_group(self):
        self.sent_question(self.fr, "Q-002", 202, chat="gT")
        sig, _ = self.run_inbox([self.react(300, "👍", 202, gid="gT", name="Ola", uuid="u-ola")])
        self.assertIn("- **Odpowiedź:** tak (Ola, Signal)", self.qtext(self.fr))
        self.assertIn(("gT", "🤖 [fr] ✓ Q-002: tak (Ola)"), sig.sent)

    def test_first_answer_wins_across_text_and_reaction(self):
        sig, _ = self.run_inbox([self_reply(400, "nie", quote=102), self.react(300, "👍", 102)])
        self.assertIn("- **Odpowiedź:** tak", self.qtext(self.fr))

    def test_reaction_to_other_message_ignored(self):
        sig, _ = self.run_inbox([self.react(300, "👍", 999)])
        self.assertEqual(sig.sent, [])
