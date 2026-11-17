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


if __name__ == "__main__":
    unittest.main()
