"""`house export`: clean public copy of a contest repo.

Used by: `house export <contest> <dest> [--force]` (owner, when a contest is published). Exports only tracked
files, leaves out house and agent files (`.house/`, `.claude/`, `.cursor/`, `.codex/`, AGENTS.md, CLAUDE.md,
`.env*` except `.env.example`), scans the rest for secrets and creates a fresh one-commit git repo in `dest`.
Rules: a secret-scan hit blocks the export (exit code 1) until the file is fixed or `--force` is given for a
false positive; `dest` must be missing or empty.
"""
import re
import shutil
from pathlib import Path

from .gitutil import git, identity, set_identity

EXCLUDED_DIRS = {".house", ".claude", ".cursor", ".codex"}
EXCLUDED_FILES = {"AGENTS.md", "CLAUDE.md"}
# Prefix patterns must not start inside an identifier: `task_dependencies_…` or `attention_mask_…` contain
# `sk_` + 20 characters and are code, not keys.
SECRET_RES = [re.compile(p) for p in (
    r"(?<![A-Za-z0-9_])sk[-_][A-Za-z0-9_-]{20,}",
    r"(?<![A-Za-z0-9_])AIza[0-9A-Za-z_-]{35}",
    r"(?<![A-Za-z0-9_])hf_[A-Za-z0-9]{30,}",
    r"(?<![A-Za-z0-9_])github_pat_[A-Za-z0-9_]{30,}",
    r"(?<![A-Za-z0-9_])gh[opsu]_[A-Za-z0-9]{30,}",
    r"(?<![A-Za-z0-9_])xox[abprs]-[A-Za-z0-9-]{10,}",
    r"(?i)(api[_-]?key|secret|token|password)\w*[\"']?\s*[:=]\s*[\"'][^\"'\s<>]{16,}[\"']",
    r"(?<![A-Za-z0-9_])xai-[A-Za-z0-9]{20,}",
    r"(?<![A-Za-z0-9_])ghp_[A-Za-z0-9]{30,}",
    r"(?<![A-Za-z0-9_])AKIA[0-9A-Z]{16}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
)]


class ExportBlocked(Exception):
    """Raised by `export` when the secret scan found something.

    Attributes:
        paths: Relative paths of the files with possible secrets.
    """
    def __init__(self, paths: list[str]):
        """Create the exception.

        Args:
            paths: Relative paths of the files with possible secrets; they appear in the message.
        """
        super().__init__("possible secrets in: " + ", ".join(paths))
        self.paths = paths


def is_excluded(rel: str) -> bool:
    """Tell whether a tracked file stays out of the public export.

    Args:
        rel: Path relative to the repo root, with "/" separators.

    Returns:
        True for files in `.house`, `.claude`, `.cursor`, `.codex`, for AGENTS.md and CLAUDE.md, and for `.env*`
        files other than `.env.example`.
    """
    parts = Path(rel).parts
    name = parts[-1]
    if any(p in EXCLUDED_DIRS for p in parts[:-1]) or name in EXCLUDED_FILES:
        return True
    return name.startswith(".env") and name != ".env.example"


def has_secret(path: Path) -> bool:
    """Scan one file for secret-looking text (API keys, tokens, private keys).

    Args:
        path: File to scan. Symlinks, directories (submodules) and unreadable files are never reported.

    Returns:
        True when any SECRET_RES pattern matches.
    """
    if path.is_symlink() or not path.is_file():
        return False  # links are exported as links (target content never copied); dirs = submodules
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return False
    return any(r.search(text) for r in SECRET_RES)


def plan(contest: Path) -> tuple[list[str], list[str]]:
    """Split the tracked files into exported and excluded ones.

    Args:
        contest: Path of the contest repo.

    Returns:
        `(files to export, excluded files)`, relative paths from `git ls-files`.
    """
    tracked = [f for f in git(contest, "ls-files", "-z").split("\0") if f]
    keep = [f for f in tracked if not is_excluded(f)]
    return keep, [f for f in tracked if is_excluded(f)]


def export(contest: Path, dest: Path, force: bool = False) -> list[Path]:
    """Write the clean copy and commit it in a new git repo.

    Args:
        contest: Path of the contest repo.
        dest: Target directory; it must not exist or must be empty.
        force: Export even when the secret scan has hits.

    Returns:
        Paths of the files written under `dest`. The new repo is initialised on branch `main` with the identity
        of the source repo and one commit.

    Raises:
        FileExistsError: `dest` exists and is not an empty directory.
        ExportBlocked: Secrets were found and `force` is False.
    """
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
