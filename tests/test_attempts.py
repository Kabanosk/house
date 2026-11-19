"""`houselib.attempts`: the attempt register `.house/attempts.jsonl`."""
import json
import tempfile
import unittest
from pathlib import Path

from houselib import attempts
from houselib.attempts import Row


def _row(task="T001", n=1, **kw) -> Row:
    base = dict(source="night", agent="worker", tool="claude", session="sess-1", log="logs/a.jsonl",
                start="2026-11-17T01:00:00", end="2026-11-17T01:10:00", base="aaa111", head="bbb222",
                outcome="merged", message="done")
    base.update(kw)
    return Row(task=task, n=n, **base)


class AttemptsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.contest = Path(tmp.name).resolve()
        self.house = self.contest / ".house"
        self.house.mkdir()

    def test_next_n_empty_then_increments(self):
        self.assertEqual(attempts.next_n(self.house, "T001"), 1)
        attempts.record(self.house, _row(n=1))
        self.assertEqual(attempts.next_n(self.house, "T001"), 2)
        attempts.record(self.house, _row(n=2))
        self.assertEqual(attempts.next_n(self.house, "T001"), 3)
        self.assertEqual(attempts.next_n(self.house, "T002"), 1)

    def test_numbers_continue_across_sources(self):
        attempts.record(self.house, _row(n=1, source="merge", tool="claude", session="", log=""))
        self.assertEqual(attempts.next_n(self.house, "T001"), 2)
        attempts.record(self.house, _row(n=2, source="night"))
        self.assertEqual([(r.source, r.n) for r in attempts.rows(self.house, "T001")], [("merge", 1), ("night", 2)])
        self.assertEqual(attempts.next_n(self.house, "T001"), 3)

    def test_paths_relative_and_message_redacted(self):
        secret = "sk-fakeFAKE0123456789abcd"
        absolute = self.contest / ".house/tmp/night/2026-11-17/T001-010000-1.jsonl"
        path = attempts.record(self.house, _row(log=str(absolute), message=f"key {secret} " + "x" * 500))
        self.assertEqual(path, self.house / "attempts.jsonl")
        raw = path.read_text()
        self.assertNotIn(secret, raw)
        self.assertNotIn(str(self.contest), raw)
        row = attempts.find(self.house, "T001", 1)
        self.assertEqual(row.log, ".house/tmp/night/2026-11-17/T001-010000-1.jsonl")
        self.assertTrue(row.message.startswith("key [REDACTED] "))
        self.assertLessEqual(len(row.message), 300)

    def test_secret_straddling_the_cut_is_still_masked(self):
        secret = "sk-fakeFAKE0123456789abcd"
        attempts.record(self.house, _row(message="y" * 295 + " " + secret))
        row = attempts.find(self.house, "T001", 1)
        self.assertNotIn("sk-", row.message)
        self.assertLessEqual(len(row.message), 300)

    def test_absolute_log_outside_contest_keeps_file_name_only(self):
        attempts.record(self.house, _row(log="/somewhere/else/run.jsonl"))
        self.assertEqual(attempts.find(self.house, "T001", 1).log, "run.jsonl")

    def test_relative_log_and_empty_log_kept(self):
        attempts.record(self.house, _row(n=1, log="a/b.jsonl"))
        attempts.record(self.house, _row(n=2, log=""))
        self.assertEqual([r.log for r in attempts.rows(self.house)], ["a/b.jsonl", ""])

    def test_row_fields_in_the_file(self):
        attempts.record(self.house, _row())
        data = json.loads((self.house / "attempts.jsonl").read_text())
        self.assertEqual(list(data), ["task", "n", "source", "agent", "tool", "session", "log", "start", "end",
                                      "base", "head", "outcome", "message", "report", "questions", "reason",
                                      "main_merged"])
        self.assertEqual(data["report"], "")
        self.assertIsNone(data["questions"])

    def test_questions_field_and_old_rows(self):
        attempts.record(self.house, _row(n=1, questions=["Q-007", "Q-008"]))
        attempts.record(self.house, _row(n=2, questions=[]))
        with (self.house / "attempts.jsonl").open("a") as f:
            f.write(json.dumps({"task": "T001", "n": 3}) + "\n")  # a row from before the field
            f.write(json.dumps({"task": "T001", "n": 4, "questions": "Q-1"}) + "\n")
            f.write(json.dumps({"task": "T001", "n": 5, "questions": ["Q-1", 2]}) + "\n")
        self.assertEqual([r.questions for r in attempts.rows(self.house)], [["Q-007", "Q-008"], [], None, None, None])

    def test_absolute_paths_in_message_are_cut(self):
        home = str(Path.home())
        message = (f"refused: uncommitted changes in the worktree {self.contest}/.worktrees/T001-x; "
                   f"see {home}/notes.txt")
        attempts.record(self.house, _row(message=message))
        raw = (self.house / "attempts.jsonl").read_text()
        self.assertNotIn(str(self.contest), raw)
        self.assertNotIn(home + "/", raw)
        self.assertEqual(attempts.find(self.house, "T001", 1).message,
                         "refused: uncommitted changes in the worktree ./.worktrees/T001-x; see ~/notes.txt")

    def test_broken_line_skipped(self):
        attempts.record(self.house, _row(n=1))
        with (self.house / "attempts.jsonl").open("a") as f:
            f.write('{"task": "T001", "n": 2, "sour\n[1, 2]\n{"task": "T001"}\n{"task": "T001", "n": "x"}\n\n')
        attempts.record(self.house, _row(n=3))
        self.assertEqual([r.n for r in attempts.rows(self.house)], [1, 3])
        self.assertEqual(attempts.next_n(self.house, "T001"), 4)

    def test_unicode_line_separators_in_message_do_not_split_a_row(self):
        for n, ch in enumerate(("\u2028", "\u2029", "\x85", "\x0b", "\x0c", "\x1c"), 1):
            attempts.record(self.house, _row(n=n, message=f"note {ch}more"))
        self.assertEqual([r.n for r in attempts.rows(self.house, "T001")], [1, 2, 3, 4, 5, 6])
        self.assertEqual(attempts.find(self.house, "T001", 1).message, "note \u2028more")
        self.assertEqual(attempts.next_n(self.house, "T001"), 7)

    def test_record_after_unterminated_last_line(self):
        (self.house / "attempts.jsonl").write_text('{"task": "T001", "n": 1, "sour')
        attempts.record(self.house, _row(n=2))
        self.assertEqual([r.n for r in attempts.rows(self.house)], [2])

    def test_rows_filter_find_and_missing_file(self):
        self.assertEqual(attempts.rows(self.house), [])
        self.assertIsNone(attempts.find(self.house, "T001", 1))
        attempts.record(self.house, _row("T001", 1))
        attempts.record(self.house, _row("T002", 1))
        self.assertEqual([r.task for r in attempts.rows(self.house)], ["T001", "T002"])
        self.assertEqual([r.task for r in attempts.rows(self.house, "T002")], ["T002"])
        self.assertEqual(attempts.find(self.house, "T002", 1).task, "T002")
        self.assertIsNone(attempts.find(self.house, "T002", 2))

    def test_unknown_keys_ignored_when_reading(self):
        (self.house / "attempts.jsonl").write_text(json.dumps({"task": "T001", "n": 1, "future": "x"}) + "\n")
        row = attempts.find(self.house, "T001", 1)
        self.assertEqual((row.task, row.n, row.outcome), ("T001", 1, ""))


if __name__ == "__main__":
    unittest.main()
