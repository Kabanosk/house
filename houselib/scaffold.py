"""`house new`: scaffold a contest repo in contests/<name>."""
import re
import shutil
from pathlib import Path

from . import HOUSE_ROOT, doctor, registry
from .gitutil import git, identity, set_identity

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def validate_name(name: str) -> None:
    if not NAME_RE.fullmatch(name):
        raise ValueError(
            f"invalid contest name {name!r}: use lowercase words/numbers separated by single hyphens, "
            "e.g. grok-bot-wro-2026"
        )


CONTEST_GITIGNORE = ".env*\n!.env.example\n.worktrees/\nnode_modules/\n__pycache__/\n.venv/\n"


def install_core(contest: Path, root: Path) -> None:
    """Copy .house/ templates (only if absent) and refresh playbooks/protocols."""
    house = contest / ".house"
    if not house.exists():
        shutil.copytree(root / "core/templates/house", house)
    for part in ("playbooks", "protocols"):
        shutil.copytree(root / "core" / part, house / part, dirs_exist_ok=True)


def adopt(name: str, repo: Path, root: Path = HOUSE_ROOT, detect=doctor.detect) -> Path:
    """Attach an existing contest repo in place: contests/<name> becomes a symlink, .house/ is added, nothing is
    committed (the owner decides on which branch the house files live)."""
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
