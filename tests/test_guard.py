"""`house guard`: rules that block edits to protected files, secret commits and unsafe pushes."""
import tempfile
import unittest
from pathlib import Path

from houselib import checks, guard, scaffold
from tests.helpers import FAKE_TOOLS, git, make_root

TASK = ("---\nid: T001\ntitle: x\nstatus: todo\nowner: null\nbranch: task/T001-x\ndepends_on: []\n"
        "acceptance:\n  - id: AC-1\n    run: \"true\"\nprotected:\n  - test_a.py\n  - later.py\n---\nx\n")


class GuardTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_root(Path(self._tmp.name))
        self.c = scaffold.new_contest("c1", None, root=self.root, detect=lambda: FAKE_TOOLS)
        (self.c / "test_a.py").write_text("assert True\n")
        (self.c / ".house/tasks/T001-x.md").write_text(TASK)
        git(self.c, "add", "-A")
        git(self.c, "commit", "-q", "-m", "base")
        checks.freeze(self.c / ".house", "T001")
        self.wt = self.c / ".worktrees/T001-x"
        git(self.c, "worktree", "add", "-q", str(self.wt), "-b", "task/T001-x")

    def tearDown(self):
        self._tmp.cleanup()

    def test_contest_root_from_worktree_and_subdir(self):
        (self.wt / "src").mkdir()
        self.assertEqual(guard.contest_root(self.wt / "src"), self.c.resolve())
        self.assertEqual(guard.contest_root(self.c), self.c.resolve())
        self.assertIsNone(guard.contest_root(Path(self._tmp.name)))

    def test_repo_relative(self):
        self.assertEqual(guard.repo_relative(str(self.wt / "test_a.py"), self.wt, self.c), "test_a.py")
        self.assertEqual(guard.repo_relative("test_a.py", self.wt, self.c), "test_a.py")
        self.assertEqual(guard.repo_relative(str(self.c / ".house/checks/T001.json"), self.c, self.c),
                         ".house/checks/T001.json")
        self.assertIsNone(guard.repo_relative("/etc/passwd", self.c, self.c))

    def test_protected_paths_only_hashed(self):
        self.assertEqual(guard.protected_paths(self.c), {"test_a.py": "T001"})

    def test_local_scorer_is_protected(self):
        (self.c / "score.py").write_text("print(1)\n")
        (self.c / ".house/spec.yaml").write_text("scoring:\n  local_scorer: python3 score.py --split val\n")
        self.assertIn("score.py", guard.protected_paths(self.c))

    def test_check_edit(self):
        self.assertIn("T001", guard.check_edit("test_a.py", self.c))
        self.assertIsNotNone(guard.check_edit(".house/checks/T001.json", self.c))
        self.assertIsNone(guard.check_edit("later.py", self.c))
        self.assertIsNone(guard.check_edit("src/app.py", self.c))

    def test_bash_writes_to_protected(self):
        for cmd in ("echo x >> test_a.py", "sed -i 's/a/b/' test_a.py", "rm -f test_a.py",
                    "cp other.py test_a.py", "cat x | tee test_a.py", "true && mv test_a.py b.py",
                    "echo '{}' > .house/checks/T001.json"):
            self.assertIsNotNone(guard.check_bash(cmd, self.wt, self.c), cmd)
        for cmd in ("cat test_a.py", "python3 -m pytest test_a.py", "cp test_a.py /tmp/copy.py", "grep x test_a.py"):
            self.assertIsNone(guard.check_bash(cmd, self.wt, self.c), cmd)

    def test_git_add_secrets(self):
        (self.wt / ".env").write_text("KEY=1\n")  # .env* is in the contest .gitignore
        self.assertIsNone(guard.check_bash("git add -A", self.wt, self.c))
        (self.wt / ".gitignore").write_text("")
        self.assertIsNotNone(guard.check_bash("git add -A", self.wt, self.c))
        self.assertIsNotNone(guard.check_bash("git add .env", self.wt, self.c))
        (self.wt / ".env").unlink()
        (self.wt / "cfg.py").write_text('API_KEY = "sk-abcdefghijklmnopqrstuvwxyz123456"\n')
        self.assertIsNotNone(guard.check_bash("git add cfg.py", self.wt, self.c))
        self.assertIsNotNone(guard.check_bash("git commit -am wip", self.wt, self.c) or
                             guard.check_bash("git add . && git commit -m x", self.wt, self.c))
        (self.wt / "cfg.py").write_text("X = 1\n")
        self.assertIsNone(guard.check_bash("git add cfg.py && git commit -m ok", self.wt, self.c))

    def test_git_push_rules(self):
        self.assertIsNone(guard.check_bash("git push origin task/T001-x", self.wt, self.c))
        self.assertIsNotNone(guard.check_bash("git push public main", self.c, self.c))
        self.assertIsNotNone(guard.check_bash("git push --force origin main", self.c, self.c))
        self.assertIsNotNone(guard.check_bash("git push origin +main", self.c, self.c))
        self.assertIsNone(guard.check_bash("git push -f origin task/T001-x", self.wt, self.c))
        (self.c / ".house/spec.yaml").write_text("guard:\n  push_allow: [origin, team]\n")
        self.assertIsNone(guard.check_bash("git push team task/T001-x", self.wt, self.c))

    def test_diff_violations(self):
        self.assertEqual(guard.diff_violations(self.c, "T001", self.wt), [])
        (self.wt / "test_a.py").write_text("assert False\n")
        git(self.wt, "commit", "-qam", "loosen")
        (self.wt / ".env.local").write_text("X=1\n")
        found = guard.diff_violations(self.c, "T001", self.wt)
        self.assertTrue(any("test_a.py" in v for v in found), found)

    def test_frozen_tampering(self):
        import json
        cf = self.c / ".house/checks/T001.json"
        data = json.loads(cf.read_text())
        data["frozen"]["criteria"][0]["run"] = "exit 0"
        cf.write_text(json.dumps(data))
        self.assertTrue(guard.frozen_tampering(self.c))


if __name__ == "__main__":
    unittest.main()
