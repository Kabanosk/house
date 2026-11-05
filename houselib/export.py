"""`house export`: clean public copy of a contest repo."""
import re
import shutil
from pathlib import Path

from .gitutil import git, identity, set_identity

EXCLUDED_DIRS = {".house", ".claude", ".cursor", ".codex"}
EXCLUDED_FILES = {"AGENTS.md", "CLAUDE.md"}
SECRET_RES = [re.compile(p) for p in (
    r"sk[-_][A-Za-z0-9_-]{20,}",
    r"AIza[0-9A-Za-z_-]{35}",
    r"hf_[A-Za-z0-9]{30,}",
    r"github_pat_[A-Za-z0-9_]{30,}",
    r"gh[opsu]_[A-Za-z0-9]{30,}",
    r"xox[abprs]-[A-Za-z0-9-]{10,}",
    r"(?i)(api[_-]?key|secret|token|password)\w*[\"']?\s*[:=]\s*[\"'][^\"'\s<>]{16,}[\"']",
    r"xai-[A-Za-z0-9]{20,}",
    r"ghp_[A-Za-z0-9]{30,}",
    r"AKIA[0-9A-Z]{16}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
)]


class ExportBlocked(Exception):
    def __init__(self, paths: list[str]):
        super().__init__("possible secrets in: " + ", ".join(paths))
        self.paths = paths


def is_excluded(rel: str) -> bool:
    parts = Path(rel).parts
    name = parts[-1]
    if any(p in EXCLUDED_DIRS for p in parts[:-1]) or name in EXCLUDED_FILES:
        return True
    return name.startswith(".env") and name != ".env.example"


def has_secret(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False  # links are exported as links (target content never copied); dirs = submodules
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return False
    return any(r.search(text) for r in SECRET_RES)


def plan(contest: Path) -> tuple[list[str], list[str]]:
    """(files to export, excluded files) among tracked files."""
    tracked = [f for f in git(contest, "ls-files", "-z").split("\0") if f]
    keep = [f for f in tracked if not is_excluded(f)]
    return keep, [f for f in tracked if is_excluded(f)]


def export(contest: Path, dest: Path, force: bool = False) -> list[Path]:
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise FileExistsError(f"{dest} exists and is not an empty directory")
    keep, _ = plan(contest)
    # skip tracked files deleted from the working tree and submodule gitlinks (directories)
    keep = [f for f in keep if (contest / f).is_symlink() or (contest / f).is_file()]
    hits = [f for f in keep if has_secret(contest / f)]
    if hits and not force:
        raise ExportBlocked(hits)
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    for rel in keep:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(contest / rel, target, follow_symlinks=False)
        out.append(target)
    git(dest, "init", "-q", "-b", "main")
    set_identity(dest, *identity(contest))
    git(dest, "add", "-A")
    git(dest, "commit", "-q", "-m", f"{contest.name}: public export")
    return out
