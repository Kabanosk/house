"""Minimal YAML-subset parser: flat `key: value`, inline lists, block lists (`- item`, one level of mappings),
null/bool/int, quoted strings. Plus `set_fields` to update flat keys in place."""
import json
import re

_KEY_RE = re.compile(r"^[A-Za-z_][\w-]*$")


def parse_scalar(s: str):
    s = s.strip()
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        return [str(parse_scalar(x)) for x in inner.split(",")] if inner else []
    if len(s) >= 2 and s[0] == s[-1] == '"':
        try:
            return json.loads(s)  # YAML double quotes: \" and \\ escapes
        except ValueError:
            return s[1:-1]
    if len(s) >= 2 and s[0] == s[-1] == "'":
        return s[1:-1].replace("''", "'")  # YAML single quotes: '' is a quote
    if s == "":
        return ""
    if s in ("null", "~"):
        return None
    if s == "true":
        return True
    if s == "false":
        return False
    try:
        return int(s)
    except ValueError:
        return s


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _key_value(text: str) -> tuple[str, str] | None:
    key, sep, value = text.partition(":")
    if sep and _KEY_RE.fullmatch(key.strip()) and (value == "" or value.startswith(" ")):
        return key.strip(), value
    return None


def _parse_block_list(lines: list[str], i: int) -> tuple[list, int]:
    """Parse `- item` lines starting at lines[i]; return (items, index after the list)."""
    items: list = []
    dash = _indent(lines[i])
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        ind = _indent(line)
        if ind < dash or (ind == dash and not line.lstrip().startswith("- ")) or ind == 0:
            break
        if ind > dash:
            raise ValueError(f"unexpected indentation: {line!r}")
        text = line.lstrip()[2:]
        kv = _key_value(text)
        i += 1
        if kv is None:
            items.append(parse_scalar(text))
            continue
        item = {kv[0]: parse_scalar(kv[1])}
        field_ind = None
        while i < len(lines) and lines[i].strip() and _indent(lines[i]) > dash:
            sub = lines[i]
            field_ind = field_ind or _indent(sub)
            kv2 = _key_value(sub.strip())
            if _indent(sub) != field_ind or kv2 is None or kv2[1].strip() == "":
                raise ValueError(f"only one level of mapping is supported in lists: {sub!r}")
            item[kv2[0]] = parse_scalar(kv2[1])
            i += 1
        if kv[1].strip() == "":
            raise ValueError(f"only one level of mapping is supported in lists: {line!r}")
        items.append(item)
    return items, i


def parse_mapping(text: str) -> dict:
    out = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise ValueError(f"not a key: value line: {line!r}")
        i += 1
        if value.strip() == "" and _indent(line) == 0:
            j = i
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and _indent(lines[j]) > 0 and lines[j].lstrip().startswith("- "):
                out[key.strip()], i = _parse_block_list(lines, j)
                continue
        out[key.strip()] = parse_scalar(value)
    return out


def _split(text: str) -> tuple[list[str], int]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing frontmatter block")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines, i
    raise ValueError("unterminated frontmatter block")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    lines, end = _split(text)
    return parse_mapping("".join(lines[1:end])), "".join(lines[end + 1:])


def _render(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    s = str(value)
    if s == "" or ": " in s or s[0] in "\"'[#" or parse_scalar(s) != s:
        return json.dumps(s, ensure_ascii=False)
    return s


def set_fields(text: str, updates: dict) -> str:
    """Set top-level scalar keys in the frontmatter; every other line (block lists, body) stays byte-identical."""
    lines, end = _split(text)
    todo = dict(updates)
    for i in range(1, end):
        line = lines[i]
        if _indent(line) == 0:
            key = line.partition(":")[0].strip()
            if key in todo:
                newline = "\n" if line.endswith("\n") else ""
                lines[i] = f"{key}: {_render(todo.pop(key))}{newline}"
    extra = [f"{k}: {_render(v)}\n" for k, v in todo.items()]
    return "".join(lines[:end] + extra + lines[end:])
