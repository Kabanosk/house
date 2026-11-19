"""`house merge`: check the branch, merge, re-check every merged task on main, revert on regression."""
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from houselib import attempts, checks, cli, merge, night, scaffold
from houselib.fm import parse_frontmatter
from tests.helpers import FAKE_TOOLS, git, make_root

CALC_OK = "def add(a, b):\n    return a + b\n"
CALC_BAD = "def add(a, b):\n    return a - b\n"
TEST_CALC = ("import unittest\nimport calc\n\n\nclass T(unittest.TestCase):\n"
             "    def test_add(self):\n        self.assertEqual(calc.add(2, 3), 5)\n")


def task_text(tid: str, branch: str, acceptance: str, protected: str = "protected: []\n") -> str:
    return (f"---\nid: {tid}\ntitle: x\nstatus: review\nowner: coder\nbranch: {branch}\ndepends_on: []\n"
            f"acceptance:\n{acceptance}{protected}---\nx\n")


class MergeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_root(Path(self._tmp.name))
        self.c = scaffold.new_contest("c1", None, root=self.root, detect=lambda: FAKE_TOOLS)
        self.house = self.c / ".house"

    def tearDown(self):
        self._tmp.cleanup()

    def add_task(self, tid: str, slug: str, acceptance: str, files: dict[str, str], protected="protected: []\n",
                 base: str = "HEAD") -> Path:
        branch = f"task/{tid}-{slug}"
        (self.house / "tasks" / f"{tid}-{slug}.md").write_text(task_text(tid, branch, acceptance, protected))
        checks.freeze(self.house, tid)
        git(self.c, "add", ".house")
        git(self.c, "commit", "-q", "-m", f"plan {tid}")
        wt = self.c / ".worktrees" / f"{tid}-{slug}"
        git(self.c, "worktree", "add", "-q", str(wt), "-b", branch, base)
        for rel, text in files.items():
            (wt / rel).write_text(text)
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m", f"{tid} work")
        return wt

    def status(self, tid: str) -> str:
        path = next((self.house / "tasks").glob(f"{tid}-*.md"))
        return parse_frontmatter(path.read_text())[0]["status"]

    def calc_task(self) -> Path:
        return self.add_task("T001", "calc", '  - id: AC-1\n    run: "python3 -m unittest -q test_calc"\n',
                             {"calc.py": CALC_OK, "test_calc.py": TEST_CALC},
                             protected="protected:\n  - test_calc.py\n")

    def test_happy_path(self):
        wt = self.calc_task()
        out = merge.merge_task(self.c, "T001")
        self.assertTrue(out.ok, out.message)
        self.assertEqual(self.status("T001"), "done")
        self.assertTrue((self.c / "calc.py").is_file())
        self.assertFalse(wt.exists())
        self.assertNotIn("task/T001-calc", git(self.c, "branch"))
        self.assertEqual(git(self.c, "status", "--porcelain", "--untracked-files=no").strip(), "")
        sealed = checks._load(self.house, "T001")["frozen"]["protected"]["test_calc.py"]
        self.assertEqual(len(sealed), 64)

    def test_red_branch_not_merged(self):
        self.add_task("T001", "calc", '  - id: AC-1\n    run: "python3 -m unittest -q test_calc"\n',
                      {"calc.py": CALC_BAD, "test_calc.py": TEST_CALC})
        head = git(self.c, "rev-parse", "HEAD")
        out = merge.merge_task(self.c, "T001")
        self.assertFalse(out.ok)
        self.assertIn("AC-1", out.message)
        self.assertEqual(self.status("T001"), "review")
        self.assertEqual(git(self.c, "rev-parse", "HEAD"), head)

    def test_regression_reverted(self):
        self.calc_task()
        self.assertTrue(merge.merge_task(self.c, "T001").ok)
        self.add_task("T002", "speed", '  - id: AC-1\n    run: "python3 -c \'import calc\'"\n',
                      {"calc.py": CALC_BAD})
        out = merge.merge_task(self.c, "T002")
        self.assertFalse(out.ok)
        self.assertTrue(out.reverted)
        self.assertIn("T001", out.message)
        self.assertEqual(self.status("T002"), "blocked")
        self.assertEqual((self.c / "calc.py").read_text(), CALC_OK)
        self.assertIn("T002 cofnięte", (self.house / "PROGRESS.md").read_text())
        self.assertTrue(checks.run_checks(self.house, "T001", self.c).green)

    def test_conflict_aborted(self):
        base = git(self.c, "rev-parse", "HEAD").strip()
        self.add_task("T001", "a", '  - id: AC-1\n    run: "true"\n', {"x.txt": "one\n"}, base=base)
        self.add_task("T002", "b", '  - id: AC-1\n    run: "true"\n', {"x.txt": "two\n"}, base=base)
        self.assertTrue(merge.merge_task(self.c, "T001").ok)
        head = git(self.c, "rev-parse", "HEAD")
        out = merge.merge_task(self.c, "T002")
        self.assertFalse(out.ok)
        self.assertIn("conflict", out.message)
        self.assertEqual(git(self.c, "rev-parse", "HEAD"), head)
        dirty = git(self.c, "status", "--porcelain", "--untracked-files=no").splitlines()
        self.assertEqual([ln for ln in dirty if ".house/" not in ln], [])

    def test_dirty_checkout_refused(self):
        self.calc_task()
        (self.c / ".gitignore").write_text("changed by hand\n")
        out = merge.merge_task(self.c, "T001")
        self.assertFalse(out.ok)
        self.assertIn("uncommitted", out.message)
        self.assertEqual((self.c / ".gitignore").read_text(), "changed by hand\n")
        self.assertFalse((self.c / "calc.py").exists())

    def test_manual_only_task_merged_not_done(self):
        self.add_task("T001", "pitch", "  - id: AC-1\n    manual: slides read well\n", {"pitch.md": "# Pitch\n"})
        out = merge.merge_task(self.c, "T001")
        self.assertTrue(out.ok, out.message)
        self.assertEqual(self.status("T001"), "merged")

    def test_missing_worktree(self):
        (self.house / "tasks/T001-x.md").write_text(task_text("T001", "task/T001-x", '  - id: AC-1\n    run: "true"\n'))
        checks.freeze(self.house, "T001")
        out = merge.merge_task(self.c, "T001")
        self.assertFalse(out.ok)
        self.assertIn("worktree", out.message)


    def test_legacy_unfrozen_done_task_is_skipped(self):
        (self.house / "tasks/T000-old.md").write_text(
            "---\nid: T000\ntitle: old\nstatus: done\nowner: null\nbranch: null\ndepends_on: []\n"
            "acceptance: [works]\n---\nx\n")
        git(self.c, "add", ".house")
        git(self.c, "commit", "-q", "-m", "legacy")
        self.calc_task()
        out = merge.merge_task(self.c, "T001")
        self.assertTrue(out.ok, out.message)

    def test_already_red_task_does_not_block_others(self):
        self.calc_task()
        self.assertTrue(merge.merge_task(self.c, "T001").ok)
        (self.c / "calc.py").write_text(CALC_BAD + "\n")
        git(self.c, "commit", "-qam", "human breaks calc")
        self.add_task("T002", "doc", '  - id: AC-1\n    run: "true"\n', {"doc.md": "x\n"})
        out = merge.merge_task(self.c, "T002")
        self.assertTrue(out.ok, out.message)
        self.assertIn("T001", out.message)  # warned as already red

    def test_remerge_after_revert(self):
        self.calc_task()
        self.assertTrue(merge.merge_task(self.c, "T001").ok)
        wt = self.add_task("T002", "mul", '  - id: AC-1\n    run: "python3 -c \'import mul\'"\n',
                           {"mul.py": "X = 1\n", "calc.py": CALC_BAD + "\n"})
        self.assertTrue(merge.merge_task(self.c, "T002").reverted)
        (wt / "calc.py").write_text(CALC_OK)
        git(wt, "commit", "-qam", "fix")
        tf = next((self.house / "tasks").glob("T002-*.md"))
        from houselib.fm import set_fields
        tf.write_text(set_fields(tf.read_text(), {"status": "review"}))
        out = merge.merge_task(self.c, "T002")
        self.assertTrue(out.ok, out.message)
        self.assertTrue((self.c / "mul.py").is_file())

    def test_reverted_rebase_refusal_blocks_for_a_human(self):
        self.calc_task()
        self.assertTrue(merge.merge_task(self.c, "T001").ok)
        wt = self.add_task("T002", "mul", '  - id: AC-1\n    run: "python3 -c \'import mul\'"\n',
                           {"mul.py": "X = 1\n", "calc.py": CALC_BAD + "\n"})
        self.assertTrue(merge.merge_task(self.c, "T002").reverted)
        (self.c / "calc.py").write_text(CALC_OK + "# main\n")
        git(self.c, "commit", "-qam", "main edits calc")
        self.set_review("T002")
        out = merge.merge_task(self.c, "T002")
        self.assertFalse(out.ok)
        self.assertIn("does not rebase cleanly", out.message)
        self.assertFalse(out.conflict, "a merge of main cannot fix a reverted task's replay: a human decides")
        self.assertEqual((out.status, self.status("T002")), ("blocked", "blocked"))
        self.assertLess(len(out.message), 600)
        self.assertNotIn("hint:", out.message)

    def set_review(self, tid: str) -> None:
        from houselib.fm import set_fields
        tf = next((self.house / "tasks").glob(f"{tid}-*.md"))
        tf.write_text(set_fields(tf.read_text(), {"status": "review"}))

    def test_tampered_frozen_criteria_refused(self):
        self.add_task("T001", "calc", '  - id: AC-1\n    run: "python3 -m unittest -q test_calc"\n',
                      {"calc.py": CALC_BAD, "test_calc.py": TEST_CALC})
        import json
        cf = self.house / "checks/T001.json"
        data = json.loads(cf.read_text())
        data["frozen"]["criteria"][0]["run"] = "true"
        cf.write_text(json.dumps(data))
        tf = self.house / "tasks/T001-calc.md"
        tf.write_text(tf.read_text().replace("python3 -m unittest -q test_calc", "true"))
        out = merge.merge_task(self.c, "T001")
        self.assertFalse(out.ok)
        self.assertIn("frozen", out.message)
        self.assertFalse((self.c / "calc.py").exists())

    def test_tampered_status_refused(self):
        self.calc_task()
        (self.house / "tasks/T009-x.md").write_text(task_text("T009", "task/T009-x", '  - id: AC-1\n    run: "true"\n'))
        git(self.c, "add", ".house")
        git(self.c, "commit", "-q", "-m", "plan T009")
        tf = self.house / "tasks/T009-x.md"
        tf.write_text(tf.read_text().replace("status: review", "status: merged"))
        out = merge.merge_task(self.c, "T001")
        self.assertFalse(out.ok)
        self.assertIn("T009", out.message)

    def test_dirty_worktree_refused(self):
        wt = self.calc_task()
        (wt / "stray.py").write_text("x = 1\n")
        out = merge.merge_task(self.c, "T001")
        self.assertFalse(out.ok)
        self.assertIn("uncommitted", out.message)

    def test_failed_revert_falls_back_to_reset(self):
        from unittest import mock
        self.calc_task()
        self.assertTrue(merge.merge_task(self.c, "T001").ok)
        self.add_task("T002", "speed", '  - id: AC-1\n    run: "python3 -c \'import calc\'"\n',
                      {"calc.py": CALC_BAD + "\n"})
        real = merge._git

        def fake(cwd, *args):
            if args[:1] == ("revert",):
                return real(cwd, "revert", "--bogus-option")
            return real(cwd, *args)
        with mock.patch.object(merge, "_git", side_effect=fake):
            out = merge.merge_task(self.c, "T002")
        self.assertFalse(out.ok)
        self.assertEqual((self.c / "calc.py").read_text(), CALC_OK)

    def test_rollback_keeps_commits_made_on_top_during_rechecks(self):
        from unittest import mock
        self.calc_task()
        self.assertTrue(merge.merge_task(self.c, "T001").ok)
        self.add_task("T002", "speed", '  - id: AC-1\n    run: "python3 -c \'import calc\'"\n',
                      {"calc.py": CALC_BAD})
        real = checks.run_checks
        done = []

        def on_main(house, tid, workdir, record=True):
            if Path(workdir) == self.c and not done:  # a parallel `house exp run` commits its csv meanwhile
                (self.house / "experiments.csv").write_text("id\nE-001\n")
                git(self.c, "add", ".house/experiments.csv")
                git(self.c, "commit", "-q", "-m", "chore(exp): E-001 keep", "--", ".house/experiments.csv")
                done.append(1)
            return real(house, tid, workdir, record)
        with mock.patch.object(merge.checks, "run_checks", side_effect=on_main):
            out = merge.merge_task(self.c, "T002")
        self.assertTrue(out.reverted, out.message)
        self.assertEqual((self.c / "calc.py").read_text(), CALC_OK)
        self.assertIn("chore(exp): E-001 keep", git(self.c, "log", "--format=%s"))
        self.assertEqual((self.house / "experiments.csv").read_text(), "id\nE-001\n")

    def test_branch_editing_protected_test_refused(self):
        (self.c / "test_calc.py").write_text(TEST_CALC)
        git(self.c, "add", "test_calc.py")
        git(self.c, "commit", "-q", "-m", "planner writes the test")
        self.add_task("T001", "calc", '  - id: AC-1\n    run: "python3 -m unittest -q test_calc"\n',
                      {"calc.py": CALC_BAD, "test_calc.py": TEST_CALC.replace("5)", "-1)")},
                      protected="protected:\n  - test_calc.py\n")
        out = merge.merge_task(self.c, "T001")
        self.assertFalse(out.ok)
        self.assertIn("test_calc.py", out.message)
        self.assertFalse((self.c / "calc.py").exists())

    def test_cli_guard_diff(self):
        import io
        from contextlib import redirect_stdout
        from unittest import mock

        from houselib import cli
        self.calc_task()
        with mock.patch.object(cli, "HOUSE_ROOT", self.root), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["guard", "diff", "c1", "T001"]), 0)
            (self.c / ".worktrees/T001-calc/.env.prod").write_text("K=sk-abcdefghijklmnopqrstuvwxyz0123\n")
            (self.c / ".worktrees/T001-calc/.gitignore").write_text("")
            self.assertEqual(cli.main(["guard", "diff", "c1", "T001"]), 1)
        self.assertIn(".env.prod", out.getvalue())

    def test_codex_driver_runs_guard_diff(self):
        from houselib import adapters
        self.assertIn("house guard diff", adapters.CODEX_DRIVER)

    def test_interrupt_after_merge_rolls_back(self):
        from unittest import mock
        self.calc_task()
        head = git(self.c, "rev-parse", "HEAD").strip()
        real = checks.run_checks
        calls = {"n": 0}

        def interrupt_on_main(house, tid, workdir, record=True):
            calls["n"] += 1
            if calls["n"] > 1:  # first call = the branch check; then main
                raise KeyboardInterrupt
            return real(house, tid, workdir, record)
        with mock.patch.object(checks, "run_checks", side_effect=interrupt_on_main), \
                self.assertRaises(KeyboardInterrupt):
            merge.merge_task(self.c, "T001")
        self.assertFalse((self.c / "calc.py").exists())
        self.assertEqual(git(self.c, "diff", "--stat", head, "HEAD", "--", ".", ":!.house").strip(), "")

    def test_cli_exit_codes(self):
        import io
        from contextlib import redirect_stdout
        from unittest import mock

        from houselib import cli
        self.calc_task()
        with mock.patch.object(cli, "HOUSE_ROOT", self.root), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["merge", "c1", "T001"]), 0)
            self.assertEqual(cli.main(["merge", "c1", "T001"]), 1)  # no branch/worktree any more
        self.assertIn("T001 done", out.getvalue())


class CliMergeRegisterTest(unittest.TestCase):
    """`house merge` records one `merge` row per run in the attempt register and commits it."""
    tearDown, add_task, calc_task = MergeTest.tearDown, MergeTest.add_task, MergeTest.calc_task

    def setUp(self):
        MergeTest.setUp(self)
        patch = mock.patch.object(cli, "HOUSE_ROOT", self.root)
        patch.start()
        self.addCleanup(patch.stop)

    def run_merge(self, tid="T001") -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["merge", "c1", tid])
        return code, err.getvalue()

    def register_clean(self) -> bool:
        return git(self.c, "status", "--porcelain", "--", ".house/attempts.jsonl").strip() == ""

    def test_cli_merge_records_row(self):
        wt = self.calc_task()
        (wt / "scratch.txt").write_text("x\n")
        code, _ = self.run_merge()  # uncommitted work in the worktree: the gate refuses it
        self.assertEqual(code, 1)
        (row,) = attempts.rows(self.house, "T001")
        self.assertEqual((row.source, row.n, row.session, row.log, row.tool, row.agent, row.outcome),
                         ("merge", 1, "", "", "claude", "coder", "merge-refused"))
        self.assertEqual(row.questions, [])
        self.assertIn("worktree ./.worktrees/T001-calc", row.message)  # no absolute path in the register
        self.assertNotIn(str(self.c), (self.house / "attempts.jsonl").read_text())
        self.assertNotIn(str(self.c.resolve()), (self.house / "attempts.jsonl").read_text())
        self.assertTrue(row.start and row.end)
        self.assertEqual(row.head, git(self.c, "rev-parse", "task/T001-calc").strip())
        self.assertEqual(row.base, git(self.c, "merge-base", "HEAD", "task/T001-calc").strip())
        self.assertTrue(self.register_clean())

    def test_cli_merge_conflict_row_has_reason(self):
        wt = self.calc_task()
        (self.c / "calc.py").write_text("X = 0\n")  # main adds calc.py too: the gate's merge conflicts
        git(self.c, "add", "calc.py")
        git(self.c, "commit", "-qm", "calc on main")
        self.assertEqual(self.run_merge()[0], 1)
        (row,) = attempts.rows(self.house, "T001")
        self.assertEqual((row.outcome, row.reason), ("merge-refused", "conflict"))
        self.assertTrue(wt.is_dir())
        (wt / "scratch.txt").write_text("x\n")
        self.assertEqual(self.run_merge()[0], 1)  # refused for another reason: no conflict
        self.assertEqual(attempts.rows(self.house, "T001")[-1].reason, "")

    def test_cli_merge_refused_before_the_gate_records_nothing(self):
        self.calc_task()
        git(self.c, "worktree", "remove", "--force", str(self.c / ".worktrees/T001-calc"))
        self.assertEqual(self.run_merge()[0], 1)  # no worktree: refused before anything is evaluated
        task = self.house / "tasks/T001-calc.md"
        task.write_text(task.read_text().replace("status: review", "status: todo"))
        git(self.c, "commit", "-qam", "todo")
        self.assertEqual(self.run_merge()[0], 1)  # not in review
        self.assertEqual(attempts.rows(self.house, "T001"), [])
        self.assertTrue(self.register_clean())

    def test_cli_merge_success_row_and_numbering(self):
        self.calc_task()
        head = git(self.c, "rev-parse", "task/T001-calc").strip()
        base = git(self.c, "merge-base", "HEAD", "task/T001-calc").strip()
        code, _ = self.run_merge()
        self.assertEqual(code, 0)
        (row,) = attempts.rows(self.house, "T001")
        self.assertEqual((row.outcome, row.head, row.base, row.n), ("merged", head, base, 1))
        self.assertTrue(self.register_clean())
        self.assertEqual(self.run_merge()[0], 1)  # already done: refused before the gate, no row
        self.assertEqual([(r.n, r.outcome) for r in attempts.rows(self.house, "T001")], [(1, "merged")])
        self.assertTrue(self.register_clean())

    def test_recording_failure_keeps_the_exit_code(self):
        self.calc_task()
        with mock.patch("houselib.attempts.record", side_effect=OSError("disk full")):
            code, err = self.run_merge()
        self.assertEqual(code, 0)
        self.assertIn("attempt register", err)

    def test_forged_register_is_refused_and_not_committed(self):
        self.calc_task()
        (self.house / "attempts.jsonl").write_text('{"task": "T001", "n": 7, "outcome": "merged"}\n')
        code, err = self.run_merge()
        self.assertEqual(code, 1)
        self.assertIn("attempt register", err)
        self.assertEqual(git(self.c, "ls-files", ".house/attempts.jsonl").strip(), "")

    def test_other_uncommitted_house_state_is_left_alone(self):
        self.calc_task()
        (self.house / "notes.txt").write_text("x\n")
        git(self.c, "worktree", "remove", "--force", str(self.c / ".worktrees/T001-calc"))
        self.run_merge()
        self.assertIn("?? .house/notes.txt", git(self.c, "status", "--porcelain"))
        self.assertTrue(self.register_clean())

    def test_merge_gate_commit_never_takes_the_register(self):
        (self.house / "notes.txt").write_text("x\n")
        (self.house / "attempts.jsonl").write_text('{"task": "T001", "n": 5, "outcome": "merged"}\n')
        merge._commit_house(self.c, "state")
        self.assertIn("notes.txt", git(self.c, "show", "--name-only", "--format=", "HEAD"))
        self.assertEqual(git(self.c, "ls-files", ".house/attempts.jsonl").strip(), "")
        self.assertIn("?? .house/attempts.jsonl", git(self.c, "status", "--porcelain"))

    def test_merge_gate_commit_never_takes_a_register_edit(self):
        night.record_attempt(self.c, attempts.Row(task="T001", n=1, source="night", agent="coder", tool="claude"), "r")
        reg = self.house / "attempts.jsonl"
        reg.write_text(reg.read_text() + '{"task": "T001", "n": 9}\n')
        (self.house / "notes.txt").write_text("x\n")
        merge._commit_house(self.c, "state")
        self.assertNotIn('"n": 9', git(self.c, "show", "HEAD:.house/attempts.jsonl"))
        self.assertIn(" M .house/attempts.jsonl", git(self.c, "status", "--porcelain"))

    def test_cli_merge_row_tool_is_codex_for_codex_agent(self):
        self.calc_task()
        task = self.house / "tasks/T001-calc.md"
        task.write_text(task.read_text().replace("owner: coder\n", "owner: coder\nagent: coder-codex\n"))
        git(self.c, "commit", "-qam", "agent")
        self.assertEqual(self.run_merge()[0], 0)
        (row,) = attempts.rows(self.house, "T001")
        self.assertEqual((row.agent, row.tool), ("coder-codex", "codex"))


if __name__ == "__main__":
    unittest.main()
