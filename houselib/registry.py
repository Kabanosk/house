"""contests.yaml: one `name: url` per line (url may be empty = local only).

Used by: `house new`, `house adopt` (register a contest), `house sync` (clone missing contests), and
`house inbox`/`deadlines`/`doctor` (list contests). The file lives in the house repo root and is read with the
`fm` YAML subset.
"""
from pathlib import Path

from . import HOUSE_ROOT
from .fm import parse_mapping

DEFAULT_ROOT = HOUSE_ROOT

def load(path: Path) -> dict[str, str]:
    """Read the registry.

    Args:
        path: Path of `contests.yaml`; a missing file means an empty registry.

    Returns:
        Contest name to URL; "" for local-only contests.
    """
    if not path.exists():
        return {}
    return {k: ("" if v is None else str(v)) for k, v in parse_mapping(path.read_text()).items()}


def contests(root: Path | None = None) -> list[Path]:
    """List registered contests that exist on this machine.

    Args:
        root: House repo root; defaults to `DEFAULT_ROOT`.

    Returns:
        `root/contests/<name>` for each registered name that has a `.house/` directory (symlinks not resolved).
    """
    root = root or DEFAULT_ROOT
    return [root / "contests" / n for n in load(root / "contests.yaml") if (root / "contests" / n / ".house").is_dir()]


def name_for(path: Path, root: Path | None = None) -> str:
    """Find a contest's registry name.

    Args:
        path: A contest path; a resolved path of an adopted (symlinked) repo is matched too.
        root: House repo root; defaults to `DEFAULT_ROOT`.

    Returns:
        The registered name, else the last path component.
    """
    root = root or DEFAULT_ROOT
    target = Path(path).resolve()
    for n in load(root / "contests.yaml"):
        if (root / "contests" / n).resolve() == target:
            return n
    return Path(path).name


def add(path: Path, name: str, url: str) -> None:
    """Add or update a contest in the registry file (entries are written sorted).

    Args:
        path: Path of `contests.yaml`; created when missing.
        name: Contest name.
        url: Git URL, or "" for a local-only contest.
    """
    entries = load(path)
    entries[name] = url
    body = "".join(f"{k}: {v}\n" if v else f"{k}:\n" for k, v in sorted(entries.items()))
    path.write_text(body)


def sync(root: Path) -> list[str]:
    """Clone registered contests that are missing locally. Empty URL = local only, skipped.

    Args:
        root: House repo root; clones go to `root/contests/<name>` with the local git identity of `root`.

    Returns:
        Names of the contests cloned.

    Raises:
        RuntimeError: A git clone failed.
    """
    from .gitutil import git, identity, set_identity

    user, email = identity(root)
    cloned = []
    for name, url in load(root / "contests.yaml").items():
        target = root / "contests" / name
        if not url or target.exists():
            continue
        target.parent.mkdir(exist_ok=True)
        git(target.parent, "clone", "-q", url, name)
        set_identity(target, user, email)
        cloned.append(name)
    return cloned
