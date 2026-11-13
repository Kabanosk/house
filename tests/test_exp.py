"""`house exp`: budgeted experiments scored by the local scorer, kept or reverted, recorded as a tree."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from houselib import exp, gitutil
from tests.helpers import git  # noqa: F401  (also isolates Signal)


def row(i, status, score="", scorer="s1", parent=""):
    return {"id": f"E-{i:03d}", "parent": parent, "commit": f"c{i}", "idea_id": "", "budget": "300",
            "duration": "1", "score": score, "status": status, "scorer": scorer, "time": "", "note": ""}


class ParsingTest(unittest.TestCase):
    def test_parse_budget(self):
        self.assertEqual(exp.parse_budget("90s"), 90)
        self.assertEqual(exp.parse_budget("5m"), 300)
        self.assertEqual(exp.parse_budget("1h30m"), 5400)
        self.assertEqual(exp.parse_budget("45"), 45)
        for bad in ("x", "0", "", "5q", "m"):
            with self.assertRaises(ValueError, msg=bad):
                exp.parse_budget(bad)

    def test_direction(self):
        self.assertEqual(exp.direction({"scoring": {"direction": "min"}}), "min")
        self.assertEqual(exp.direction({"scoring": {"metric": "RMSE (min)"}}), "min")
        self.assertEqual(exp.direction({"scoring": {"metric": "F1, maximize"}}), "max")
        self.assertEqual(exp.direction({"scoring": {"metric": "MAE minimize", "direction": "max"}}), "max")
        self.assertIsNone(exp.direction({"scoring": {"metric": "F1"}}))
        self.assertIsNone(exp.direction({}))

    def test_parse_score(self):
        self.assertEqual(exp.parse_score("score: 0.8\nn=1000\n"), 0.8)
        self.assertEqual(exp.parse_score("Score: 0.1\nSCORE=1.5e-3\n"), 0.0015)
        self.assertEqual(exp.parse_score("loss\n0.25\n\n"), 0.25)
        self.assertEqual(exp.parse_score("-3\n"), -3.0)
        self.assertIsNone(exp.parse_score("done\n"))
        self.assertIsNone(exp.parse_score("F1 0.8 on 1000\n"))
        self.assertIsNone(exp.parse_score(""))

    def test_best_and_stagnation(self):
        rows = [row(1, "keep", "0.5"), row(2, "revert", "0.4"), row(3, "keep", "0.7"), row(4, "revert", "0.6"),
                row(5, "crash"), row(6, "revert", "0.65"), row(7, "keep", "0.9", scorer="old"), row(8, "new")]
        self.assertEqual(exp.best(rows, "s1", "max")["id"], "E-003")
        self.assertEqual(exp.stagnation(rows, "s1", "max"), 2)
        self.assertEqual(exp.best(rows, "s1", "min")["id"], "E-001")
        self.assertIsNone(exp.best(rows, "other", "max"))
        self.assertEqual(exp.stagnation(rows, "other", "max"), 0)
        self.assertEqual(exp.stagnation([row(1, "revert", "0.1"), row(2, "revert", "0.2")], "s1", "max"), 2)

    def test_next_id(self):
        self.assertEqual(exp.next_id([]), "E-001")
        self.assertEqual(exp.next_id([row(9, "keep", "1"), row(3, "new")]), "E-010")


class RecordsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.c = Path(self._tmp.name)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.c)], check=True)
        (self.c / ".house").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_load_missing_is_empty_and_write_round_trips(self):
        self.assertEqual(exp.load(self.c), [])
        rows = [row(1, "keep", "0.5"), {**row(2, "crash"), "note": 'has, comma and "quote"\nnewline'}]
        with exp._locked(self.c):
            exp._write(self.c, rows)
        back = exp.load(self.c)
        self.assertEqual(back[0], rows[0])
        self.assertEqual(back[1]["note"], 'has, comma and "quote" newline')
        header = (self.c / ".house/experiments.csv").read_text().splitlines()[0]
        self.assertEqual(header, ",".join(exp.COLUMNS))

    def test_exclude_is_idempotent(self):
        gitutil.exclude(self.c, ".house/experiments/")
        gitutil.exclude(self.c, ".house/experiments/")
        lines = (self.c / ".git/info/exclude").read_text().splitlines()
        self.assertEqual(lines.count(".house/experiments/"), 1)


if __name__ == "__main__":
    unittest.main()
