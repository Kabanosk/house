"""`houselib.leaderboard`: background polling of the official leaderboard, change summary for `/lb`."""
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from houselib import leaderboard as lb
from tests import helpers  # noqa: F401  (isolated data dir)

T0 = datetime(2026, 11, 17, 12, 0).astimezone()
SPEC = "leaderboard:\n  cmd: \"x\"\n  every: 15min\n  team: UwUr2\n"


def board(*pairs):
    return [{"team": t, "score": s} for t, s in pairs]


class FakeRun:
    def __init__(self, out="[]", code=0, exc=None):
        self.out, self.code, self.exc, self.calls = out, code, exc, []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(cmd, self.code, self.out, "boom" if self.code else "")


class ContestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.contest = self.tmp / "c"
        (self.contest / ".house").mkdir(parents=True)
        (self.contest / ".house/spec.yaml").write_text(SPEC)

    def snap(self, rows, at):
        with open(self.contest / ".house/leaderboard.jsonl", "a") as f:
            f.write(json.dumps({"at": at.isoformat(), "rows": rows}) + "\n")


class ConfigTest(ContestCase):
    def test_config_reads_spec(self):
        self.assertEqual(lb.config(self.contest), {"cmd": "x", "every": timedelta(minutes=15), "team": "UwUr2"})

    def test_config_missing(self):
        (self.contest / ".house/spec.yaml").write_text("leaderboard: null\n")
        self.assertIsNone(lb.config(self.contest))
        (self.contest / ".house/spec.yaml").write_text("leaderboard:\n  team: A\n")
        self.assertIsNone(lb.config(self.contest))
        (self.contest / ".house/spec.yaml").unlink()
        self.assertIsNone(lb.config(self.contest))

    def test_parse_every(self):
        self.assertEqual(lb.parse_every("90s"), timedelta(seconds=90))
        self.assertEqual(lb.parse_every("15min"), timedelta(minutes=15))
        self.assertEqual(lb.parse_every("2h"), timedelta(hours=2))
        self.assertEqual(lb.parse_every("bzdura"), timedelta(minutes=15))
        self.assertEqual(lb.parse_every(None), timedelta(minutes=15))

    def test_parse_rows(self):
        rows = lb.parse_rows('[{"team": "A", "score": "0.53", "id": 7}]')
        self.assertEqual(rows, [{"team": "A", "score": 0.53, "id": 7}])
        self.assertEqual(lb.parse_rows("[]"), [])
        for bad in ("{}", "<html>", '[{"score": 1}]', '[{"team": "A", "score": "x"}]'):
            with self.assertRaises(ValueError, msg=bad):
                lb.parse_rows(bad)


class PollTest(ContestCase):
    def test_poll_appends_only_on_change(self):
        run = FakeRun(json.dumps(board(("A", 0.5))))
        self.assertIsNotNone(lb.poll(self.contest, T0, run=run))
        lb.poll(self.contest, T0 + timedelta(minutes=1), run=run, force=True)
        self.assertEqual(len(lb.snapshots(self.contest)), 1)
        run.out = json.dumps(board(("A", 0.6)))
        lb.poll(self.contest, T0 + timedelta(minutes=2), run=run, force=True)
        snaps = lb.snapshots(self.contest)
        self.assertEqual([s["rows"][0]["score"] for s in snaps], [0.5, 0.6])
        cmd, kw = run.calls[0]
        self.assertEqual((cmd, kw["cwd"], kw["timeout"], kw["shell"]), ("x", self.contest, 120, True))
        self.assertEqual(lb.poll_state(self.contest)["last_ok"], (T0 + timedelta(minutes=2)).isoformat())

    def test_poll_respects_every(self):
        run = FakeRun("[]")
        lb.poll(self.contest, T0, run=run)
        self.assertIsNone(lb.poll(self.contest, T0 + timedelta(minutes=5), run=run))
        self.assertEqual(len(run.calls), 1)
        lb.poll(self.contest, T0 + timedelta(minutes=15), run=run)
        self.assertEqual(len(run.calls), 2)

    def test_poll_not_configured(self):
        (self.contest / ".house/spec.yaml").write_text("")
        run = FakeRun()
        self.assertIsNone(lb.poll(self.contest, T0, run=run))
        self.assertEqual(run.calls, [])

    def test_poll_bad_output_records_error(self):
        lb.poll(self.contest, T0, run=FakeRun(json.dumps(board(("A", 0.5)))))
        line = lb.poll(self.contest, T0 + timedelta(hours=1), run=FakeRun("<html>login</html>"))
        self.assertIn("błąd", line)
        state = lb.poll_state(self.contest)
        self.assertEqual(state["last_ok"], T0.isoformat())
        self.assertEqual(state["last_try"], (T0 + timedelta(hours=1)).isoformat())
        self.assertTrue(state["error"])
        lb.poll(self.contest, T0 + timedelta(hours=2), run=FakeRun(code=1))
        lb.poll(self.contest, T0 + timedelta(hours=3), run=FakeRun(exc=subprocess.TimeoutExpired("x", 120)))
        self.assertIn("120", lb.poll_state(self.contest)["error"])
        self.assertEqual(len(lb.snapshots(self.contest)), 1)
        lb.poll(self.contest, T0 + timedelta(hours=4), run=FakeRun(json.dumps(board(("A", 0.5)))))
        self.assertIsNone(lb.poll_state(self.contest)["error"])


US = "UwUr2"


class ChangesTest(unittest.TestCase):
    def test_changes_new_and_improved(self):
        old = board(("A", .54), ("B", .53), ("C", .52))
        new = board(("A", .54), ("D", .535), ("C", .53), ("B", .53))
        self.assertEqual(lb.changes(old, new, None),
                         [lb.Change("D", .535, None, 2, None), lb.Change("C", .53, .52, 3, 3)])

    def test_tie_swap_is_not_a_change(self):
        self.assertEqual(lb.changes(board(("A", .5), ("B", .5)), board(("B", .5), ("A", .5)), None), [])

    def test_changes_outside_window_ignored(self):
        old = board(*[(f"T{i}", 1 - i / 100) for i in range(1, 41)])
        old[29] = {"team": US, "score": old[29]["score"]}
        far, near = [dict(r) for r in old], [dict(r) for r in old]
        far[34]["score"] -= 0.001
        near[31]["score"] -= 0.001
        self.assertEqual(lb.changes(old, far, US), [])
        self.assertEqual([c.team for c in lb.changes(old, near, US)], ["T32"])
        out = [dict(r) for r in old]
        out[0], out[29] = dict(old[29]), dict(old[0])  # we move to #1: our own change is never in the list
        out[0]["score"] = 2
        self.assertEqual([c.team for c in lb.changes(old, out, US)], [])

    def test_table_top10_and_us(self):
        rows = board(*[(f"T{i}", round(1 - i / 100, 2)) for i in range(1, 16)])
        rows[12]["team"] = US
        lines = lb.table(rows, US).splitlines()
        self.assertEqual(lines[0], "miejsce | team | wynik")
        self.assertEqual(lines[1], "1 | T1 | 0.99")
        self.assertEqual(lines[-2:], ["…", "13 | UwUr2 | 0.87 ← my"])
        self.assertEqual(len(lines), 13)
        rows[12]["team"], rows[3]["team"] = "T13", US
        lines = lb.table(rows, US).splitlines()
        self.assertNotIn("…", lines)
        self.assertEqual(lines[4], "4 | UwUr2 | 0.96 ← my")


class SummaryTest(ContestCase):
    OLD = board(("A", .54), ("B", .53), (US, .53), ("C", .52))
    NEW = board(("A", .54), ("D", .535), ("C", .53), (US, .53), ("B", .53))

    def test_summary_first_time_is_table(self):
        self.snap(self.OLD, T0)
        s = lb.summary(self.contest, None, None, T0 + timedelta(minutes=5))
        self.assertFalse(s.sentences)
        self.assertEqual(s.at, T0.isoformat())
        self.assertIn("1 | A | 0.54", s.text)
        self.assertTrue(s.text.endswith("ranking z 12:00"))

    def test_summary_few_changes_sentences(self):
        self.snap(self.OLD, T0)
        self.snap(self.NEW, T0 + timedelta(hours=1))
        s = lb.summary(self.contest, T0.isoformat(), T0, T0 + timedelta(hours=1, minutes=5))
        self.assertTrue(s.sentences)
        self.assertEqual(s.at, (T0 + timedelta(hours=1)).isoformat())
        self.assertIn("D wszedł z 0.535 na #2", s.text)
        self.assertIn("C poprawił na 0.53 (było 0.52), z #4 na #3", s.text)
        self.assertIn("my: #4 (0.53), było #3", s.text)
        self.assertNotIn("B ", s.text)
        self.assertTrue(s.text.endswith("ranking z 13:00"))

    def test_summary_worse_and_moved(self):
        self.snap(board(("A", .54), ("B", .53), (US, .5)), T0)
        self.snap(board(("B", .53), ("A", .52), (US, .5)), T0 + timedelta(hours=1))
        s = lb.summary(self.contest, T0.isoformat(), T0, T0 + timedelta(hours=2))
        self.assertIn("A zmienił na 0.52 (było 0.54), z #1 na #2", s.text)
        self.assertIn("my: #3 (0.5)", s.text)

    def test_summary_many_changes_table(self):
        self.snap(board(("A", .5), ("B", .5), ("C", .5), ("D", .5), (US, .4)), T0)
        self.snap(board(("A", .6), ("B", .6), ("C", .6), ("D", .6), (US, .4)), T0 + timedelta(hours=1))
        s = lb.summary(self.contest, T0.isoformat(), T0, T0 + timedelta(hours=2))
        self.assertFalse(s.sentences)
        self.assertIn("5 | UwUr2 | 0.4 ← my", s.text)

    def test_summary_after_8h_table(self):
        self.snap(self.OLD, T0)
        self.snap(board(("A", .55), ("B", .53), (US, .53), ("C", .52)), T0 + timedelta(hours=1))
        s = lb.summary(self.contest, T0.isoformat(), T0, T0 + timedelta(hours=9))
        self.assertFalse(s.sentences)

    def test_summary_previous_snapshot_gone_is_table(self):
        self.snap(self.NEW, T0 + timedelta(hours=1))
        s = lb.summary(self.contest, T0.isoformat(), T0, T0 + timedelta(hours=2))
        self.assertFalse(s.sentences)

    def test_summary_no_changes(self):
        self.snap(self.OLD, T0)
        s = lb.summary(self.contest, T0.isoformat(), T0 + timedelta(hours=1), T0 + timedelta(hours=2))
        self.assertTrue(s.sentences)
        self.assertIn("bez zmian od 13:00", s.text)
        self.assertIn("my: #3 (0.53)", s.text)

    def test_render_team_absent(self):
        self.snap(board(("A", .5)), T0)
        self.assertIn("nas nie ma w rankingu", lb.summary(self.contest, None, None, T0).text)
        self.snap(board(("A", .6)), T0 + timedelta(hours=1))
        self.assertIn("my: nas nie ma w rankingu",
                      lb.summary(self.contest, T0.isoformat(), T0, T0 + timedelta(hours=1)).text)

    def test_summary_fresh_time_from_last_ok(self):
        self.snap(self.OLD, T0)
        (self.contest / ".house/leaderboard.state.json").write_text(json.dumps(
            {"last_try": (T0 + timedelta(hours=3)).isoformat(), "last_ok": (T0 + timedelta(hours=3)).isoformat(),
             "error": None}))
        self.assertTrue(lb.summary(self.contest, None, None, T0 + timedelta(hours=3)).text
                        .endswith("ranking z 15:00"))

    def test_summary_stale_and_empty(self):
        s = lb.summary(self.contest, None, None, T0)
        self.assertIsNone(s.at)
        self.assertIn("brak danych rankingu", s.text)
        (self.contest / ".house/leaderboard.state.json").write_text(json.dumps(
            {"last_try": (T0 + timedelta(hours=2)).isoformat(), "last_ok": None, "error": "kod 1: boom"}))
        self.assertIn("kod 1: boom", lb.summary(self.contest, None, None, T0).text)
        self.snap(self.OLD, T0)
        (self.contest / ".house/leaderboard.state.json").write_text(json.dumps(
            {"last_try": (T0 + timedelta(hours=2)).isoformat(), "last_ok": T0.isoformat(), "error": "kod 1: boom"}))
        text = lb.summary(self.contest, None, None, T0 + timedelta(hours=2)).text
        self.assertTrue(text.endswith("⚠ ostatnie pobranie nieudane (14:00); dane z 12:00"), text)


if __name__ == "__main__":
    unittest.main()
