"""Read the contest's `.house/spec.yaml`: section → key → scalar / inline list / block list.

Only what house code needs (guard rules, notify, limits); agents read the file themselves. Anything nested
deeper than house reads (e.g. a rubric with per-level descriptions) becomes None instead of failing.

Used by `guard` (e.g. `guard.push_allow`, `scoring.local_scorer`), by `hook.session_start` and by other `house`
commands through `load_spec` + `get`. This is a small purpose-built reader, not a YAML parser: no anchors, no
multi-line scalars, no nesting beyond `section: key: value`. It never raises on a missing or odd file (a missing
file gives `{}`), so a lookup always needs a default for absent keys.
"""
from pathlib import Path

from .fm import parse_mapping


def _strip_comment(line: str) -> str:
    """Drop a trailing ` # comment` that is outside quotes."""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip()
    return line.rstrip()


def _indent(line: str) -> int:
    """Count leading spaces of `line`."""
    return len(line) - len(line.lstrip(" "))


def _parse_entry(head: str, deeper: list[str]):
    """Value of `head` (`key: value`) given its deeper lines; None when nested beyond what house reads."""
    key = head.partition(":")[0].strip()
    body = [ln for ln in deeper if ln.strip()]
    try:
        if not body:
            return parse_mapping(head.strip())[key]
        if body[0].lstrip().startswith("- "):
            return parse_mapping(head.strip() + "\n" + "\n".join(deeper))[key]
        width = min(_indent(ln) for ln in body)
        return _parse_section([ln[width:] for ln in deeper])
    except (ValueError, KeyError):
        return None


def _parse_section(lines: list[str]) -> dict:
    """Parse the mapping at indent 0 of `lines`."""
    out: dict = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip() or _indent(line) > 0 or line.startswith("- "):
            continue
        deeper = []
        while i < len(lines) and (not lines[i].strip() or _indent(lines[i]) > 0 or lines[i].startswith("- ")):
            deeper.append(lines[i])
            i += 1
        out[line.partition(":")[0].strip()] = _parse_entry(line, deeper)
    return out


def load_spec(house: Path) -> dict:
    """Parse `<house>/spec.yaml` into nested dicts (section → key → scalar, list or nested dict).

    Comments are stripped. A value nested deeper than house reads becomes None.

    Args:
        house: The contest's `.house` directory (not the contest root).

    Returns:
        The parsed mapping; `{}` when `spec.yaml` does not exist.
    """
    path = house / "spec.yaml"
    if not path.is_file():
        return {}
    return _parse_section([_strip_comment(ln) for ln in path.read_text().splitlines()])


def get(spec: dict, dotted: str, default=None):
    """Look up a value by dotted path, e.g. `get(spec, "guard.push_allow")`.

    Args:
        spec: Mapping returned by `load_spec`.
        dotted: Keys joined by dots, one per nesting level.
        default: Returned when any key on the path is missing or an intermediate value is not a mapping.

    Returns:
        The value found (a key present with a None value returns None, not `default`), else `default`.
    """
    node = spec
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node
