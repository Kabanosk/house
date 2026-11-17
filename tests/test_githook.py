"""`houselib.githook` and `bin/house-githook`: git hooks that enforce the guard rules in task worktrees."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from houselib import HOUSE_ROOT, checks, githook, guard, scaffold
from tests.helpers import FAKE_TOOLS, git, make_root

TASK = ("---\nid: T001\ntitle: x\nstatus: todo\nowner: null\nbranch: task/T001-x\ndepends_on: []\n"
        "acceptance:\n  - id: AC-1\n    run: \"true\"\nprotected:\n  - test_a.py\n---\nx\n")
SECRET = 'API_KEY = "sk-abcdefghijklmnopqrstuvwxyz123456"\n'


def run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


class HookCase(unittest.TestCase):
    """A contest with a frozen task, a task worktree and the hooks installed (pointing at the real checkout)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = make_root(self.tmp)
        self.c = scaffold.new_contest("c1", None, root=self.root, detect=lambda: FAKE_TOOLS)
        (self.c / "test_a.py").write_text("assert True\n")
        (self.c / ".house/tasks/T001-x.md").write_text(TASK)
        git(self.c, "add", "-A")
        git(self.c, "commit", "-q", "-m", "base")
        checks.freeze(self.c / ".house", "T001")
        self.wt = self.c / ".worktrees/T001-x"
        git(self.c, "worktree", "add", "-q", str(self.wt), "-b", "task/T001-x")
        self.result = githook.install(self.c, HOUSE_ROOT)

    def tearDown(self):
        self._tmp.cleanup()

    def commit(self, cwd: Path, *paths: str) -> subprocess.CompletedProcess:
        git(cwd, "add", "-f", *paths)
        return run(cwd, "commit", "-q", "-m", "work")


class InstallTest(HookCase):
    def test_writes_executable_wrappers(self):
        self.assertEqual(self.result.written, ["pre-commit", "pre-push"])
        self.assertEqual(self.result.problems, [])
        for name in githook.HOOKS:
            path = self.c / ".git/hooks" / name
            self.assertTrue(path.stat().st_mode & 0o100, name)
            text = path.read_text()
            self.assertIn(githook.MARKER, text)
            self.assertIn(f'"$hook" {name} "$@"', text)
            self.assertIn(str(HOUSE_ROOT / "bin/house-githook"), text)

    def test_idempotent_and_refreshes_when_root_moves(self):
        self.assertEqual(githook.install(self.c, HOUSE_ROOT).written, [])
        self.assertEqual(githook.install(self.c, self.root).written, ["pre-commit", "pre-push"])

    def test_inspect_without_writing(self):
        (self.c / ".git/hooks/pre-push").unlink()
        problems = githook.install(self.c, HOUSE_ROOT, write=False).problems
        self.assertEqual(len(problems), 1)
        self.assertIn("pre-push is missing", problems[0])
        self.assertFalse((self.c / ".git/hooks/pre-push").exists())
        githook.install(self.c, HOUSE_ROOT)
        problems = githook.install(self.c, self.root, write=False).problems
        self.assertEqual(len(problems), 2)
        self.assertTrue(all("outdated" in p for p in problems))

    def test_foreign_hook_is_left_alone(self):
        path = self.c / ".git/hooks/pre-commit"
        path.write_text("#!/bin/sh\nexit 0\n")
        res = githook.install(self.c, self.root)
        self.assertEqual(path.read_text(), "#!/bin/sh\nexit 0\n")
        self.assertTrue(any("is not a house hook" in p for p in res.problems))
        self.assertEqual(res.written, ["pre-push"])

    def test_hookspath_is_respected(self):
        git(self.c, "config", "core.hooksPath", str(self.tmp / "team-hooks"))
        res = githook.install(self.c, self.root)
        self.assertEqual(res.written, [])
        self.assertIn("core.hooksPath", res.problems[0])
        self.assertFalse((self.tmp / "team-hooks").exists())

    def test_adapt_installs_hooks(self):
        from houselib import adapters
        (self.c / ".git/hooks/pre-commit").unlink()
        (self.c / ".git/hooks/pre-push").unlink()
        adapters.adapt(self.c, self.root, detect=lambda: FAKE_TOOLS)
        self.assertTrue((self.c / ".git/hooks/pre-commit").exists())


class PreCommitTest(HookCase):
    def test_secret_is_refused_in_a_worktree(self):
        (self.wt / "cfg.py").write_text(SECRET)
        r = self.commit(self.wt, "cfg.py")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("cfg.py looks like a secret", r.stderr)
        self.assertEqual(run(self.wt, "log", "--oneline", "-1").stdout.count("work"), 0)

    def test_dot_env_is_refused_even_when_added_by_force(self):
        (self.wt / ".env").write_text("KEY=1\n")
        r = self.commit(self.wt, ".env")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(".env looks like a secret", r.stderr)

    def test_clean_file_is_committed(self):
        (self.wt / "app.py").write_text("X = 1\n")
        r = self.commit(self.wt, "app.py")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_secret_judged_on_staged_content(self):
        (self.wt / "cfg.py").write_text("X = 1\n")
        git(self.wt, "add", "cfg.py")
        (self.wt / "cfg.py").write_text(SECRET)  # unstaged edit must not matter
        self.assertEqual(run(self.wt, "commit", "-q", "-m", "x").returncode, 0)

    def test_commit_a_is_checked_too(self):
        (self.wt / "app.py").write_text("X = 1\n")
        self.assertEqual(self.commit(self.wt, "app.py").returncode, 0)
        (self.wt / "app.py").write_text(SECRET)
        r = run(self.wt, "commit", "-q", "-am", "oops")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("app.py looks like a secret", r.stderr)

    def test_house_files_are_refused(self):
        (self.wt / ".house/notes.md").write_text("n\n")
        r = self.commit(self.wt, ".house/notes.md")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(".house/notes.md: task branches must not carry .house/ files", r.stderr)

    def test_protected_test_change_is_refused(self):
        (self.wt / "test_a.py").write_text("assert False\n")
        r = self.commit(self.wt, "test_a.py")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("test_a.py is protected (T001)", r.stderr)

    def test_protected_file_deletion_is_refused(self):
        r = run(self.wt, "rm", "-q", "test_a.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = run(self.wt, "commit", "-q", "-m", "rm")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("test_a.py is protected", r.stderr)

    def test_main_checkout_is_not_checked(self):
        (self.c / "cfg.py").write_text(SECRET)
        r = self.commit(self.c, "cfg.py")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_not_a_contest_is_not_checked(self):
        repo = self.tmp / "plain"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.name", "n")
        git(repo, "config", "user.email", "e@x")
        (repo / "a").write_text("1")
        git(repo, "add", "a")
        git(repo, "commit", "-q", "-m", "a")
        other = self.tmp / "plain-wt"
        git(repo, "worktree", "add", "-q", str(other), "-b", "b")
        (other / "cfg.py").write_text(SECRET)
        self.assertEqual(githook.pre_commit(other), [])

    def test_merge_in_progress_skips_protected_but_not_secrets(self):
        (self.wt / "test_a.py").write_text("assert False\n")
        (self.wt / "cfg.py").write_text(SECRET)
        git(self.wt, "add", "-f", "test_a.py", "cfg.py")
        gitdir = Path(git(self.wt, "rev-parse", "--absolute-git-dir").strip())
        (gitdir / "MERGE_HEAD").write_text(git(self.wt, "rev-parse", "HEAD"))
        problems = githook.pre_commit(self.wt)
        self.assertEqual(problems, ["cfg.py looks like a secret (keep keys in an ignored .env)"])

    def test_internal_error_fails_open(self):
        r = subprocess.run([str(HOUSE_ROOT / "bin/house-githook"), "nonsense"], cwd=self.wt, capture_output=True,
                           text=True, check=False)
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_wrapper_skips_when_script_is_missing(self):
        githook.install(self.c, self.tmp / "gone")
        (self.wt / "cfg.py").write_text(SECRET)
        r = self.commit(self.wt, "cfg.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("git hook skipped", r.stderr)


class PrePushTest(HookCase):
    def setUp(self):
        super().setUp()
        for name in ("origin", "evil"):
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.tmp / f"{name}.git")], check=True)
            git(self.c, "remote", "add", name, str(self.tmp / f"{name}.git"))
        git(self.c, "push", "-q", "origin", "main")  # the main checkout is not checked
        (self.wt / "app.py").write_text("X = 1\n")
        self.assertEqual(self.commit(self.wt, "app.py").returncode, 0)

    def test_allowed_remote_branch_push(self):
        r = run(self.wt, "push", "-q", "origin", "task/T001-x")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_other_remote_is_refused(self):
        r = run(self.wt, "push", "-q", "evil", "task/T001-x")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("push to `evil` is not allowed", r.stderr)

    def test_url_instead_of_remote_name_is_refused(self):
        r = run(self.wt, "push", "-q", str(self.tmp / "origin.git"), "task/T001-x")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("is not allowed", r.stderr)

    def test_fast_forward_to_main_is_allowed(self):
        r = run(self.wt, "push", "-q", "origin", "task/T001-x:main")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_force_push_to_main_is_refused(self):
        run(self.wt, "push", "-q", "origin", "task/T001-x:main")
        git(self.wt, "reset", "-q", "--hard", "HEAD~1")
        (self.wt / "other.py").write_text("Y = 2\n")
        self.assertEqual(self.commit(self.wt, "other.py").returncode, 0)
        r = run(self.wt, "push", "-q", "--force", "origin", "task/T001-x:main")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("force push (not a fast-forward)", r.stderr)

    def test_force_push_to_a_task_branch_is_allowed(self):
        run(self.wt, "push", "-q", "origin", "task/T001-x")
        git(self.wt, "commit", "-q", "--amend", "-m", "again")
        r = run(self.wt, "push", "-q", "--force", "origin", "task/T001-x")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_deleting_main_is_refused(self):
        r = run(self.wt, "push", "-q", "origin", ":main")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("deleting the contest's main branch", r.stderr)

    def test_main_checkout_push_is_not_checked(self):
        r = run(self.c, "push", "-q", "evil", "main")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_push_allow_from_spec(self):
        spec = self.c / ".house/spec.yaml"
        spec.write_text(spec.read_text().replace("push_allow: [origin]", "push_allow: [origin, evil]"))
        r = run(self.wt, "push", "-q", "evil", "task/T001-x")
        self.assertEqual(r.returncode, 0, r.stderr)


class AgentSideGuardTest(HookCase):
    """`guard.check_bash` / `check_edit` keep the agent from switching the hooks off."""

    def test_no_verify_and_hooks_path_are_refused(self):
        for cmd in ("git commit --no-verify -m x", "git commit -nm x", "git push --no-verify origin task/T001-x",
                    "git -c core.hooksPath=/dev/null commit -m x", "git config core.hooksPath /tmp/none",
                    "git config --worktree core.hookspath /tmp/none", "GIT_CONFIG_KEY_0=core.hooksPath git commit -m x",
                    "echo exit 0 > .git/hooks/pre-commit", "rm .git/hooks/pre-push",
                    "sed -i s/x/y/ .git/config"):
            self.assertIsNotNone(guard.check_bash(cmd, self.c, self.c), cmd)

    def test_ordinary_git_commands_are_allowed(self):
        for cmd in ("git commit -m x", "git commit -am wip", "git push origin task/T001-x", "git push -n origin x",
                    "git config --get core.hooksPath", "git config --list", "git log --oneline",
                    "cat .git/config", "git status"):
            self.assertIsNone(guard.check_bash(cmd, self.c, self.c), cmd)

    def test_heredoc_text_about_hooks_is_data(self):
        cmd = "cat <<'EOF' > notes.md\nthe core.hooksPath setting\nEOF"
        self.assertIsNone(guard.check_bash(cmd, self.c, self.c))

    def test_edit_tools_cannot_touch_hooks(self):
        self.assertIsNotNone(guard.check_edit(".git/hooks/pre-commit", self.c))
        self.assertIsNotNone(guard.check_edit(".git/config", self.c))
        self.assertIsNone(guard.check_edit("src/app.py", self.c))


class HelpersTest(HookCase):
    def test_looks_secret(self):
        self.assertTrue(guard.looks_secret("cfg.py", SECRET.encode()))
        self.assertTrue(guard.looks_secret(".env", b"A=1\n"))
        self.assertFalse(guard.looks_secret(".env.example", b"A=\n"))
        self.assertFalse(guard.looks_secret("model.bin", b"\0" + SECRET.encode()))

    def test_protected_violation(self):
        pinned = b"assert True\n"
        self.assertIsNone(guard.protected_violation(self.c, "test_a.py", pinned))
        self.assertIn("T001", guard.protected_violation(self.c, "test_a.py", b"x") or "")
        self.assertIsNotNone(guard.protected_violation(self.c, "test_a.py", None))
        self.assertIsNone(guard.protected_violation(self.c, "app.py", b"x"))


if __name__ == "__main__":
    unittest.main()
