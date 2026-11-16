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

    def test_fmt_size(self):
        self.assertEqual(signalread.fmt_size(500), "500 B")
        self.assertEqual(signalread.fmt_size(4096), "4 KB")
        self.assertEqual(signalread.fmt_size(86016), "84 KB")
        self.assertEqual(signalread.fmt_size(3 * 1024 * 1024 + 1), "3.0 MB")
        self.assertEqual(signalread.fmt_size(None), "? B")


if __name__ == "__main__":
    unittest.main()
