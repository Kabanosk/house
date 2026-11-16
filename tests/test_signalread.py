"""`houselib.signalread`: folding reactions/edits/deletes, filters, formatting, cursors."""
import unittest

from houselib import signalread
from tests import helpers  # noqa: F401  (never the real Signal account)


class HelpersTest(unittest.TestCase):
    def test_snippet(self):
        long = "🤖 [house] Cześć! Jestem Claude Wojtka, będę tu wrzucał wyniki."
        self.assertEqual(signalread.snippet(long, 28), "Cześć! Jestem Claude Wojtka…")
        self.assertEqual(signalread.snippet("Spoko, a jaki wynik ma teraz baseline?", 28),
                         "Spoko, a jaki wynik ma teraz…")
        self.assertEqual(signalread.snippet("krótko\n\n  i  już", 28), "krótko i już")
        self.assertEqual(signalread.snippet("x" * 40, 10), "x" * 10 + "…")
        self.assertEqual(signalread.snippet(None, 10), "")

    def test_fmt_time(self):
        from datetime import datetime
        now = datetime(2026, 11, 16, 12, 40).astimezone()
        self.assertEqual(signalread.fmt_time(datetime(2026, 11, 16, 9, 5).astimezone(), now), "09:05")
        self.assertEqual(signalread.fmt_time(datetime(2026, 11, 15, 23, 2).astimezone(), now), "wczoraj 23:02")
        self.assertEqual(signalread.fmt_time(datetime(2026, 11, 13, 8, 0).astimezone(), now), "2026-11-13 08:00")
        self.assertEqual(signalread.fmt_time(None, now), "nigdy")

    def test_fmt_size(self):
        self.assertEqual(signalread.fmt_size(500), "500 B")
        self.assertEqual(signalread.fmt_size(4096), "4 KB")
        self.assertEqual(signalread.fmt_size(86016), "84 KB")
        self.assertEqual(signalread.fmt_size(3 * 1024 * 1024 + 1), "3.0 MB")
        self.assertEqual(signalread.fmt_size(None), "? B")


if __name__ == "__main__":
    unittest.main()
