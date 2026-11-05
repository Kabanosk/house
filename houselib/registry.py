"""contests.yaml: one `name: url` per line (url may be empty = local only)."""
from pathlib import Path

from .fm import parse_mapping

def load(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return {k: ("" if v is None else str(v)) for k, v in parse_mapping(path.read_text()).items()}


def add(path: Path, name: str, url: str) -> None:
    entries = load(path)
    entries[name] = url
    body = "".join(f"{k}: {v}\n" if v else f"{k}:\n" for k, v in sorted(entries.items()))
    path.write_text(body)
