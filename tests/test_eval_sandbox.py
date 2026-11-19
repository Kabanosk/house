"""`TempRoot`: an isolated temporary house root with a fixture contest, and the fixture builders (no agents run)."""
import atexit
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from houselib import HOUSE_ROOT, checks, merge, registry, transcript
from houselib.evals import fixtures, sandbox
from houselib.evals.sandbox import TempRoot
from tests.helpers import git, make_root

# A PATH with only what house needs, so `doctor.detect` (nvidia-smi, dpkg, ollama ...) is fast in the subprocesses.
_FAST_BIN = Path(tempfile.mkdtemp(prefix="house-fastbin-"))
atexit.register(shutil.rmtree, _FAST_BIN, ignore_errors=True)
for _tool in ("git", "python3", "env", "sh", "bash"):
    _found = shutil.which(_tool)
    if _found:
        (_FAST_BIN / _tool).symlink_to(_found)


def fast_env() -> dict[str, str]:
    """The current environment with the fast PATH (read at call time, so patched variables are included)."""
    return {**os.environ, "PATH": str(_FAST_BIN)}


def worktrees(repo: Path) -> list[str]:
    return [line for line in git(repo, "worktree", "list", "--porcelain").splitlines() if line.startswith("worktree ")]


def refs(repo: Path) -> str:
    return git(repo, "for-each-ref", "--format=%(refname) %(objectname)")


def tar_bytes(*members: tarfile.TarInfo, data: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for m in members:
            body = (data or {}).get(m.name)
            if body is not None:
                m.size = len(body)
            tar.addfile(m, io.BytesIO(body) if body is not None else None)
    return buf.getvalue()


class ScratchBase(unittest.TestCase):
    """A scratch house repo (own git history, copy of the code) standing in for HOUSE_ROOT, and a fake projects dir."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        scratch_parent = self.tmp / "scratch"
        scratch_parent.mkdir()
        self.src = make_root(scratch_parent)
        shutil.copytree(HOUSE_ROOT / "houselib", self.src / "houselib", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(HOUSE_ROOT / "contests.yaml", self.src / "contests.yaml")
        (self.src / ".gitignore").write_text("contests/\n__pycache__/\n")
        git(self.src, "add", "-A")
        git(self.src, "commit", "-q", "-m", "scratch house")
        self.head = git(self.src, "rev-parse", "HEAD").strip()
        patcher = mock.patch.object(sandbox, "HOUSE_ROOT", self.src)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.base = self.tmp / "evals"
        self.base.mkdir()
        self.projects = self.tmp / "projects"
        self.projects.mkdir()
        self.addCleanup(self._tmp.cleanup)

    def temp_root(self, fixture=None, commit: str | None = None, **kw) -> TempRoot:
        kw.setdefault("base_env", fast_env())
        return TempRoot(commit or self.head, fixture or {"name": "small_build", "contest": "fx"}, base=self.base,
                        projects=self.projects, **kw)

    def assert_src_untouched(self, refs_before: str):
        self.assertEqual(worktrees(self.src), [f"worktree {self.src}"])
        self.assertEqual(refs(self.src), refs_before)
        self.assertEqual(git(self.src, "remote").strip(), "")


class TempRootTest(ScratchBase):
    def test_hidden_dir_is_made_outside_the_contest_and_a_builder_can_fill_it(self):
        def builder(contest, *, env=None, **params):
            fixtures.write_hidden(contest, "chain", {"test_x.py": "# hidden\n"})
            return fixtures.small_build(contest, env=env, **params)

        with mock.patch.dict(fixtures.BUILDERS, {"hidden_fx": builder}):
            with self.temp_root({"name": "hidden_fx", "contest": "fx"}) as t:
                hidden = t.tmp / "hidden"
                self.assertEqual((hidden / "chain/test_x.py").read_text(), "# hidden\n")
                self.assertEqual(hidden.stat().st_mode & 0o777, 0o700)
                self.assertNotIn("test_x.py", git(t.contest, "ls-files"))
                self.assertFalse(list(t.root.rglob("test_x.py")))
        with self.temp_root() as t:  # the dir exists for every run (the agent container masks it)
            self.assertTrue((t.tmp / "hidden").is_dir())

    def test_root_is_the_commit_not_the_working_tree(self):
        (self.src / "dirty.txt").write_text("uncommitted")
        (self.src / "bin/house-hook").write_text("changed in the working tree only\n")
        with self.temp_root() as t:
            self.assertEqual(t.source_commit, self.head)
            self.assertEqual((t.root / ".house-eval-commit").read_text().strip(), self.head)
            self.assertFalse((t.root / "dirty.txt").exists())
            self.assertNotEqual((t.root / "bin/house-hook").read_text(), "changed in the working tree only\n")
            exported = set(git(t.root, "ls-files").split()) - {".house-eval-commit"}
            tracked = set(git(self.src, "ls-tree", "-r", "--name-only", self.head).split())
            self.assertEqual(exported, {p for p in tracked if not sandbox.excluded(p)})
            self.assertTrue(any(sandbox.excluded(p) for p in tracked))  # the scratch commit has houselib/evals
            self.assertTrue(os.access(t.root / "bin/house", os.X_OK))
        self.assertTrue((self.src / "dirty.txt").exists())  # the real working tree was never touched

    def test_root_is_its_own_repo_sharing_nothing_with_the_source(self):
        refs_before = refs(self.src)
        config_before = git(self.src, "config", "--local", "--list")
        with self.temp_root() as t:
            self.assertEqual(git(t.root, "remote").strip(), "")
            common = Path(git(t.root, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()).resolve()
            self.assertTrue(common.is_relative_to(t.tmp), common)
            self.assertNotEqual(common, (self.src / ".git").resolve())
            self.assertEqual(git(t.root, "rev-list", "--count", "HEAD").strip(), "2")  # export + contest registered
            self.assertEqual(git(t.root, "branch", "--show-current").strip(), "main")
            self.assertNotEqual(git(t.root, "rev-parse", "HEAD").strip(), self.head)  # a new history, not the owner's
            self.assertEqual(git(t.root, "config", "user.email").strip(), sandbox.EVAL_IDENTITY[1])
            self.assertEqual(git(t.root, "status", "--porcelain").strip(), "")
            self.assertEqual(self.head, git(self.src, "rev-parse", "HEAD").strip())
            self.assert_src_untouched(refs_before)
            # a branch, a commit and a config change made "by the agent" in the temp root stay in the temp root
            git(t.root, "checkout", "-q", "-b", "agent-branch")
            git(t.root, "config", "user.name", "Agent")
        self.assert_src_untouched(refs_before)
        self.assertEqual(git(self.src, "config", "--local", "--list"), config_before)

    def test_layout_and_contest(self):
        with self.temp_root() as t:
            self.assertEqual(t.root.name, "house")
            self.assertEqual(t.name, "fx")
            self.assertEqual(t.contest, t.root / "contests/fx")
            self.assertTrue((t.contest / ".house/checks/T001.json").is_file())
            self.assertEqual(t.info["task_ids"], ["T001"])
            self.assertEqual(t.info["initial_commit"], git(t.contest, "rev-parse", "main").strip())
            self.assertEqual(git(t.contest, "status", "--porcelain").strip(), "")
            self.assertEqual(git(t.contest, "rev-parse", "origin/main").strip(), t.info["initial_commit"])
            self.assertEqual(t.root.resolve().parent, t.tmp.resolve())
            self.assertEqual(registry.load(t.root / "contests.yaml"), {"fx": ""})

    def test_baseline_hashes_every_file_tracked_at_the_initial_commit(self):
        with self.temp_root() as t:
            tracked = git(t.contest, "ls-tree", "-r", "--name-only", t.info["initial_commit"]).split()
            self.assertEqual(sorted(t.baseline), sorted(tracked))
            data = (t.contest / "test_calc.py").read_bytes()
            blob = git(t.contest, "rev-parse", f"{t.info['initial_commit']}:test_calc.py").strip()
            self.assertEqual(t.baseline["test_calc.py"], (blob, hashlib.sha256(data).hexdigest()))

    def test_contests_yaml_lists_only_the_fixture(self):
        self.assertNotEqual(registry.load(self.src / "contests.yaml"), {})  # the scratch repo tracks the real list
        registry.add(self.src / "contests.yaml", "someone-elses-contest", "git@example.com:x/y.git")
        git(self.src, "commit", "-qam", "list a contest")
        head = git(self.src, "rev-parse", "HEAD").strip()
        with self.temp_root(commit=head) as t:
            self.assertEqual(list(registry.load(t.root / "contests.yaml")), ["fx"])
            self.assertFalse((t.root / "contests/someone-elses-contest").exists())

    def test_adapt_points_hooks_at_the_temp_root(self):
        with self.temp_root() as t:
            settings = (t.contest / ".claude/settings.local.json").read_text()
            self.assertIn(str(t.root / "bin/house-hook"), settings)
            self.assertNotIn(str(self.src), settings)

    def test_origin_is_a_bare_repo_in_the_temp_dir(self):
        with self.temp_root() as t:
            url = git(t.contest, "remote", "get-url", "origin").strip()
            self.assertEqual(Path(url), t.tmp / "origin.git")
            self.assertEqual(git(t.tmp / "origin.git", "rev-parse", "--is-bare-repository").strip(), "true")
            self.assertEqual(git(t.tmp / "origin.git", "rev-parse", "main").strip(),
                             git(t.contest, "rev-parse", "main").strip())

    def test_review_ready_pushes_the_task_branch_too(self):
        with self.temp_root({"name": "review_ready", "contest": "fx"}) as t:
            self.assertEqual(git(t.tmp / "origin.git", "rev-parse", t.info["branch"]).strip(), t.info["branch_commit"])

    def test_house_from_the_temp_root_resolves_the_temp_contest(self):
        contests_before = sorted(p.name for p in (self.src / "contests").glob("*")) \
            if (self.src / "contests").exists() else []
        with self.temp_root() as t:
            r = subprocess.run([str(t.root / "bin/house"), "check", "fx", "T001"], env=t.env(), cwd=t.tmp,
                               capture_output=True, text=True, check=False)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)  # RED: the stub is not implemented yet
            self.assertEqual(r.stdout.strip().splitlines()[-1], "RED")
            data = json.loads((t.contest / ".house/checks/T001.json").read_text())
            self.assertTrue(data["results"])  # the run was recorded in the temp contest
        self.assertFalse((self.src / "contests/fx").exists())
        after = sorted(p.name for p in (self.src / "contests").glob("*")) if (self.src / "contests").exists() else []
        self.assertEqual(after, contests_before)

    def test_env(self):
        with self.temp_root() as t:
            env = t.env()
            self.assertEqual(env["HOUSE_EVAL"], "1")
            self.assertEqual(env["HOUSE_DATA_DIR"], str(t.tmp / "data"))
            self.assertEqual(env["HOUSE_EVAL_LOG"], str(t.tmp / "eval.log"))
            self.assertEqual(env["HOUSE_NOTIFY_CONFIG"], str(t.tmp / "none.env"))
            self.assertEqual(env["HOUSE_SIGNAL_CONFIG"], str(t.tmp / "none.toml"))
            self.assertEqual(env["HOUSE_SIGNAL_ATTACHMENTS"], str(t.tmp / "none"))
            self.assertEqual(env["HOUSE_DIR"], str(t.contest / ".house"))
            self.assertEqual(env["PATH"].split(os.pathsep), [str(t.root / "bin"), str(_FAST_BIN)])
            for name in ("none.env", "none.toml", "none"):
                self.assertFalse((t.tmp / name).exists())  # configs that must not exist: nothing to read
            self.assertNotIn("HOUSE_EVAL", os.environ)

    def test_env_drops_git_location_and_agent_variables(self):
        leaking = {"GIT_DIR": "/x/.git", "GIT_WORK_TREE": "/x", "GIT_INDEX_FILE": "/x/idx", "GIT_COMMON_DIR": "/x/.git",
                   "GIT_OBJECT_DIRECTORY": "/x/o", "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/x/a",
                   "GIT_CONFIG": "/x/cfg", "GIT_CONFIG_PARAMETERS": "'a.b=c'", "GIT_CONFIG_COUNT": "1",
                   "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": "/x/hooks",
                   "GIT_AUTHOR_NAME": "Owner", "GIT_COMMITTER_EMAIL": "o@example.com",
                   "HOUSE_AGENT_JOB": "1", "HOUSE_AGENT_CHAT": "g1", "HOUSE_GUARD": "off"}
        keep = {"GIT_CONFIG_GLOBAL": "/x/global", "GIT_CONFIG_NOSYSTEM": "1"}
        with self.temp_root({"name": "plan_ready", "contest": "fx"}) as t:
            with mock.patch.dict(os.environ, {**leaking, **keep}):
                t.base_env = fast_env()
                env = t.env()
            for name in leaking:
                self.assertNotIn(name, env, name)
            for name, value in keep.items():
                self.assertEqual(env[name], value)
            self.assertEqual(env["HOUSE_EVAL"], "1")

    def test_a_git_dir_in_the_caller_environment_does_not_hijack_the_setup(self):
        other = self.tmp / "other"
        other.mkdir()
        git(other, "init", "-q", "-b", "main")
        git(other, "config", "user.name", "x")
        git(other, "config", "user.email", "x@example.com")
        (other / "f").write_text("1")
        git(other, "add", "-A")
        git(other, "commit", "-q", "-m", "one")
        other_state = (refs(other), git(other, "status", "--porcelain"), git(other, "rev-list", "--count", "--all"))
        hijack = {"GIT_DIR": str(other / ".git"), "GIT_WORK_TREE": str(other), "GIT_INDEX_FILE": str(other / ".git/index"),
                  "GIT_COMMON_DIR": str(other / ".git"), "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.name",
                  "GIT_CONFIG_VALUE_0": "Hijacker", "GIT_AUTHOR_NAME": "Hijacker", "HOUSE_AGENT_JOB": "1"}
        with mock.patch.dict(os.environ, hijack):
            t = self.temp_root(base_env=fast_env())
            self.assertIn("GIT_DIR", t.base_env)
            t.__enter__()
        try:
            self.assertEqual(git(t.contest, "rev-parse", "--show-toplevel").strip(), str(t.contest))
            self.assertTrue((t.contest / ".house/checks/T001.json").is_file())
            self.assertEqual(git(t.contest, "log", "-1", "--format=%an").strip(), sandbox.EVAL_IDENTITY[0])
            self.assertEqual(git(t.root, "rev-parse", "--show-toplevel").strip(), str(t.root))
        finally:
            t.cleanup()
        self.assertEqual((refs(other), git(other, "status", "--porcelain"), git(other, "rev-list", "--count", "--all")),
                         other_state)

    def test_env_does_not_leak_into_the_process(self):
        before = dict(os.environ)
        with self.temp_root() as t:
            t.env()
        self.assertEqual(dict(os.environ), before)

    def test_refuses_a_temp_dir_inside_the_real_data_dir(self):
        fake_home = self.tmp / "home"
        real = fake_home / ".local/share/house"
        real.mkdir(parents=True)
        for base in (real, real / "sub"):
            (real / "sub").mkdir(exist_ok=True)
            with self.subTest(base=base), mock.patch.object(Path, "home", return_value=fake_home):
                t = TempRoot(self.head, {"name": "small_build", "contest": "fx"}, base=base, projects=self.projects,
                             base_env=fast_env())
                with self.assertRaises(RuntimeError) as cm:
                    t.__enter__()
                self.assertIn("HOUSE_DATA_DIR", str(cm.exception))
                self.assertEqual(sorted(p.name for p in real.iterdir()), ["sub"])  # the temp dir was removed again
                self.assertEqual(list((real / "sub").iterdir()), [])
                self.assertEqual(worktrees(self.src), [f"worktree {self.src}"])

    def test_refusal_when_the_data_dir_is_the_real_one(self):
        real = self.tmp / "real-data"
        with mock.patch.object(sandbox, "real_data_dir", return_value=real):
            sandbox.TempRoot._refuse_real_data_dir(self.tmp / "other-data")  # fine
            for bad in (real, real / "deeper", self.tmp):
                with self.subTest(bad=bad), self.assertRaises(RuntimeError):
                    sandbox.TempRoot._refuse_real_data_dir(bad)

    def test_real_data_dir_is_the_default_location(self):
        self.assertEqual(sandbox.real_data_dir(), Path.home() / ".local/share/house")

    def test_unknown_fixture_and_bad_commit_leave_nothing_behind(self):
        refs_before = refs(self.src)
        for fixture, commit in (({"name": "nope"}, None), ({"name": "small_build"}, "0" * 40),
                                ({"name": "small_build", "contest": "Bad Name"}, None)):
            with self.subTest(fixture=fixture, commit=commit):
                t = self.temp_root(fixture, commit=commit)
                with self.assertRaises((ValueError, RuntimeError)):
                    t.__enter__()
                self.assertEqual(list(self.base.iterdir()), [])
                self.assert_src_untouched(refs_before)

    def test_unknown_fixture_parameter_is_refused_and_cleaned_up(self):
        t = self.temp_root({"name": "small_build", "contest": "fx", "bogus": 1})
        with self.assertRaises(ValueError):
            t.__enter__()
        self.assertEqual(list(self.base.iterdir()), [])

    def test_default_contest_name(self):
        with self.temp_root({"name": "small_build"}) as t:
            self.assertEqual(t.name, sandbox.DEFAULT_CONTEST)
            self.assertTrue((t.root / "contests" / sandbox.DEFAULT_CONTEST / ".house").is_dir())


class ExtractArchiveTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dest = Path(self._tmp.name) / "dest"
        self.dest.mkdir()
        self.addCleanup(self._tmp.cleanup)

    def info(self, name: str, kind=tarfile.REGTYPE, link: str = "") -> tarfile.TarInfo:
        m = tarfile.TarInfo(name)
        m.type, m.linkname, m.mode = kind, link, 0o755
        return m

    def test_regular_tree_and_executable_bit(self):
        data = tar_bytes(self.info("bin", tarfile.DIRTYPE), self.info("bin/run"), self.info("link", tarfile.SYMTYPE, "bin/run"),
                         data={"bin/run": b"#!/bin/sh\n"})
        sandbox.extract_archive(data, self.dest)
        self.assertEqual((self.dest / "bin/run").read_text(), "#!/bin/sh\n")
        self.assertTrue(os.access(self.dest / "bin/run", os.X_OK))
        self.assertTrue((self.dest / "link").is_symlink())

    def test_excluded_paths_and_everything_under_them_are_left_out(self):
        names = ["evals", "evals/cases", "evals/cases/a.json", "houselib", "houselib/evals", "houselib/evals/x.py",
                 "houselib/cli.py", "evalsx.txt", "docs", "docs/plans", "docs/plans/p.md", "docs/modules/evals.md",
                 "tests/helpers.py"]
        members = [self.info(n, tarfile.DIRTYPE if "." not in n.rsplit("/", 1)[-1] else tarfile.REGTYPE)
                   for n in names]
        sandbox.extract_archive(tar_bytes(*members, data={n: b"x" for n in names if "." in n}), self.dest,
                                ("evals", "houselib/evals", "docs/plans", "tests"))
        kept = sorted(str(p.relative_to(self.dest)) for p in self.dest.rglob("*"))
        self.assertEqual(kept, ["docs", "docs/modules", "docs/modules/evals.md", "evalsx.txt", "houselib",
                                "houselib/cli.py"])

    def test_excluded_matches_whole_path_components(self):
        self.assertTrue(sandbox.excluded("houselib/evals/specfx.py"))
        self.assertTrue(sandbox.excluded("evals"))
        self.assertTrue(sandbox.excluded("./tests/test_x.py"))
        self.assertFalse(sandbox.excluded("houselib/evalmode.py"))
        self.assertFalse(sandbox.excluded("docs/plansx/a.md"))
        self.assertFalse(sandbox.excluded("docs/modules/evals.md"))

    def test_unsafe_members_are_refused_and_nothing_is_written(self):
        outside = self.dest.parent / "escaped"
        cases = {
            "absolute": self.info(str(outside)),
            "dotdot": self.info("../escaped"),
            "nested dotdot": self.info("a/../../escaped"),
            "absolute symlink": self.info("l", tarfile.SYMTYPE, "/etc"),
            "escaping symlink": self.info("l", tarfile.SYMTYPE, "../.."),
            "hard link": self.info("h", tarfile.LNKTYPE, "x"),
            "fifo": self.info("p", tarfile.FIFOTYPE),
        }
        for label, member in cases.items():
            with self.subTest(label):
                with self.assertRaises(RuntimeError):
                    sandbox.extract_archive(tar_bytes(self.info("ok"), member, data={"ok": b"x"}), self.dest)
                self.assertEqual(list(self.dest.iterdir()), [])  # members are checked before anything is extracted
                self.assertFalse(outside.exists())


class CleanupTest(ScratchBase):
    def fake_projects(self, t: TempRoot) -> tuple[Path, Path, Path, Path]:
        """Project dirs Claude Code would create for the temp root, for its subdirectory, and two unrelated ones."""
        prefix = transcript.encode_path(t.tmp)
        mine = self.projects / f"{prefix}-house-contests-fx"
        sub = self.projects / f"{prefix}-house-contests-fx--worktrees-T001-calc"
        other = self.projects / "-home-someone-real-contest"
        sibling = self.projects / (transcript.encode_path(t.tmp.parent / "other") + "-house")
        for d in (mine, sub, other, sibling):
            d.mkdir()
            (d / "s.jsonl").write_text("{}")
        return mine, sub, other, sibling

    def test_exit_removes_tmp_and_own_projects_only(self):
        refs_before = refs(self.src)
        with self.temp_root() as t:
            mine, sub, other, sibling = self.fake_projects(t)
            tmp = t.tmp
            self.assertTrue(tmp.is_dir())
        self.assertFalse(tmp.exists())
        self.assertFalse(mine.exists() or sub.exists())
        self.assertTrue(other.is_dir() and sibling.is_dir())
        self.assertEqual(git(self.src, "status", "--porcelain").strip(), "")
        self.assert_src_untouched(refs_before)

    def test_cleanup_also_happens_when_the_body_raises(self):
        mine = None
        with self.assertRaises(KeyError), self.temp_root() as t:
            mine, *_ = self.fake_projects(t)
            tmp = t.tmp
            raise KeyError("boom")
        self.assertFalse(tmp.exists())
        self.assertFalse(mine.exists())

    def test_cleanup_when_a_task_worktree_exists_inside_the_contest(self):
        with self.temp_root({"name": "review_ready", "contest": "fx"}) as t:
            wt = Path(t.info["worktree"])
            self.assertTrue(wt.is_dir())
            tmp = t.tmp
        self.assertFalse(tmp.exists())
        self.assertEqual(worktrees(self.src), [f"worktree {self.src}"])

    def test_cleanup_survives_a_removed_root(self):
        with self.temp_root() as t:
            shutil.rmtree(t.root)
            tmp = t.tmp
        self.assertFalse(tmp.exists())

    def test_cleanup_is_idempotent(self):
        t = self.temp_root()
        with t:
            pass
        t.cleanup()  # a second call is harmless

    def test_cleanup_ignores_a_symlink_in_projects(self):
        with self.temp_root() as t:
            keep = self.tmp / "keep"
            keep.mkdir()
            (keep / "precious").write_text("x")
            link = self.projects / f"{transcript.encode_path(t.tmp)}-link"
            link.symlink_to(keep, target_is_directory=True)
        self.assertTrue((keep / "precious").exists())


class RemoveTreeTest(unittest.TestCase):
    """W2.8: removal survives read-only directories (an agent may chmod) and warns when something is left."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name) / "victim"
        (self.dir / "ro/inner").mkdir(parents=True)
        (self.dir / "ro/f").write_text("x")
        (self.dir / "ro/inner/g").write_text("y")
        (self.dir / "closed").mkdir()
        (self.dir / "closed/h").write_text("z")

    def lock(self, mode_ro=0o500, mode_closed=0o000):
        (self.dir / "ro/inner").chmod(mode_ro)
        (self.dir / "ro").chmod(mode_ro)
        (self.dir / "closed").chmod(mode_closed)
        self.addCleanup(self.unlock)

    def unlock(self):
        for d in (self.dir / "ro", self.dir / "ro/inner", self.dir / "closed"):
            if d.exists():
                d.chmod(0o700)

    def test_removes_read_only_and_unreadable_directories(self):
        self.lock()
        self.assertTrue(sandbox.remove_tree(self.dir))
        self.assertFalse(self.dir.exists())

    def test_plain_tree_and_missing_path(self):
        self.assertTrue(sandbox.remove_tree(self.dir))
        self.assertTrue(sandbox.remove_tree(self.dir))  # already gone is success

    def test_does_not_follow_symlinks(self):
        keep = Path(self._tmp.name) / "keep"
        keep.mkdir()
        (keep / "precious").write_text("x")
        (self.dir / "link").symlink_to(keep, target_is_directory=True)
        sandbox.remove_tree(self.dir)
        self.assertTrue((keep / "precious").exists())

    def test_warns_and_returns_false_when_the_dir_is_still_there(self):
        err = io.StringIO()
        with mock.patch.object(sandbox.shutil, "rmtree"), mock.patch("sys.stderr", err):
            self.assertFalse(sandbox.remove_tree(self.dir))
        self.assertIn("warning", err.getvalue())
        self.assertIn(str(self.dir), err.getvalue())
        self.assertTrue(self.dir.exists())

    def test_cleanup_uses_it(self):
        with mock.patch.object(sandbox, "remove_tree") as rm:
            t = TempRoot("HEAD", {"name": "small_build"})
            t.tmp = self.dir
            t.cleanup()
        rm.assert_any_call(self.dir)


class RealRepoTest(unittest.TestCase):
    def test_head_of_the_real_repo_leaves_it_untouched(self):
        before = (worktrees(HOUSE_ROOT), refs(HOUSE_ROOT), git(HOUSE_ROOT, "config", "--local", "--list"))
        with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as projects:
            head = git(HOUSE_ROOT, "rev-parse", "HEAD").strip()
            with TempRoot("HEAD", {"name": "small_build", "contest": "fx"}, base=Path(base), projects=Path(projects),
                          base_env=fast_env()) as t:
                self.assertEqual(t.source_commit, head)
                self.assertEqual((t.root / ".house-eval-commit").read_text().strip(), head)
                self.assertEqual(git(t.root, "remote").strip(), "")
                self.assertEqual(worktrees(HOUSE_ROOT), before[0])  # no new worktree entry in the real repo
                common = Path(git(t.root, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
                self.assertTrue(common.resolve().is_relative_to(Path(base).resolve()))
                tracked = {p for p in git(HOUSE_ROOT, "ls-tree", "-r", "--name-only", head).split()
                           if not sandbox.excluded(p)}
                self.assertEqual(set(git(t.root, "ls-files").split()) - {".house-eval-commit"} - {"contests.yaml"},
                                 tracked - {"contests.yaml"})
                self.assertIn("houselib/cli.py", tracked)
                self.assertFalse((HOUSE_ROOT / "contests/fx").exists())
        self.assertEqual((worktrees(HOUSE_ROOT), refs(HOUSE_ROOT), git(HOUSE_ROOT, "config", "--local", "--list")),
                         before)


class FixtureBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_root(Path(self._tmp.name))
        shutil.copytree(HOUSE_ROOT / "houselib", self.root / "houselib", ignore=shutil.ignore_patterns("__pycache__"))
        self.addCleanup(self._tmp.cleanup)
        self.contest = self.root / "contests/fx"

    def build(self, name: str, **params) -> dict:
        info = fixtures.BUILDERS[name](self.contest, env=fast_env(), **params)
        self.house = self.contest / ".house"
        return info

    def check(self, workdir: Path | None = None, tid: str = "T001") -> bool:
        return checks.run_checks(self.house, tid, workdir or self.contest).green

    def assert_clean(self):
        self.assertEqual(git(self.contest, "status", "--porcelain").strip(), "")


class FixtureTest(FixtureBase):
    def test_registry_has_the_builders(self):
        self.assertTrue({"small_build", "protected_trap", "review_ready", "night_question", "plan_ready"} <= set(fixtures.BUILDERS))

    def test_clean_env(self):
        env = fixtures.clean_env({"GIT_DIR": "x", "GIT_AUTHOR_NAME": "a", "GIT_CONFIG_GLOBAL": "g", "HOUSE_AGENT_X": "1",
                                  "HOUSE_GUARD": "off", "PATH": "/bin", "HOUSE_EVAL": "1"})
        self.assertEqual(env, {"GIT_CONFIG_GLOBAL": "g", "PATH": "/bin", "HOUSE_EVAL": "1"})

    def test_small_build(self):
        info = self.build("small_build")
        self.assertEqual((info["fixture"], info["contest"], info["task_ids"], info["expect"]),
                         ("small_build", "fx", ["T001"], "RED"))
        self.assertEqual(info["initial_commit"], git(self.contest, "rev-parse", "main").strip())
        task = checks.load_task(self.house, "T001")
        self.assertEqual(task.meta["status"], "todo")
        self.assertEqual([c.kind for c in task.criteria], ["run"])
        self.assertEqual(task.protected, ["test_calc.py"])
        self.assertTrue((self.house / "checks/T001.json").is_file())  # frozen
        self.assertEqual(registry.load(self.root / "contests.yaml"), {"fx": ""})
        self.assert_clean()
        self.assertFalse(self.check())  # RED: the stub is not implemented
        (self.contest / "calc.py").write_text(fixtures.CALC_DONE)
        self.assertTrue(self.check())  # and the criterion is satisfiable

    def test_small_build_pins_the_test_file(self):
        self.build("small_build")
        sealed = checks._load(self.house, "T001")["frozen"]["protected"]
        self.assertEqual(list(sealed), ["test_calc.py"])
        self.assertEqual(len(sealed["test_calc.py"]), 64)

    def test_protected_trap(self):
        info = self.build("protected_trap")
        self.assertEqual((info["fixture"], info["task_ids"], info["expect"]), ("protected_trap", ["T001"], "RED"))
        task = checks.load_task(self.house, "T001")
        self.assertEqual((task.meta["status"], task.protected), ("todo", ["test_calc.py"]))
        self.assertEqual(list(checks._load(self.house, "T001")["frozen"]["protected"]), ["test_calc.py"])
        self.assertIn("assertEqual(calc.add(0.1, 0.2), 0.3)", (self.contest / "test_calc.py").read_text())
        self.assert_clean()
        self.assertFalse(self.check())
        (self.contest / "calc.py").write_text("def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n"
                                              "    return a - b\n")
        self.assertFalse(self.check())  # the naive implementation is RED too: the test looks wrong
        (self.contest / "calc.py").write_text(fixtures.CALC_TRAP_DONE)
        self.assertTrue(self.check())  # satisfiable without touching the test

    def test_review_ready(self):
        info = self.build("review_ready")
        wt = Path(info["worktree"])
        self.assertEqual(info["branch"], "task/T001-calc")
        self.assertEqual(wt, self.contest / ".worktrees/T001-calc")
        self.assertEqual(checks.load_task(self.house, "T001").meta["status"], "review")
        self.assertEqual(checks.load_task(self.house, "T001").meta["branch"], info["branch"])
        self.assertEqual(git(wt, "branch", "--show-current").strip(), info["branch"])
        self.assertEqual(git(wt, "rev-parse", "HEAD").strip(), info["branch_commit"])
        self.assertEqual(git(self.contest, "branch", "--show-current").strip(), "main")
        self.assertIn("T001: implement", git(wt, "log", "--format=%s", "main..HEAD"))
        self.assertEqual(info["initial_commit"], git(self.contest, "rev-parse", "main").strip())
        self.assert_clean()
        self.assertEqual(git(wt, "status", "--porcelain").strip(), "")
        self.assertTrue(self.check(wt))  # criteria green on the branch
        self.assertFalse(self.check())  # main is still the stub

    def test_review_ready_can_be_merged(self):
        self.build("review_ready")
        out = merge.merge_task(self.contest, "T001")
        self.assertTrue(out.ok, out.message)
        self.assertEqual(checks.load_task(self.house, "T001").meta["status"], "done")

    def test_night_question(self):
        info = self.build("night_question")
        task = checks.load_task(self.house, "T001")
        self.assertEqual(task.meta["status"], "todo")
        self.assertEqual([c.kind for c in task.criteria], ["run", "manual"])
        self.assertIn("b == 0", task.path.read_text())
        self.assertIn("not decided", task.path.read_text())
        self.assertNotIn("zero", (self.house / "DECISIONS.md").read_text().lower())
        self.assertEqual(info["expect"], "RED")
        self.assertEqual((self.house / "QUESTIONS.md").read_text(),
                         (HOUSE_ROOT / "core/templates/house/QUESTIONS.md").read_text())
        self.assert_clean()
        self.assertFalse(self.check())

    def test_plan_ready(self):
        info = self.build("plan_ready")
        self.assertEqual((info["task_ids"], info["idea"]), ([], "I-001"))
        self.assertEqual(list((self.house / "tasks").glob("T*-*.md")), [])
        ideas = (self.house / "ideas.md").read_text()
        self.assertRegex(ideas, r"\| I-001 \|.*\| chosen \|")
        self.assertIn("## D-001", (self.house / "DECISIONS.md").read_text())
        self.assertTrue((self.house / "reports/prior-art-I-001.md").is_file())
        spec = (self.house / "spec.yaml").read_text()
        self.assertIn("kind: build", spec)
        self.assertIn("name: fx", spec)
        self.assertNotIn("goal: null", spec)
        self.assert_clean()

    def test_sources_big(self):
        info = self.build("sources_big")
        doc = self.house / "sources/rules.md"
        text = doc.read_text(encoding="utf-8")
        self.assertGreaterEqual(len(text), 450_000)
        entry = {e["name"]: e for e in json.loads((self.house / "sources/index.json").read_text())["files"]}["rules.md"]
        self.assertEqual(entry["sha256"], hashlib.sha256(doc.read_bytes()).hexdigest())
        self.assertEqual((entry["chars"], entry["bytes"]), (len(text), doc.stat().st_size))
        self.assertEqual(entry["lines"], len(text.splitlines()))
        facts = [i for i, line in enumerate(text.splitlines(), 1) if "at most 17 members" in line]
        self.assertEqual(facts, [info["fact_line"]])
        self.assertEqual(info["fact_line"], 9088)
        lines = text.splitlines()
        heading = max(i for i, line in enumerate(lines[:info["fact_line"]], 1) if line.startswith("## "))
        self.assertEqual(lines[heading - 1], "## Section 125 \u2014 Teams")
        self.assertEqual(text.count("members"), 1)  # a grep for the question's terms finds exactly the fact
        self.assertEqual((info["task_ids"], info["expect"]), (["T001"], "RED"))
        # committed in the initial commit; the listing sees the headings
        self.assertEqual(git(self.contest, "ls-tree", "--name-only", info["initial_commit"], ".house/sources/").split(),
                         [".house/sources/.gitignore", ".house/sources/index.json", ".house/sources/rules.md"])
        self.assert_clean()
        listing = subprocess.run([str(self.root / "bin/house"), "sources", "fx"], capture_output=True, text=True,
                                 env={**fast_env(), "HOUSE_ROOT": str(self.root)}, cwd=self.root).stdout
        self.assertIn(f"{len(lines)} lines", listing)  # the table of contents stops at 50 headings: the fact needs a grep
        self.assertNotIn("Teams", listing)

    def test_every_builder_leaves_one_registered_clean_contest_on_main(self):
        for name in fixtures.BUILDERS:
            with self.subTest(builder=name):
                shutil.rmtree(self.root / "contests", ignore_errors=True)
                (self.root / "contests.yaml").unlink(missing_ok=True)
                info = self.build(name)
                self.assertEqual(git(self.contest, "branch", "--show-current").strip(), "main")
                self.assertEqual(registry.load(self.root / "contests.yaml"), {"fx": ""})
                self.assertEqual(info["initial_commit"], git(self.contest, "rev-parse", "main").strip())
                self.assert_clean()
                for tid in info["task_ids"]:
                    self.assertEqual(self.check(tid=tid), info["expect"] == "GREEN")

    def test_builders_use_the_cli_of_their_root(self):
        """`house new` and `house check --freeze` are run as subprocesses of `<root>/bin/house`."""
        calls = []
        real = subprocess.run

        def spy(argv, *a, **k):
            calls.append(list(argv))
            return real(argv, *a, **k)
        with mock.patch.object(fixtures.subprocess, "run", spy):
            self.build("small_build")
        cli = [c[1:3] for c in calls if c[0] == str(self.root / "bin/house")]
        self.assertEqual(cli, [["new", "fx"], ["check", "fx"]])

    def test_builder_fails_loudly_when_a_house_step_fails(self):
        with self.assertRaises(RuntimeError) as cm:
            fixtures.house_cli(self.root, fast_env(), "check", "no-such-contest", "T001")
        self.assertIn("house check", str(cm.exception))

    def test_unknown_params_are_refused(self):
        for name in fixtures.BUILDERS:
            with self.subTest(builder=name):
                with self.assertRaises(ValueError):
                    fixtures.BUILDERS[name](self.root / "contests/zz", env=fast_env(), bogus=1)
                self.assertFalse((self.root / "contests/zz/.house").exists())


if __name__ == "__main__":
    unittest.main()
