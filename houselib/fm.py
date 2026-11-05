"""Minimal YAML-subset parser: flat `key: value`, inline lists, null/bool/int, quoted strings."""


def parse_scalar(s: str):
    s = s.strip()
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        return [str(parse_scalar(x)) for x in inner.split(",")] if inner else []
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
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


def parse_mapping(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise ValueError(f"not a key: value line: {line!r}")
        out[key.strip()] = parse_scalar(value)
    return out


def parse_frontmatter(text: str) -> tuple[dict, str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing frontmatter block")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return parse_mapping("".join(lines[1:i])), "".join(lines[i + 1:])
    raise ValueError("unterminated frontmatter block")
