"""contests.yaml: one `name: url` per line (url may be empty = local only)."""
from pathlib import Path

from . import HOUSE_ROOT
from .fm import parse_mapping

DEFAULT_ROOT = HOUSE_ROOT

def load(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return {k: ("" if v is None else str(v)) for k, v in parse_mapping(path.read_text()).items()}


def contests(root: Path | None = None) -> list[Path]:
    """Registered contests present on this machine (paths under contests/, symlinks not resolved)."""
    root = root or DEFAULT_ROOT
    return [root / "contests" / n for n in load(root / "contests.yaml") if (root / "contests" / n / ".house").is_dir()]


def name_for(path: Path, root: Path | None = None) -> str:
    """A contest's registry name, also when given its resolved path (adopted repos are symlinks)."""
    root = root or DEFAULT_ROOT
    target = Path(path).resolve()
    for n in load(root / "contests.yaml"):
        if (root / "contests" / n).resolve() == target:
            return n
    return Path(path).name


def add(path: Path, name: str, url: str) -> None:
    entries = load(path)
    entries[name] = url
    body = "".join(f"{k}: {v}\n" if v else f"{k}:\n" for k, v in sorted(entries.items()))
    path.write_text(body)


def sync(root: Path) -> list[str]:
    """Clone registered contests that are missing locally. Empty URL = local only, skipped."""
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
