"""`house exp`: budgeted experiments scored by the local scorer, kept or reverted, recorded as a tree."""
import io
import shlex
import subprocess
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from houselib import cli, exp, gitutil, hook, scaffold
from tests.helpers import FAKE_TOOLS, git, make_root

SPEC = ("kind: optimize\nscoring:\n  metric: F1\n  direction: max\n  local_scorer: python3 score.py\n"
        "limits:\n  stagnation_n: 3\nexp:\n  budget: 30s\n  shared: [data]\n")
SCORER = "print(f\"score: {float(open('out.txt').read())}\")\n"


def row(i, status, score="", scorer="s1", parent=""):
    return {"id": f"E-{i:03d}", "parent": parent, "commit": f"c{i}", "idea_id": "", "budget": "300",
            "duration": "1", "score": score, "status": status, "scorer": scorer, "time": "", "note": "",
            "command": ""}


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


class ContestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = make_root(Path(self._tmp.name))
        self.c = scaffold.new_contest("c1", None, root=root, detect=lambda: FAKE_TOOLS)
        (self.c / ".house/spec.yaml").write_text(SPEC)
        (self.c / "score.py").write_text(SCORER)
        git(self.c, "add", "-A")
        git(self.c, "commit", "-qm", "scorer")

    def tearDown(self):
        self._tmp.cleanup()

    def rows(self):
        return {r["id"]: r for r in exp.load(self.c)}


class RunTest(ContestCase):
    def test_first_run_from_head_is_keep_and_pinned(self):
        res = exp.run_new(self.c, "best", "echo 0.5 > out.txt")
        self.assertEqual((res.id, res.status, res.score, res.parent_score), ("E-001", "keep", 0.5, None))
        r = self.rows()["E-001"]
        self.assertEqual(r["commit"], git(self.c, "rev-parse", "refs/exp/E-001").strip())
        self.assertEqual(r["parent"], git(self.c, "rev-parse", "HEAD~1").strip())
        self.assertTrue((self.c / ".worktrees/exp-E-001").is_dir())
        self.assertIn("E-001", git(self.c, "log", "-1", "--format=%s", "--", ".house/experiments.csv"))
        self.assertEqual(git(self.c, "status", "--porcelain", "--", ".house").strip(), "")
        self.assertTrue(res.log.is_file())
        self.assertEqual(r["command"], "echo 0.5 > out.txt")

    def test_sweep_rows_record_their_command(self):
        cmd = ["python3", "-c", "open('out.txt', 'w').write('0.5')"]
        exp.run_new(self.c, "best", cmd)
        self.assertEqual(shlex.split(self.rows()["E-001"]["command"]), cmd)

    def test_better_child_keep_worse_revert(self):
        exp.run_new(self.c, "best", "echo 0.5 > out.txt")
        eid, wt = exp.new(self.c, "best", idea="I-002", note="add model")
        self.assertEqual(self.rows()[eid]["status"], "new")
        (wt / "model.py").write_text("X = 1\n")
        res = exp.run(self.c, eid, "echo 0.7 > out.txt")
        self.assertEqual((res.status, res.parent_score), ("keep", 0.5))
        self.assertIn("model.py", git(self.c, "show", "--name-only", "--format=", "refs/exp/E-002"))
        self.assertEqual(self.rows()["E-002"]["parent"], "E-001")
        self.assertEqual(self.rows()["E-002"]["idea_id"], "I-002")
        res = exp.run_new(self.c, "best", "echo 0.6 > out.txt")
        self.assertEqual((res.status, self.rows()["E-003"]["parent"]), ("revert", "E-002"))
        self.assertFalse((self.c / ".worktrees/exp-E-003").exists())
        git(self.c, "rev-parse", "--verify", "refs/exp/E-003")
        self.assertNotIn("exp-E-003", git(self.c, "worktree", "list"))

    def test_tie_is_revert(self):
        exp.run_new(self.c, "best", "echo 0.5 > out.txt")
        self.assertEqual(exp.run_new(self.c, "best", "echo 0.5 > out.txt").status, "revert")

    def test_crash_and_timeout(self):
        res = exp.run_new(self.c, "best", "exit 3")
        self.assertEqual(res.status, "crash")
        self.assertIn("exit 3", res.note)
        t0 = time.monotonic()
        res = exp.run_new(self.c, "best", "sleep 30 & sleep 30", budget=1)
        self.assertLess(time.monotonic() - t0, 10)
        self.assertEqual(res.status, "timeout")
        self.assertFalse((self.c / ".worktrees" / f"exp-{res.id}").exists())

    def test_tampered_scorer_is_crash(self):
        exp.run_new(self.c, "best", "echo 0.5 > out.txt")
        eid, wt = exp.new(self.c, "best")
        (wt / "score.py").write_text("print('score: 99')\n")
        res = exp.run(self.c, eid, "echo 0.9 > out.txt")
        self.assertEqual(res.status, "crash")
        self.assertIn("tampered", res.note)
        self.assertIn("score.py", res.note)
        self.assertIsNone(res.score)

    def test_command_rewriting_scorer_is_ignored(self):
        res = exp.run_new(self.c, "best", "echo 0.5 > out.txt; echo \"print('score: 99')\" > score.py")
        self.assertEqual(res.score, 0.5)

    def test_scorer_change_makes_old_scores_incomparable(self):
        exp.run_new(self.c, "best", "echo 0.5 > out.txt")
        (self.c / "score.py").write_text("print(f\"score: {float(open('out.txt').read()) / 10}\")\n")
        git(self.c, "commit", "-qam", "scorer fix")
        res = exp.run_new(self.c, "E-001", "echo 0.6 > out.txt")
        self.assertEqual((res.status, res.score, res.parent_score), ("keep", 0.06, None))
        rows = exp.load(self.c)
        self.assertNotEqual(rows[0]["scorer"], rows[1]["scorer"])
        self.assertEqual(exp.best(rows, exp.scorer_hash(self.c), "max")["id"], "E-002")
        wt = self.c / ".worktrees/exp-E-002"
        self.assertEqual(git(wt, "status", "--porcelain", "--", "score.py").strip(), "")

    def test_missing_score_is_crash_with_hint(self):
        (self.c / ".house/spec.yaml").write_text(SPEC.replace("python3 score.py", "echo done"))
        res = exp.run_new(self.c, "best", "true")
        self.assertEqual(res.status, "crash")
        self.assertIn("score:", res.note)

    def test_unknown_direction_refuses(self):
        (self.c / ".house/spec.yaml").write_text(SPEC.replace("  direction: max\n", ""))
        with self.assertRaises(ValueError) as cm:
            exp.run_new(self.c, "best", "echo 1 > out.txt")
        self.assertIn("scoring.direction", str(cm.exception))
        self.assertEqual(exp.load(self.c), [])

    def test_unknown_parent_refuses(self):
        for bad in ("E-404", "nosuchref"):
            with self.assertRaises(ValueError):
                exp.new(self.c, bad)
        eid, _ = exp.new(self.c, "best")
        with self.assertRaises(ValueError):
            exp.new(self.c, eid)  # not run yet

    def test_shared_data_is_linked(self):
        (self.c / "data").mkdir()
        (self.c / "data/x.txt").write_text("0.4\n")
        res = exp.run_new(self.c, "best", "cat data/x.txt > out.txt")
        self.assertEqual(res.score, 0.4)
        wt = self.c / ".worktrees" / f"exp-{res.id}"
        self.assertTrue((wt / "data").is_symlink())
        self.assertNotIn("data", git(self.c, "show", "--name-only", "--format=", f"refs/exp/{res.id}"))
        self.assertTrue((self.c / "data/x.txt").exists())

    def test_env_vars(self):
        res = exp.run_new(self.c, "best", 'echo "$HOUSE_EXP $HOUSE_CONTEST" > env.txt; echo 1 > out.txt')
        wt = self.c / ".worktrees" / f"exp-{res.id}"
        self.assertEqual((wt / "env.txt").read_text().split(), [res.id, str(self.c)])

    def test_concurrent_new_distinct_ids(self):
        out = []
        threads = [threading.Thread(target=lambda: out.append(exp.new(self.c, "HEAD")[0])) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(out), ["E-001", "E-002", "E-003"])
        self.assertEqual(len(exp.load(self.c)), 3)

    def test_large_untracked_output_refuses_before_commit(self):
        eid, wt = exp.new(self.c, "best")
        (wt / "weights.bin").write_bytes(b"x" * 100)
        old = exp.MAX_UNTRACKED_BYTES
        exp.MAX_UNTRACKED_BYTES = 10
        try:
            with self.assertRaises(ValueError) as cm:
                exp.run(self.c, eid, "echo 1 > out.txt")
        finally:
            exp.MAX_UNTRACKED_BYTES = old
        self.assertIn("weights.bin", str(cm.exception))
        self.assertEqual(self.rows()[eid]["status"], "new")
        self.assertEqual(git(wt, "rev-parse", "HEAD"), git(self.c, "rev-parse", "HEAD~0"))

    def test_removing_worktree_keeps_shared_data(self):
        (self.c / "data").mkdir()
        (self.c / "data/x.txt").write_text("0.4\n")
        exp.run_new(self.c, "best", "cat data/x.txt > out.txt")
        exp.run_new(self.c, "best", "echo 0.1 > out.txt")  # revert → worktree removed
        self.assertEqual((self.c / "data/x.txt").read_text(), "0.4\n")


class StatusTest(ContestCase):
    def test_status_and_stagnation_arrow(self):
        self.assertIn("none yet", exp.status(self.c))
        self.assertIsNone(exp.summary_line(self.c))
        exp.run_new(self.c, "best", "echo 0.5 > out.txt", idea="I-001")
        exp.run_new(self.c, "best", "exit 1")
        for v in ("0.4", "0.3"):
            exp.run_new(self.c, "best", f"echo {v} > out.txt")
        text = exp.status(self.c)
        self.assertIn("best: E-001 0.5", text)
        self.assertIn("keep 1, revert 2, crash 1", text)
        self.assertIn("stagnation: 2/3", text)
        self.assertNotIn("protocols/stagnation.md", text)
        self.assertIn("E-002 ← E-001 crash", text)
        exp.run_new(self.c, "best", "echo 0.2 > out.txt")
        self.assertIn("stagnation: 3/3 → protocols/stagnation.md", exp.status(self.c))
        line = exp.summary_line(self.c)
        self.assertIn("best E-001 = 0.5", line)
        self.assertIn("stagnation 3/3 → protocols/stagnation.md", line)

    def test_status_reports_scorer_change(self):
        exp.run_new(self.c, "best", "echo 0.5 > out.txt")
        (self.c / "score.py").write_text("print('score: 1')\n")
        git(self.c, "commit", "-qam", "scorer fix")
        text = exp.status(self.c)
        self.assertIn("best: none", text)
        self.assertIn("scorer changed", text)

    def test_prune_keeps_top_worktrees(self):
        for v in ("0.1", "0.2", "0.3", "0.4"):
            exp.run_new(self.c, "best", f"echo {v} > out.txt")  # each beats the best → four keeps
        removed = exp.prune(self.c, keep=2)
        self.assertEqual(sorted(removed), ["E-001", "E-002"])
        self.assertTrue((self.c / ".worktrees/exp-E-004").is_dir())
        self.assertTrue((self.c / ".worktrees/exp-E-003").is_dir())
        self.assertFalse((self.c / ".worktrees/exp-E-001").exists())
        git(self.c, "rev-parse", "--verify", "refs/exp/E-001")
        self.assertEqual(exp.prune(self.c, keep=2), [])

    def test_hook_summary_has_experiments_line(self):
        exp.run_new(self.c, "best", "echo 0.5 > out.txt")
        self.assertIn("experiments: best E-001 = 0.5", hook.session_start({"cwd": str(self.c)}))


class CliTest(ContestCase):
    def setUp(self):
        super().setUp()
        self.patch = mock.patch.object(cli, "HOUSE_ROOT", self.c.parent.parent)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        super().tearDown()

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = cli.main(list(args))
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_round_trip(self):
        code, out, err = self.run_cli("exp", "run", "c1", "--from", "HEAD", "--budget", "20s", "--",
                                      "python3", "-c", "open('out.txt', 'w').write('0.5')")
        self.assertEqual(code, 0, err)
        self.assertIn("E-001 keep 0.5", out)
        code, out, _ = self.run_cli("exp", "run", "c1", "--from", "best", "--", "echo 0.1 > out.txt")
        self.assertEqual(code, 1)
        self.assertIn("E-002 revert 0.1", out)
        code, out, _ = self.run_cli("exp", "new", "c1", "--from", "best", "--idea", "I-003", "--note", "try x")
        self.assertEqual(code, 0)
        eid, path = out.split()
        self.assertEqual((eid, Path(path)), ("E-003", self.c / ".worktrees/exp-E-003"))
        code, out, _ = self.run_cli("exp", "run", "c1", "E-003", "--", "echo 0.9 > out.txt")
        self.assertEqual(code, 0)
        code, out, _ = self.run_cli("exp", "status", "c1")
        self.assertIn("best: E-003 0.9", out)
        code, out, _ = self.run_cli("exp", "prune", "c1", "--keep", "1")
        self.assertIn("E-001", out)

    def test_usage_errors(self):
        self.assertEqual(self.run_cli("exp", "run", "c1", "--", "true")[0], 2)
        self.assertEqual(self.run_cli("exp", "run", "c1", "E-001", "--from", "best", "--", "true")[0], 2)
        self.assertEqual(self.run_cli("exp", "run", "c1", "--from", "best")[0], 2)
        code, _, err = self.run_cli("exp", "run", "c1", "--from", "best", "--budget", "soon", "--", "true")
        self.assertEqual(code, 2)
        self.assertIn("budget", err)


if __name__ == "__main__":
    unittest.main()
