"""`houselib.notify`: format and send to Note to Self + the contest's group, remember what was sent."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from houselib import notify, questions
from tests import helpers  # noqa: F401  (never the real Signal account)

QS = ("# Q\n\n## Q-001 [open] Które zgłoszenie finalne?\n- **Opcje:** A) lgbm B) ensemble\n"
      "- **Rekomendacja:** B (lepszy na obu)\n- **Poziom:** krytyczny\n- **Termin:** 2026-11-20 23:50\n"
      "- **Domyślnie:** B\n- **Odpowiedź:** —\n\n## Q-002 [answered 2026-11-12] stare\n")


class FakeSignal:
    def __init__(self, groups=None):
        self.sent, self.groups, self.ts, self.meta = [], groups or [], 1000, []

    def send(self, text, group_id=None, meta=None, **kw):
        self.ts += 1
        self.sent.append((group_id, text))
        self.meta.append(meta)
        return self.ts

    def list_groups(self):
        return self.groups


class NotifyTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.c = tmp / "fr-contest"
        (self.c / ".house").mkdir(parents=True)
        (self.c / ".house/state.json").write_text("{}")
        (self.c / ".house/QUESTIONS.md").write_text(QS)
        self.env = mock.patch.dict(os.environ, {"HOUSE_DATA_DIR": str(tmp / "data")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self._tmp.cleanup()

    def spec(self, text: str):
        (self.c / ".house/spec.yaml").write_text(text)

    def test_prefix(self):
        self.assertEqual(notify.prefix(self.c), "🤖 [fr-contest@test]")
        self.spec("notify:\n  alias: fr\n")
        self.assertEqual(notify.prefix(self.c), "🤖 [fr@test]")

    def test_send_self_and_group_and_log(self):
        self.spec('notify:\n  alias: fr\n  signal_group: "Huawei Poland"\n')
        sig = FakeSignal([{"id": "g1", "name": "Huawei Poland"}, {"id": "g2", "name": "Other"}])
        ts, warnings = notify.send(self.c, "hello", sig=sig)
        self.assertEqual(sig.sent, [(None, "🤖 [fr@test] hello"), ("g1", "🤖 [fr@test] hello")])
        self.assertEqual(len(ts), 2)
        self.assertEqual(warnings, [])
        self.assertEqual([(g, m["contest"]) for (g, _), m in zip(sig.sent, sig.meta, strict=True)],
                         [(None, "fr-contest"), ("g1", "fr-contest")])
        self.assertEqual(json.loads((notify.data_dir() / "groups.json").read_text()), {"Huawei Poland": "g1"})

    def test_notify_meta(self):
        self.spec('notify:\n  alias: fr\n  signal_group: "Team"\n')
        sig = FakeSignal([{"id": "g1", "name": "Team"}])
        notify.send(self.c, "hello", sig=sig)
        self.assertEqual(sig.meta, [{"origin": "notify", "contest": "fr-contest", "question": None}] * 2)
        sig = FakeSignal()
        notify.ask_new(self.c, sig=sig)
        self.assertEqual(sig.meta, [{"origin": "ask", "contest": "fr-contest", "question": "Q-001"}] * 2)

    def test_unknown_or_ambiguous_group(self):
        self.spec("notify:\n  signal_group: Team\n")
        sig = FakeSignal([{"id": "g1", "name": "Team"}, {"id": "g2", "name": "Team"}])
        _, warnings = notify.send(self.c, "x", sig=sig)
        self.assertEqual([g for g, _ in sig.sent], [None])
        self.assertTrue(warnings)

    def test_no_signal_is_noop(self):
        with mock.patch.object(notify, "from_config", return_value=None):
            self.assertEqual(notify.send(self.c, "x"), ([], []))

    def test_question_message(self):
        q = questions.parse(QS)[0]
        msg = notify.question_message(q)
        self.assertTrue(msg.startswith("Q-001 Które zgłoszenie finalne?"))
        self.assertIn("A) lgbm B) ensemble", msg)
        self.assertIn("Rekomendacja: B (lepszy na obu)", msg)
        self.assertIn("Domyślnie B o 2026-11-20 23:50", msg)
        self.assertIn("↩ odpowiedz cytatem", msg)

    def test_ask_new_sends_each_open_question_once(self):
        sig = FakeSignal()
        self.assertEqual(notify.ask_new(self.c, sig=sig), ["Q-001"])
        self.assertEqual(notify.ask_new(self.c, sig=sig), [])
        self.assertEqual(len(sig.sent), 1)
        self.assertEqual(sig.meta[0]["question"], "Q-001")
        self.assertIn("Q-001", json.loads((self.c / ".house/state.json").read_text())["sent_questions"])


if __name__ == "__main__":
    unittest.main()
