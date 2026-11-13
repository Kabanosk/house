"""Record of the files `house adapt` generated in a contest repo (`.house/generated.json`).

house writes, backs up and deletes only files it generated itself. A file someone edited by hand is kept as
`<name>.orig` before being regenerated; a file house never generated is never touched.

Used by: `adapters.adapt` (`house adapt <contest>`) and `house doctor <contest>` (drift report). Agents should
not edit generated files (AGENTS.md, CLAUDE.md, `.claude/agents`, `.claude/skills`): change `core/` or
`adapters/` in the house repo and re-run `house adapt`. The manifest maps relative path to SHA-256 of the text
house wrote.
"""
import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST = ".house/generated.json"


@dataclass
class WriteResult:
    """What `apply` did, as paths relative to the contest.

    Attributes:
        written: Files written.
        backed_up: Files that were moved aside before being rewritten.
        removed: Files that are no longer generated and were deleted (unmodified ones only).
        kept_modified: No longer generated files left in place because someone edited them (or they are links).
        backups: Relative paths of the backup files actually created, in the order of `backed_up`.
    """
    written: list[str] = field(default_factory=list)
    backed_up: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    kept_modified: list[str] = field(default_factory=list)
    backups: list[str] = field(default_factory=list)  # relpaths of the backup files actually created


def sha256_text(text: str) -> str:
    """Hash text.

    Args:
        text: Text to hash (encoded as UTF-8).

    Returns:
        The SHA-256 hex digest.
    """
    return hashlib.sha256(text.encode()).hexdigest()


def _sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of the file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(contest: Path) -> dict[str, str] | None:
    """Read the manifest's files map; raise ValueError or TypeError when it is malformed."""
    data = json.loads((contest / MANIFEST).read_text())
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict):
        raise TypeError("no files map")
    return {str(k): str(v) for k, v in files.items()}


def load(contest: Path) -> dict[str, str] | None:
    """Read the manifest.

    Args:
        contest: Path of the contest checkout.

    Returns:
        Relative path to recorded SHA-256, or None when there is no usable manifest (never written, or unreadable).
    """
    if not (contest / MANIFEST).exists():
        return None
    try:
        return _read(contest)
    except (ValueError, TypeError):
        return None


def is_corrupt(contest: Path) -> bool:
    """Tell whether the manifest exists but cannot be read (e.g. merge-conflict markers).

    Args:
        contest: Path of the contest checkout.

    Returns:
        True for an existing, unreadable manifest.
    """
    return (contest / MANIFEST).exists() and load(contest) is None


def save(contest: Path, files: dict[str, str]) -> Path:
    """Write the manifest.

    Args:
        contest: Path of the contest checkout.
        files: Relative path to SHA-256 of every generated file.

    Returns:
        The path of `.house/generated.json`.
    """
    path = contest / MANIFEST
    path.write_text(json.dumps({"version": 1, "files": dict(sorted(files.items()))}, indent=2) + "\n")
    return path


def _needs_backup(path: Path, rel: str, new_sha: str, old: dict[str, str] | None,
                  legacy_owned: Callable[[str], bool]) -> bool:
    """Decide whether an existing file must be moved aside before it is overwritten.

    Args:
        path: Absolute path of the file about to be written.
        rel: Its path relative to the contest.
        new_sha: SHA-256 of the text about to be written.
        old: The previous manifest, or None when there was none.
        legacy_owned: Tells for paths without a manifest whether an older house generated them.
    """
    if path.is_symlink():
        return True  # e.g. CLAUDE.md -> AGENTS.md: never write through someone's link
    if not path.is_file():
        return False
    current = _sha256_file(path)
    if current == new_sha:
        return False
    if old is not None and rel in old:
        return current != old[rel]  # ours, but edited by hand since we wrote it
    # no manifest: an older house generated it (legacy_owned) or it is someone else's file
    return not (old is None and legacy_owned(rel))


def _backup_path(path: Path) -> Path:
    """`<name>.orig`, or a timestamped name when an older backup already exists (never overwrite a backup)."""
    backup = path.with_name(path.name + ".orig")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    n = 0
    while backup.exists() or backup.is_symlink():
        n += 1
        backup = path.with_name(f"{path.name}.orig-{stamp}" + (f"-{n}" if n > 1 else ""))
    return backup


def _prune_empty_dirs(path: Path, stop: Path) -> None:
    """Remove empty parent directories of path, up to but excluding stop."""
    d = path.parent
    while d != stop and d.is_dir() and not any(d.iterdir()):
        d.rmdir()
        d = d.parent


def apply(contest: Path, generated: dict[str, str], old: dict[str, str] | None,
          legacy_owned: Callable[[str], bool]) -> WriteResult:
    """Write generated files and delete the ones house no longer generates, without touching foreign work.

    Args:
        contest: Path of the contest checkout.
        generated: Relative path to the full text to write.
        old: Previous manifest (`load`), or None when there was none.
        legacy_owned: Called with a relative path when there is no manifest; True if an older house wrote it.

    Returns:
        A `WriteResult`. A file that differs from what house wrote last (or is someone else's, or a symlink) is
        renamed to a backup first. Files in `old` but not in `generated` are deleted only when unmodified.
    """
    res = WriteResult()
    for rel, text in generated.items():
        path = contest / rel
        if _needs_backup(path, rel, sha256_text(text), old, legacy_owned):
            backup = _backup_path(path)
            path.rename(backup)
            res.backed_up.append(rel)
            res.backups.append(str(backup.relative_to(contest)))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        res.written.append(rel)
    for rel in sorted(set(old or {}) - set(generated)):
        path = contest / rel
        if path.is_symlink():
            res.kept_modified.append(rel)
            continue
        if not path.is_file():
            continue
        if _sha256_file(path) == old[rel]:
            path.unlink()
            _prune_empty_dirs(path, contest)
            res.removed.append(rel)
        else:
            res.kept_modified.append(rel)
    return res


def drift(contest: Path) -> list[tuple[str, str]]:
    """Compare generated files on disk with the manifest.

    Args:
        contest: Path of the contest checkout.

    Returns:
        Sorted `(relative path, "missing" | "modified")` pairs; empty when everything is in sync or no manifest exists.
    """
    out = []
    for rel, sha in sorted((load(contest) or {}).items()):
        path = contest / rel
        if not path.is_file():
            out.append((rel, "missing"))
        elif _sha256_file(path) != sha:
            out.append((rel, "modified"))
    return out
