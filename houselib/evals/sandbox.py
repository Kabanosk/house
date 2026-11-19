"""An isolated, temporary `house` root with one fixture contest, for a single eval run.

An eval starts a real agent session. That session must reach neither the owner's real contests nor the owner's real
data nor the owner's real git repository, so it gets its own house: an export of the commit under test that is
turned into a brand-new git repository, a fixture contest inside it, a bare `origin` next to it and an environment
that points every house path at the temp dir.

    with TempRoot("HEAD", {"name": "small_build", "contest": "calc"}) as t:
        subprocess.run([...agent command...], cwd=t.contest, env=t.env())   # t.root, t.contest, t.name, t.info

Layout of `<tmp>` (a fresh `mkdtemp` directory): `house/` (its own repo, one commit, no remote, without the paths of
`EXPORT_EXCLUDE`; `contests.yaml` lists only the fixture contest; `.house-eval-commit` holds the sha it was exported
from, also `t.source_commit`),
`origin.git/` (bare, the contest's `origin`), `data/` (`HOUSE_DATA_DIR`), `eval.log`, `hidden/` (hidden tests of the
fixture, 0700; the agent container masks it, see `runner`) and `material/` (prompt material of the fixture, 0700,
masked the same way; only a variant's `copy` or `{material:}` shows it to the agent).

Safety rules (this module is the isolation boundary of `house eval`):
- `<tmp>/house` is **not** a git worktree of the owner's repo (a worktree would share its refs, config, hooks and
  `origin`): the commit is exported with `git archive`, extracted with `tarfile` (members with absolute paths or `..`,
  and links leaving the tree, are refused) and committed into a fresh `git init` with a local eval identity. Its
  git common dir is inside `<tmp>`. Nothing is ever run against the real repo except the read-only `rev-parse` and
  `archive`; no `git worktree add/remove/prune`.
- The export holds no answer key: the paths of `EXPORT_EXCLUDE` (`evals/`, `houselib/evals/`, `tests/`,
  `docs/plans/`, `docs/specs/`) are skipped when the archive is extracted, before the fresh repo's first commit, so
  they are neither in its tree nor in any of its git objects (the agent container mounts `<tmp>/house`). The fixture
  builders, the generators and the grader run in this process, from its own `houselib.evals`; the export's code only
  runs as `<tmp>/house/bin/house` (whose `cli` imports the eval modules only when they exist). A commit whose `cli`
  still imports `houselib.evals` unconditionally cannot be exported any more (setup fails at `house new`).
- The root is the **commit**, never the working tree: uncommitted edits do not leak in. `contest_dir(name)` resolves
  under the `HOUSE_ROOT` of the code that runs, so the agent must call `<tmp>/house/bin/house` (first on `PATH` in
  `env()`); that copy's `HOUSE_ROOT` is the temp root.
- Code versions: the house-generated steps of setup (`house new`, `house check --freeze`, `house adapt`) run through
  `<tmp>/house/bin/house` as subprocesses, i.e. with the code and templates of the commit under test, like the agent
  will; only ordinary files (fixture code, task text, spec values) and the git plumbing are written by this process.
- `env()` and every setup subprocess use a cleaned environment (`fixtures.clean_env`): no `GIT_DIR`, `GIT_WORK_TREE`,
  `GIT_INDEX_FILE`, `GIT_COMMON_DIR`, `GIT_CONFIG_PARAMETERS`, ... and no `HOUSE_AGENT_*` or `HOUSE_GUARD`. It sets
  `HOUSE_EVAL=1` (Signal, notifications and inbox become logged no-ops) and points `HOUSE_DATA_DIR`, `HOUSE_EVAL_LOG`,
  `HOUSE_NOTIFY_CONFIG`, `HOUSE_SIGNAL_CONFIG` and `HOUSE_SIGNAL_ATTACHMENTS` into `<tmp>`; the config files it names
  do not exist, so there is no account to read. `HOME` is left alone (the agent CLI needs its login), so the
  global git config still applies.
- It refuses to start (`RuntimeError`, before the repo is read) when the data dir it would use is, or overlaps, the
  real `~/.local/share/house`.
- Cleanup runs on exit, also when the body raised or setup failed halfway: deletion of `<tmp>` and of the Claude
  Code project directories created for paths under `<tmp>` (`<projects>/<encode_path(tmp)>` and
  `<projects>/<encode_path(tmp)>-*`, real directories only, never a symlink or any other entry). Deletion goes
  through `remove_tree`, which chmods and retries (the agent may have made directories read-only) and prints a
  warning line when something is still left. Keep `base` short:
  `encode_path` does not mirror Claude Code's truncation of project names over 200 characters (the `mkdtemp`
  default location is fine).
"""
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Self

from .. import HOUSE_ROOT, scaffold, transcript
from .fixtures import BUILDERS, HIDDEN_DIR, MATERIAL_DIR, clean_env, git, house_cli

DEFAULT_CONTEST = "eval-fixture"
DEFAULT_PROJECTS = Path("~/.claude/projects").expanduser()
COMMIT_FILE = ".house-eval-commit"
EVAL_IDENTITY = ("House Eval", "eval@house.invalid")
QUESTIONS_FILE = ".house/QUESTIONS.md"
# Left out of the export (the agent's read-only `<tmp>/house`): the eval answer key. `evals/` (cases with their
# expected answers, results, README), `houselib/evals/` (the harness: fixture builders, the generators `chainfx` and
# `specfx` with planted facts, reference solutions and hidden-test sources, the grader), `tests/` (unit tests that
# name planted values and reference code) and the design history `docs/plans/`, `docs/specs/` (case designs and
# their traps). The fixtures are built by this process's own `houselib.evals`, never by the export's code.
EXPORT_EXCLUDE = ("evals", "houselib/evals", "tests", "docs/plans", "docs/specs")


def real_data_dir() -> Path:
    """The owner's real house data dir (`~/.local/share/house`), which an eval must never use."""
    return Path.home() / ".local/share/house"


def _chmod_tree(path: Path) -> None:
    """Make every real directory under `path` (itself included) owner-accessible; symlinks are left alone."""
    try:
        if path.is_symlink() or not path.is_dir():
            return
        path.chmod(0o700)
        for child in list(path.iterdir()):
            _chmod_tree(child)
    except OSError:
        pass


def _retry_removal(func, path, *_exc) -> None:
    """`shutil.rmtree` error handler: make the entry and its parent writable and retry the failed removal."""
    for target in (os.path.dirname(path), path):
        try:
            if not os.path.islink(target):
                os.chmod(target, 0o700)
        except OSError:
            pass
    if func in (os.unlink, os.remove, os.rmdir):  # retrying a failed open or scandir would leak a descriptor
        try:
            func(path)
        except OSError:
            pass


def remove_tree(path: Path) -> bool:
    """Delete a directory tree even when the agent made parts of it read-only or unreadable.

    The agent runs as the owner and may `chmod` inside its workspace, which makes a plain `shutil.rmtree` leave the
    tree behind. Errors are handled by chmodding and retrying; if something is still there a second pass opens up
    every directory first.

    Args:
        path: Directory to remove (a symlink is removed, never followed). A missing path counts as removed.

    Returns:
        True when `path` is gone; False after printing a warning line to stderr naming it.
    """
    path = Path(path)
    kw = {"onexc": _retry_removal} if sys.version_info >= (3, 12) else {"onerror": _retry_removal}
    for attempt in (0, 1):
        if not (path.exists() or path.is_symlink()):
            return True
        if attempt:
            _chmod_tree(path)
        if path.is_symlink():
            path.unlink(missing_ok=True)
            continue
        shutil.rmtree(path, **kw)
    if path.exists() or path.is_symlink():
        print(f"house eval: warning: could not remove {path}; delete it by hand", file=sys.stderr)
        return False
    return True


def excluded(name: str, exclude: tuple[str, ...] = EXPORT_EXCLUDE) -> bool:
    """Whether the repository-relative path `name` is, or lies under, one of the `exclude` paths."""
    parts = PurePosixPath(name).parts
    return any(parts[:len(PurePosixPath(e).parts)] == PurePosixPath(e).parts for e in exclude)


def extract_archive(data: bytes, dest: Path, exclude: tuple[str, ...] = ()) -> None:
    """Extract a tar archive (from `git archive`) into `dest`, refusing anything that could escape it.

    Args:
        data: The tar bytes.
        dest: Existing directory to extract into.
        exclude: Repository-relative paths left out: a member that is one of them or lies under one is skipped
            (after the safety checks, which every member passes).

    Raises:
        RuntimeError: A member has an absolute path or `..` component, is a device, fifo or hard link, or is a
            symlink with an absolute target or a target that leaves `dest`.
    """
    dest = dest.resolve()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
        members = tar.getmembers()
        for m in members:
            if Path(m.name).is_absolute() or ".." in Path(m.name).parts:
                raise RuntimeError(f"refusing archive member with unsafe path: {m.name!r}")
            if m.isdev() or m.islnk():
                raise RuntimeError(f"refusing archive member of unsupported type: {m.name!r}")
            if m.issym():
                target = Path(m.linkname)
                resolved = (dest / Path(m.name).parent / target).resolve()
                if target.is_absolute() or not resolved.is_relative_to(dest):
                    raise RuntimeError(f"refusing symlink leaving the tree: {m.name!r} -> {m.linkname!r}")
        members = [m for m in members if not excluded(m.name, exclude)]
        if hasattr(tarfile, "data_filter"):
            tar.extractall(dest, members=members, filter="data")
        else:  # pragma: no cover - Python without the extraction filters; members were checked above
            tar.extractall(dest, members=members)


def tracked_hashes(repo: Path, commit: str, env: Mapping[str, str] | None = None) -> dict[str, tuple[str, str]]:
    """Every file tracked at `commit`, as `{path: (blob id, sha256 of its bytes)}`.

    Taken by the host right after setup, before the agent can write `.git`: the grader compares protected files with
    these hashes, not with objects in the agent-writable object store.

    Args:
        repo: The repository (the fixture contest).
        commit: The commit to list (the fixture's initial commit).
        env: Environment for git (passed through `clean_env`).

    Raises:
        RuntimeError: git failed.
    """
    listing = git(repo, env, "ls-tree", "-r", "-z", "--full-tree", commit)
    blobs = []
    for entry in listing.split("\0"):
        meta, _, path = entry.partition("\t")
        fields = meta.split()
        if len(fields) == 3 and fields[1] == "blob":
            blobs.append((path, fields[2]))
    if not blobs:
        return {}
    batch = subprocess.run(["git", "cat-file", "--batch"], cwd=repo, env=clean_env(env), check=False,
                           input="".join(f"{sha}\n" for _, sha in blobs).encode(), capture_output=True)
    if batch.returncode != 0:
        raise RuntimeError(f"git cat-file --batch failed in {repo}: {batch.stderr.decode(errors='replace')}")
    out, pos, hashes = batch.stdout, 0, {}
    for path, sha in blobs:
        header_end = out.index(b"\n", pos)
        size = int(out[pos:header_end].split()[2])
        data = out[header_end + 1:header_end + 1 + size]
        hashes[path] = (sha, hashlib.sha256(data).hexdigest())
        pos = header_end + 1 + size + 1
    return hashes


def tracked_file(repo: Path, commit: str, path: str, env: Mapping[str, str] | None = None) -> bytes | None:
    """The bytes of `path` at `commit`, read by the host during setup; None when it is not tracked there.

    Args:
        repo: The repository (the fixture contest).
        commit: The commit (the fixture's initial commit).
        path: Repository-relative path, e.g. `QUESTIONS_FILE`.
        env: Environment for git (passed through `clean_env`).
    """
    r = subprocess.run(["git", "cat-file", "blob", f"{commit}:{path}"], cwd=repo, env=clean_env(env),
                       capture_output=True, check=False)
    return r.stdout if r.returncode == 0 else None


class TempRoot:
    """Context manager: a temporary house root on a given commit with a fixture contest (see the module docstring).

    Attributes:
        commit: The commit-ish given.
        source_commit: The sha the root was exported from (also in `<root>/.house-eval-commit`); set on entry.
        tmp: The temp directory (set on entry; stays set after exit, the directory is gone).
        root: `<tmp>/house`, the exported root (its own git repo).
        contest: `<tmp>/house/contests/<name>`, the fixture contest checkout.
        name: Contest name.
        info: What the fixture builder returned (task ids, branch, ...); `initial_commit` is the contest's `main`
            after `adapt` and the push, the state the agent starts from.
        baseline: `tracked_hashes` of the contest at `initial_commit`, taken by the host at the end of setup and
            kept in memory only (the grader's reference for `file_unchanged`).
        baseline_questions: The bytes of `QUESTIONS_FILE` at `initial_commit` (None when it is not tracked), taken
            with `baseline`: the grader's reference for which questions were open at the start.
    """

    def __init__(self, commit: str, fixture: dict, base: Path | None = None, projects: Path = DEFAULT_PROJECTS,
                 base_env: Mapping[str, str] | None = None):
        """Prepare a run; nothing is created until `__enter__`.

        Args:
            commit: Commit-ish of the house repo to test (`HEAD`, a sha, a branch); resolved in `HOUSE_ROOT`.
            fixture: `{"name": <builder in fixtures.BUILDERS>, "contest": <contest name, default
                "eval-fixture">, **builder params}`.
            base: Directory to create the temp dir in (default: the system temp dir; keep the path short).
            projects: Claude Code's projects directory, whose entries for paths under the temp dir are deleted on
                exit (injectable for tests).
            base_env: Environment `env()` and the setup subprocesses start from (default: `os.environ`, read
                at the time of use); injectable for tests.
        """
        self.commit = commit
        self.fixture = dict(fixture)
        self.base = base
        self.projects = projects
        self.base_env = base_env
        self.source_commit = ""
        self.tmp: Path | None = None
        self.root: Path | None = None
        self.contest: Path | None = None
        self.name = str(self.fixture.get("contest") or DEFAULT_CONTEST)
        self.info: dict = {}
        self.baseline: dict[str, tuple[str, str]] = {}
        self.baseline_questions: bytes | None = None
        self._done = False

    def __enter__(self) -> Self:
        """Create the root; on any failure clean up what exists and re-raise.

        Raises:
            RuntimeError: The data dir would be the real one, the commit is unknown, or a git or house step failed.
            ValueError: Unknown fixture builder, invalid contest name or unknown builder parameters.
        """
        try:
            self._setup()
        except BaseException:
            self.cleanup()
            raise
        return self

    def __exit__(self, *exc) -> bool:
        self.cleanup()
        return False

    def _setup(self) -> None:
        params = {k: v for k, v in self.fixture.items() if k not in ("name", "contest")}
        if "reference" in params:  # the reference solution is built by unit tests only, never for an agent run
            raise ValueError("fixture parameter 'reference' is test-only and never used for a run")
        builder_name = self.fixture.get("name")
        if builder_name not in BUILDERS:
            raise ValueError(f"unknown fixture {builder_name!r}; known: {', '.join(sorted(BUILDERS))}")
        scaffold.validate_name(self.name)
        self.tmp = Path(tempfile.mkdtemp(prefix="house-eval-", dir=self.base)).resolve()
        self._refuse_real_data_dir(self.tmp / "data")
        (self.tmp / HIDDEN_DIR).mkdir(mode=0o700)  # hidden tests of the fixture (`fixtures.write_hidden`)
        (self.tmp / MATERIAL_DIR).mkdir(mode=0o700)  # prompt material of the fixture (`fixtures.write_material`)
        outer = clean_env(self.base_env)
        self.source_commit = git(HOUSE_ROOT, outer, "rev-parse", "--verify", f"{self.commit}^{{commit}}").strip()
        self.root = self.tmp / "house"
        self.contest = self.root / "contests" / self.name
        self._export_root(outer)
        env = self.env()
        self.info = BUILDERS[builder_name](self.contest, env=env, **params)
        house_cli(self.root, env, "adapt", self.name)
        origin = self.tmp / "origin.git"
        git(self.tmp, env, "init", "-q", "--bare", "-b", "main", str(origin))
        git(self.contest, env, "remote", "add", "origin", str(origin))
        git(self.contest, env, "push", "-q", "-u", "origin", "main")
        if self.info.get("branch"):
            git(self.contest, env, "push", "-q", "origin", self.info["branch"])
        git(self.root, env, "add", "-A")  # contests.yaml now names the fixture contest
        git(self.root, env, "commit", "-q", "--no-verify", "-m", f"register contest {self.name}")
        self.info["initial_commit"] = git(self.contest, env, "rev-parse", "main").strip()  # after the adapt commit
        self.baseline = tracked_hashes(self.contest, self.info["initial_commit"], env)
        self.baseline_questions = tracked_file(self.contest, self.info["initial_commit"], QUESTIONS_FILE, env)

    def commit_patch(self) -> None:
        """Commit a variant's `remove`/`edit` changes in the contest and take the host's references again.

        One commit `chore: setup` (a neutral message: the agent can read the log) by the eval identity with
        hooks off, pushed to `origin` like the fixture's `main`; then `info["initial_commit"]`, `baseline` and
        `baseline_questions` are read anew, so the contest starts clean and the graders compare with the patched
        state. Call it before the agent starts (`runner.apply_variant` does). Ignored files (the Claude settings)
        are not part of it. Does nothing when the working tree is clean.

        Raises:
            RuntimeError: A git step failed.
        """
        env = self.env()
        if not git(self.contest, env, "status", "--porcelain").strip():
            return
        git(self.contest, env, "add", "-A")
        git(self.contest, env, "-c", f"user.name={EVAL_IDENTITY[0]}", "-c", f"user.email={EVAL_IDENTITY[1]}",
            "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "chore: setup")
        git(self.contest, env, "push", "-q", "origin", "main")
        self.info["initial_commit"] = git(self.contest, env, "rev-parse", "main").strip()
        self.baseline = tracked_hashes(self.contest, self.info["initial_commit"], env)
        self.baseline_questions = tracked_file(self.contest, self.info["initial_commit"], QUESTIONS_FILE, env)

    def _export_root(self, env: dict[str, str]) -> None:
        """Make `<tmp>/house` a fresh one-commit repo holding the exported tree of `source_commit`."""
        archive = subprocess.run(["git", "archive", "--format=tar", self.source_commit], cwd=HOUSE_ROOT, env=env,
                                 capture_output=True, check=False)
        if archive.returncode != 0:
            raise RuntimeError(f"git archive {self.source_commit} failed: {archive.stderr.decode(errors='replace')}")
        self.root.mkdir()
        extract_archive(archive.stdout, self.root, EXPORT_EXCLUDE)
        left = [e for e in EXPORT_EXCLUDE if os.path.lexists(self.root / e)]
        if left:  # the commit below must never hold them: not in the tree, not in any object
            raise RuntimeError(f"eval export still holds {', '.join(left)}")
        (self.root / "contests.yaml").write_text("")  # the commit's list names real contests: start empty
        (self.root / COMMIT_FILE).write_text(self.source_commit + "\n")
        git(self.root, env, "init", "-q", "-b", "main")
        git(self.root, env, "config", "user.name", EVAL_IDENTITY[0])
        git(self.root, env, "config", "user.email", EVAL_IDENTITY[1])
        git(self.root, env, "add", "-A")
        git(self.root, env, "commit", "-q", "--no-verify", "-m", f"house {self.source_commit} (eval export)")

    @staticmethod
    def _refuse_real_data_dir(data: Path) -> None:
        """Raise RuntimeError when `data` is, contains or lies inside the real house data dir."""
        real, mine = real_data_dir().resolve(), data.resolve()
        if mine == real or real in mine.parents or mine in real.parents:
            raise RuntimeError(f"refusing to run an eval with HOUSE_DATA_DIR={mine}: it is the real house data dir "
                               f"({real}) or overlaps it")

    def env(self) -> dict[str, str]:
        """The environment for processes of this run: the cleaned current one with the isolation variables on top.

        Returns:
            A new dict (the process environment is not changed), without repo-location `GIT_*` variables,
            `HOUSE_AGENT_*` and `HOUSE_GUARD`, with `HOUSE_EVAL=1`, `HOUSE_DATA_DIR`, `HOUSE_EVAL_LOG`,
            `HOUSE_NOTIFY_CONFIG`, `HOUSE_SIGNAL_CONFIG` and `HOUSE_SIGNAL_ATTACHMENTS` under the temp dir (the
            three config paths do not exist), `HOUSE_DIR` = `<contest>/.house` and `PATH` with `<tmp>/house/bin`
            first.
        """
        env = clean_env(self.base_env)
        return {**env,
                "HOUSE_EVAL": "1",
                "HOUSE_DATA_DIR": str(self.tmp / "data"),
                "HOUSE_EVAL_LOG": str(self.tmp / "eval.log"),
                "HOUSE_NOTIFY_CONFIG": str(self.tmp / "none.env"),
                "HOUSE_SIGNAL_CONFIG": str(self.tmp / "none.toml"),
                "HOUSE_SIGNAL_ATTACHMENTS": str(self.tmp / "none"),
                "HOUSE_DIR": str(self.contest / ".house"),
                "PATH": f"{self.root / 'bin'}{os.pathsep}{env.get('PATH', '')}"}

    def cleanup(self) -> None:
        """Remove the temp dir and this run's Claude Code project directories; safe to repeat."""
        if self._done or self.tmp is None:
            return
        self._done = True
        remove_tree(self.tmp)
        self._remove_projects()

    def _remove_projects(self) -> None:
        """Delete `<projects>/<encode_path(tmp)>` and `<projects>/<encode_path(tmp)>-*` (real directories only)."""
        prefix = transcript.encode_path(self.tmp)
        try:
            entries = list(self.projects.iterdir())
        except OSError:
            return
        for d in entries:
            if (d.name == prefix or d.name.startswith(prefix + "-")) and d.is_dir() and not d.is_symlink():
                remove_tree(d)
