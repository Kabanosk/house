"""`house new`: scaffold a contest repo in contests/<name>.

Used by: `house new <contest> [--repo URL]` and `house adopt <contest> <path>` (owner or coordinator, once
per contest) and `adapters.adapt` (via `install_core`). A contest repo gets `.house/` (spec, tasks, playbooks,
protocols, reports, ... from `core/templates/house`), a `.gitignore` and an entry in contests.yaml.
Rules: contest names are lowercase words/numbers separated by single hyphens; `install_core` never overwrites
an existing `.house/` except playbooks and protocols, which are refreshed from `core/`.
"""
import re
import shutil
from pathlib import Path

from . import HOUSE_ROOT, doctor, registry
from .gitutil import git, identity, set_identity

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def validate_name(name: str) -> None:
    """Check a contest name.

    Args:
        name: Candidate name, e.g. "grok-bot-wro-2026": lowercase letters and digits, words joined by single hyphens.

    Raises:
        ValueError: The name does not match (this also blocks path tricks such as "..").
    """
    if not NAME_RE.fullmatch(name):
        raise ValueError(
            f"invalid contest name {name!r}: use lowercase words/numbers separated by single hyphens, "
            "e.g. grok-bot-wro-2026"
        )


CONTEST_GITIGNORE = ".env*\n!.env.example\n.worktrees/\nnode_modules/\n__pycache__/\n.venv/\n"


def install_core(contest: Path, root: Path) -> None:
    """Copy the `.house/` templates (only if absent) and refresh playbooks and protocols from `core/`.

    Args:
        contest: Path of the contest checkout.
        root: House repo root; sources are `root/core/templates/house`, `root/core/playbooks`, `root/core/protocols`.
    """
    house = contest / ".house"
    if not house.exists():
        shutil.copytree(root / "core/templates/house", house)
    for part in ("playbooks", "protocols"):
        shutil.copytree(root / "core" / part, house / part, dirs_exist_ok=True)


def adopt(name: str, repo: Path, root: Path = HOUSE_ROOT, detect=doctor.detect) -> Path:
    """Attach an existing contest repo in place: contests/<name> becomes a symlink, .house/ is added, nothing is
    committed (the owner decides on which branch the house files live).

    Args:
        name: Contest name (validated).
        repo: Root of an existing git repository; `~` is expanded.
        root: House repo root.
        detect: Callable returning the tools dict (default `doctor.detect`); replace in tests.

    Returns:
        The symlink path `root/contests/<name>`. The repo's `origin` URL (if any) is registered in contests.yaml.

    Raises:
        ValueError: Invalid name, or `repo` is not the root of a git repository.
        FileExistsError: `contests/<name>` already exists.
    """
    validate_name(name)
    repo = repo.expanduser().resolve()
    if not (repo / ".git").exists():
        raise ValueError(f"{repo} is not the root of a git repository")
    link = root / "contests" / name
    if link.exists() or link.is_symlink():
        raise FileExistsError(f"{link} already exists")
    link.parent.mkdir(exist_ok=True)
    link.symlink_to(repo, target_is_directory=True)
    install_core(repo, root)
    doctor.write(repo, detect())
    try:
        url = git(repo, "remote", "get-url", "origin").strip()
    except RuntimeError:
        url = ""
    registry.add(root / "contests.yaml", name, url)
    return link


def new_contest(name: str, repo_url: str | None, root: Path = HOUSE_ROOT, detect=doctor.detect) -> Path:
    """Create a new contest repo and register it.

    Args:
        name: Contest name (validated).
        repo_url: Git URL to clone, or None to `git init -b main` an empty repo.
        root: House repo root.
        detect: Callable returning the tools dict (default `doctor.detect`); replace in tests.

    Returns:
        The contest path `root/contests/<name>`. The repo gets the local git identity of `root`, `.house/`,
        `.gitignore` (when absent) and one commit "chore: scaffold house".

    Raises:
        ValueError: Invalid name.
        FileExistsError: The contest directory already exists.
        RuntimeError: A git command failed (e.g. the clone).
    """
    validate_name(name)
    contest = root / "contests" / name
    if contest.exists():
        raise FileExistsError(f"{contest} already exists")
    user, email = identity(root)
    contest.parent.mkdir(exist_ok=True)
    if repo_url:
        git(contest.parent, "clone", "-q", repo_url, name)
    else:
        contest.mkdir()
        git(contest, "init", "-q", "-b", "main")
    set_identity(contest, user, email)
    install_core(contest, root)
    doctor.write(contest, detect())
    if not (contest / ".gitignore").exists():
        (contest / ".gitignore").write_text(CONTEST_GITIGNORE)
    registry.add(root / "contests.yaml", name, repo_url or "")
    git(contest, "add", ".house", ".gitignore")
    if git(contest, "status", "--porcelain", "--", ".house", ".gitignore").strip():
        git(contest, "commit", "-q", "-m", "chore: scaffold house")
    return contest
