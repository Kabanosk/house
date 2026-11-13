"""`house merge`: check the branch, merge, re-check every merged task on main, revert on regression."""
import tempfile
import unittest
from pathlib import Path

from houselib import checks, merge, scaffold
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


if __name__ == "__main__":
    unittest.main()
