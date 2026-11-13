"""Record of the files `house adapt` generated in a contest repo (`.house/generated.json`).

house writes, backs up and deletes only files it generated itself. A file someone edited by hand is kept as
`<name>.orig` before being regenerated; a file house never generated is never touched.
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
    written: list[str] = field(default_factory=list)
    backed_up: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    kept_modified: list[str] = field(default_factory=list)
    backups: list[str] = field(default_factory=list)  # relpaths of the backup files actually created


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(contest: Path) -> dict[str, str] | None:
    data = json.loads((contest / MANIFEST).read_text())
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict):
        raise TypeError("no files map")
    return {str(k): str(v) for k, v in files.items()}


def load(contest: Path) -> dict[str, str] | None:
    """The recorded files, or None when there is no usable manifest (never written, or unreadable)."""
    if not (contest / MANIFEST).exists():
        return None
    try:
        return _read(contest)
    except (ValueError, TypeError):
        return None


def is_corrupt(contest: Path) -> bool:
    """The manifest exists but cannot be read (e.g. merge-conflict markers)."""
    return (contest / MANIFEST).exists() and load(contest) is None


def save(contest: Path, files: dict[str, str]) -> Path:
    path = contest / MANIFEST
    path.write_text(json.dumps({"version": 1, "files": dict(sorted(files.items()))}, indent=2) + "\n")
    return path


def _needs_backup(path: Path, rel: str, new_sha: str, old: dict[str, str] | None,
                  legacy_owned: Callable[[str], bool]) -> bool:
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
    d = path.parent
    while d != stop and d.is_dir() and not any(d.iterdir()):
        d.rmdir()
        d = d.parent


def apply(contest: Path, generated: dict[str, str], old: dict[str, str] | None,
          legacy_owned: Callable[[str], bool]) -> WriteResult:
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
    out = []
    for rel, sha in sorted((load(contest) or {}).items()):
        path = contest / rel
        if not path.is_file():
            out.append((rel, "missing"))
        elif _sha256_file(path) != sha:
            out.append((rel, "modified"))
    return out
