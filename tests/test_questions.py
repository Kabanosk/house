"""`houselib.questions`: parse QUESTIONS.md, record answers, deadlines."""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from houselib import questions
from houselib.fm import parse_frontmatter

QS = """# Pytania do człowieka

## Q-001 [open] Który model zgłosić?
- **Kontekst:** dwa kandydaty
- **Opcje:** A) lgbm B) ensemble
- **Rekomendacja:** B (lepszy na obu)
- **Blokuje:** T002, T003
- **Poziom:** krytyczny
- **Termin:** 2026-11-20 23:50
- **Domyślnie:** B
- **Odpowiedź:** —

## Q-002 [answered 2026-11-12] Stary
- **odpowiedź**: A

## Q-003   [open]   Regulamin: czy wolno API?
-   **Poziom:**   zawsze człowiek
- **Termin:** 2026-11-14 10:00
- **Domyślnie:** nie
"""

TASK = "---\nid: {tid}\ntitle: x\nstatus: {st}\nowner: null\nbranch: null\ndepends_on: []\nacceptance: [x]\n---\nx\n"


class QuestionsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.house = Path(self._tmp.name) / ".house"
        (self.house / "tasks").mkdir(parents=True)
        (self.house / "QUESTIONS.md").write_text(QS)
        (self.house / "DECISIONS.md").write_text("# D\n\n## D-002 x\n")
        (self.house / "state.json").write_text("{}")
        (self.house / "tasks/T002-a.md").write_text(TASK.format(tid="T002", st="blocked"))
        (self.house / "tasks/T003-b.md").write_text(TASK.format(tid="T003", st="doing"))

    def tearDown(self):
        self._tmp.cleanup()

    def test_parse(self):
        qs = {q.id: q for q in questions.parse(QS)}
        self.assertEqual(set(qs), {"Q-001", "Q-002", "Q-003"})
        q = qs["Q-001"]
        self.assertEqual((q.status, q.title, q.level, q.default), ("open", "Który model zgłosić?", "krytyczny", "B"))
        self.assertEqual(q.deadline, datetime(2026, 11, 20, 23, 50))
        self.assertEqual(q.blocks, ["T002", "T003"])
        self.assertEqual(q.options, "A) lgbm B) ensemble")
        self.assertEqual(qs["Q-002"].status, "answered")
        self.assertEqual(qs["Q-002"].answer, "A")
        self.assertEqual((qs["Q-003"].status, qs["Q-003"].level), ("open", "zawsze człowiek"))

    def test_answer_records_everything_once(self):
        did = questions.answer(self.house, "Q-001", "B", "Kuba", "Signal", today="2026-11-13")
        self.assertEqual(did, "D-003")
        text = (self.house / "QUESTIONS.md").read_text()
        self.assertIn("## Q-001 [answered 2026-11-13] Który model zgłosić?", text)
        self.assertIn("- **Odpowiedź:** B (Kuba, Signal)", text)
        self.assertNotIn("- **Odpowiedź:** —", text)
        dec = (self.house / "DECISIONS.md").read_text()
        self.assertIn("## D-003 Q-001: Który model zgłosić?", dec)
        self.assertIn("- **Kto:** człowiek (Kuba)", dec)
        st = parse_frontmatter((self.house / "tasks/T002-a.md").read_text())[0]["status"]
        self.assertEqual(st, "todo")
        st3 = parse_frontmatter((self.house / "tasks/T003-b.md").read_text())[0]["status"]
        self.assertEqual(st3, "doing")
        self.assertIsNone(questions.answer(self.house, "Q-001", "A", "Ola", "Signal"))
        self.assertIsNone(questions.answer(self.house, "Q-404", "A", "Ola", "Signal"))

    def test_answer_appends_missing_odpowiedz_line(self):
        did = questions.answer(self.house, "Q-003", "tak, wolno", "Wojtek", "Signal", today="2026-11-13")
        self.assertIsNotNone(did)
        self.assertIn("- **Odpowiedź:** tak, wolno (Wojtek, Signal)", (self.house / "QUESTIONS.md").read_text())

    def test_due(self):
        (self.house / "state.json").write_text(json.dumps({"sent_questions": {"Q-001": "2026-11-20T21:50:00"}}))
        apply, remind = questions.due(self.house, datetime(2026, 11, 20, 22, 0))
        self.assertEqual((apply, remind), ([], []))
        apply, remind = questions.due(self.house, datetime(2026, 11, 20, 22, 55))
        self.assertEqual(([q.id for q in apply], [q.id for q in remind]), ([], ["Q-001"]))
        questions.mark_reminded(self.house, "Q-001")
        self.assertEqual(questions.due(self.house, datetime(2026, 11, 20, 23, 0))[1], [])
        apply, _ = questions.due(self.house, datetime(2026, 11, 21, 0, 0))
        self.assertEqual([q.id for q in apply], ["Q-001"])  # Q-003 (zawsze człowiek) never defaults


if __name__ == "__main__":
    unittest.main()
