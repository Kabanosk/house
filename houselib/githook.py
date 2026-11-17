"""Git hooks (`pre-commit`, `pre-push`) that enforce the guard rules inside task worktrees.

Why: the Claude Code hook (`hook.py`) reads shell commands and guesses what they do; git runs these hooks on every
commit and push whatever the command looked like, for Claude Code, Codex and a plain shell alike.

What they enforce, only in a **linked worktree of a house contest** (task and experiment worktrees; the main
checkout, where the human commits, and repos that are not house contests are left alone):
  * `pre-commit`: staged files that look like secrets (`.env*` except `.env.example`, key/token patterns, judged on
    the staged content); staged `.house/` files (workers write through `$HOUSE_DIR`); staged changes to protected
    files (frozen acceptance tests, the local scorer). The last two are skipped while a merge, rebase or cherry-pick
    is in progress (the staged diff then holds the other side's changes); secrets are checked always.
  * `pre-push`: a remote not listed in spec `guard.push_allow` (default `origin`; a URL given instead of a remote
    name counts as a different remote); deleting or force-pushing the contest's main branch.

Install: `install(contest)` (called by `house adapt`) writes two small wrapper scripts into the repo's hooks
directory. They call `bin/house-githook`, which calls `main` here. It leaves a hooks directory alone when
`core.hooksPath` is set (a team tool such as husky owns it) or when a hook that is not ours already exists, and
reports that, so `house adapt` and `house doctor` can warn.

Contract: exit 1 refuses (reasons on stderr, which the agent reads); exit 0 allows. An internal error fails open
with one warning line, like `bin/house-hook`. There is deliberately **no environment switch** (not even
`HOUSE_GUARD=off`): the agent controls the environment of its own commands. The agent-side guard
(`guard.check_bash`) refuses `--no-verify`, `core.hooksPath` and edits of `.git/hooks`. The human bypasses a hook by
committing in the main checkout. Internal commits by house (`night`, `exp`) go through the same hooks, so a secret
left in a worktree stays uncommitted and `house merge` then refuses the dirty worktree.
"""
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import HOUSE_ROOT, guard
from . import spec as specmod

HOOKS = ("pre-commit", "pre-push")
MARKER = "# house-githook v1"
SEQUENCE_STATE = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply")
GIT_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_PREFIX", "GIT_INDEX_FILE")


@dataclass
class InstallResult:
    """Result of `install`.

    Attributes:
        written: Names of the hooks that were written or refreshed.
        problems: Why hooks are not (fully) in place: a foreign `core.hooksPath`, a foreign hook file, a missing or
            outdated wrapper (only with `write=False`). Empty means every hook is ours and current.
    """
    written: list[str]
    problems: list[str]


def _git(cwd: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run `git *args` in `cwd` (text output, never raises on a non-zero exit)."""
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False, env=env)


def wrapper(root: Path, name: str) -> str:
    """Text of the hook script `name`: runs `<root>/bin/house-githook <name> …` and skips with a warning (exit 0)
    when that file is gone, so moving the house checkout never blocks commits."""
    hook = Path(root).resolve() / "bin/house-githook"
    return (f"#!/bin/sh\n{MARKER} (written by `house adapt`; do not edit)\n"
            f"hook={shlex.quote(str(hook))}\n"
            f'if [ -x "$hook" ]; then exec "$hook" {name} "$@"; fi\n'
            'echo "house: git hook skipped ($hook is missing; run house adapt)" >&2\nexit 0\n')


def _hooks_dir(contest: Path) -> tuple[Path | None, str | None]:
    """The directory git reads hooks from, or (None, why house must not write there)."""
    configured = _git(contest, "config", "--get", "core.hooksPath")
    if configured.returncode == 0 and configured.stdout.strip():
        return None, (f"core.hooksPath is set to {configured.stdout.strip()}: house git hooks not installed "
                      "(commits and pushes in task worktrees are checked only by `house merge`)")
    r = _git(contest, "rev-parse", "--path-format=absolute", "--git-path", "hooks")
    if r.returncode != 0:
        return None, f"{contest} is not a git repository: house git hooks not installed"
    return Path(r.stdout.strip()), None


def install(contest: Path, root: Path = HOUSE_ROOT, write: bool = True) -> InstallResult:
    """Install (or, with `write=False`, only inspect) the house git hooks of a contest repo.

    Args:
        contest: The contest checkout (the directory with `.house/`).
        root: The house checkout whose `bin/house-githook` the hooks call.
        write: False to report the state without touching files (used by `house doctor`).

    Returns:
        What was written and what is wrong; see `InstallResult`.
    """
    directory, problem = _hooks_dir(contest)
    if directory is None:
        return InstallResult([], [problem] if problem else [])
    written, problems = [], []
    for name in HOOKS:
        path, text = directory / name, wrapper(root, name)
        current = path.read_text(errors="replace") if path.is_file() else None
        if current is not None and MARKER not in current:
            problems.append(f"{path} exists and is not a house hook; left in place, {name} is not enforced")
        elif current != text:
            if not write:
                problems.append(f"git hook {name} is " + ("missing" if current is None else "outdated")
                                + " (run: house adapt)")
                continue
            directory.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            path.chmod(0o755)
            written.append(name)
    return InstallResult(written, problems)


def in_linked_worktree(cwd: Path) -> bool:
    """True when `cwd` is in a linked worktree (its git dir differs from the repo's common git dir)."""
    own = _git(cwd, "rev-parse", "--absolute-git-dir").stdout.strip()
    common = _git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    return bool(own and common) and Path(own).resolve() != Path(common).resolve()


def _staged(cwd: Path, env: dict) -> list[tuple[str, str]]:
    """[(status letter, path)] of the staged changes; renames are reported as a delete plus an add."""
    r = subprocess.run(["git", "diff", "--cached", "--name-status", "-z", "--no-renames"], cwd=cwd,
                       capture_output=True, check=False, env=env)
    parts = [os.fsdecode(p) for p in r.stdout.split(b"\0")]
    return [(s, p) for s, p in zip(parts[0::2], parts[1::2], strict=False) if s and p]


def pre_commit(cwd: Path, index_file: str | None = None) -> list[str]:
    """Reasons to refuse the commit being made in `cwd` (see the module docstring); [] allows it.

    Args:
        cwd: The directory the hook runs in (the worktree's top level).
        index_file: Value of `$GIT_INDEX_FILE` when git uses a temporary index (`commit -a`, `commit <path>`).
    """
    contest = guard.contest_root(cwd)
    if contest is None or not in_linked_worktree(cwd):
        return []
    env = {k: v for k, v in os.environ.items() if k not in GIT_ENV}
    if index_file:
        env["GIT_INDEX_FILE"] = index_file
    gitdir = Path(_git(cwd, "rev-parse", "--absolute-git-dir").stdout.strip())
    sequence = any((gitdir / name).exists() for name in SEQUENCE_STATE)
    protected = guard.protected_paths(contest)
    problems = []
    for status, rel in _staged(cwd, env):
        content = None
        if status != "D":
            blob = subprocess.run(["git", "show", f":{rel}"], cwd=cwd, capture_output=True, check=False, env=env)
            content = blob.stdout if blob.returncode == 0 else b""
        if not sequence:
            if rel == ".house" or rel.startswith(".house/"):
                problems.append(f"{rel}: task branches must not carry .house/ files (write via $HOUSE_DIR)")
            else:
                why = guard.protected_violation(contest, rel, content, protected)
                if why:
                    problems.append(why)
        if content is not None and guard.looks_secret(Path(rel).name, content):
            problems.append(f"{rel} looks like a secret (keep keys in an ignored .env)")
    return problems


def pre_push(cwd: Path, remote: str, lines: list[str]) -> list[str]:
    """Reasons to refuse the push from `cwd` to `remote`; `lines` are git's stdin lines
    `<local ref> <local sha> <remote ref> <remote sha>`. [] allows it."""
    contest = guard.contest_root(cwd)
    if contest is None or not in_linked_worktree(cwd):
        return []
    allow = specmod.get(specmod.load_spec(contest / ".house"), "guard.push_allow") or ["origin"]
    problems = []
    if remote not in allow:
        problems.append(f"push to `{remote}` is not allowed (spec guard.push_allow: {', '.join(map(str, allow))})")
    main = _git(contest, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    for line in lines:
        fields = line.split()
        if len(fields) != 4 or fields[2] != f"refs/heads/{main}":
            continue
        _, local_sha, _, remote_sha = fields
        if set(local_sha) == {"0"}:
            problems.append(f"deleting the contest's main branch `{main}` is not allowed")
        elif set(remote_sha) != {"0"} and _git(cwd, "merge-base", "--is-ancestor", remote_sha, local_sha).returncode:
            problems.append(f"force push (not a fast-forward) to the contest's main branch `{main}` is not allowed")
    return problems


def main(argv: list[str], stdin=None) -> int:
    """Entry point of `bin/house-githook <hook> [git's arguments]`; returns the exit code (1 refuses)."""
    name = argv[0] if argv else ""
    index_file = os.environ.get("GIT_INDEX_FILE")
    for var in GIT_ENV:  # git exports these to hooks; later git calls must discover the repo from their own cwd
        os.environ.pop(var, None)
    try:
        cwd = Path.cwd()
        if name == "pre-commit":
            problems = pre_commit(cwd, index_file)
        elif name == "pre-push":
            problems = pre_push(cwd, argv[1] if len(argv) > 1 else "", (stdin or sys.stdin).read().splitlines())
        else:
            return 0
    except Exception as e:  # noqa: BLE001 — a hook must not stop work because of its own bug (fail open)
        print(f"house-githook: skipped ({type(e).__name__}: {e})", file=sys.stderr)
        return 0
    if not problems:
        return 0
    print(f"house git hook ({name}) refused:", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    print("If this is really needed, ask the coordinator / human (the main checkout is not checked by hooks).",
          file=sys.stderr)
    return 1
