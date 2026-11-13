"""Minimal YAML-subset parser: flat `key: value`, inline lists, block lists (`- item`, one level of mappings),
null/bool/int, quoted strings. Plus `set_fields` to update flat keys in place.

Used by: every module that reads task files, agent profiles, `spec.yaml` or `contests.yaml` (frontmatter
between `---` lines in `.house/tasks/T###-*.md`, `core/agents/*.md`, skills). Not a full YAML parser: nested
mappings, multi-line strings and anchors are not supported and raise ValueError or are read as plain text.
Agents editing task frontmatter should keep to this subset (status values: todo, doing, review, merged, done,
blocked) and prefer `set_fields` for programmatic edits, which leaves every other line untouched.
"""
import json
import re

_KEY_RE = re.compile(r"^[A-Za-z_][\w-]*$")


def parse_scalar(s: str):
    """Parse one YAML scalar or inline list.

    Args:
        s: The text of the value. Forms: `[a, b]` (list of strings), "double" (JSON escapes), 'single' (`''` is
            a quote), empty string, `null` or `~`, `true`/`false`, an integer, anything else as a plain string.

    Returns:
        A list, str, None, bool or int.
    """
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
    """Return the number of leading spaces of the line."""
    return len(line) - len(line.lstrip(" "))


def _key_value(text: str) -> tuple[str, str] | None:
    """Split `key: value` into `(key, raw value)`; None when the text is not a simple mapping entry."""
    key, sep, value = text.partition(":")
    if sep and _KEY_RE.fullmatch(key.strip()) and (value == "" or value.startswith(" ")):
        return key.strip(), value
    return None


def _parse_block_list(lines: list[str], i: int) -> tuple[list, int]:
    """Parse `- item` lines starting at lines[i].

    Args:
        lines: All lines of the document.
        i: Index of the first `- ` line.

    Returns:
        `(items, index after the list)`. An item is a scalar or a flat mapping (`- key: v` plus indented keys).

    Raises:
        ValueError: Deeper nesting than one mapping level, or bad indentation.
    """
    items: list = []
    dash = _indent(lines[i])
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        ind = _indent(line)
        if ind < dash or (ind == dash and not line.lstrip().startswith("- ")):
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
    """Parse a document of top-level `key: value` lines (comments and blank lines allowed).

    Args:
        text: The YAML-subset text. A key with an empty value followed by `- ` lines becomes a block list.

    Returns:
        A dict of key to parsed value.

    Raises:
        ValueError: A line is not `key: value`, or a block list is nested too deeply.
    """
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
            if j < len(lines) and lines[j].lstrip().startswith("- "):  # indented or at column 0
                out[key.strip()], i = _parse_block_list(lines, j)
                continue
        out[key.strip()] = parse_scalar(value)
    return out


def _split(text: str) -> tuple[list[str], int]:
    """Return the lines of the text and the index of the closing `---`; raise ValueError if there is no block."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing frontmatter block")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines, i
    raise ValueError("unterminated frontmatter block")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a Markdown file into frontmatter and body.

    Args:
        text: File content starting with a `---` line, the frontmatter, and a closing `---` line.

    Returns:
        `(frontmatter as a dict, the body after the closing line)`.

    Raises:
        ValueError: Missing or unterminated frontmatter block, or invalid content (see `parse_mapping`).
    """
    lines, end = _split(text)
    return parse_mapping("".join(lines[1:end])), "".join(lines[end + 1:])


def _render(value) -> str:
    """Render a Python value as a YAML scalar, quoting it when plain form would be read back differently."""
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
    """Set top-level scalar keys in the frontmatter; every other line (block lists, body) stays byte-identical.

    Args:
        text: File content with a frontmatter block.
        updates: Key to new value (str, int, bool or None). Existing keys are replaced in place; new keys are
            added at the end of the frontmatter.

    Returns:
        The new file content.

    Raises:
        ValueError: Missing or unterminated frontmatter block.
    """
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
