"""Hand-off sections: the shared section list of the worker report template and the night brief, and the parser
of worker reports (`report_sections`).

Used by: `night` (brief and report checks), `evidence` (the result section). Keep header names stable: detection
matches them, and the Polish aliases of older reports, case-insensitively. The compaction hand-off that used these
sections was removed after K5 (Claude Code's own compaction instructions did as well, `docs/research/k5/`).
"""
import re

# (English header, accepted aliases) in template order.
SECTIONS: list[tuple[str, tuple[str, ...]]] = [
    ("Goal", ("Cel",)),
    ("Next", ("W toku", "Następne kroki")),
    ("Critical context", ("Krytyczny kontekst",)),
    ("Constraints and human decisions", ("Ograniczenia",)),
    ("Dead ends", ("Ślepe uliczki",)),
    ("Decisions and facts", ("Decyzje",)),
    ("Errors and fixes", ("Błędy",)),
    ("Done", ("Zrobione",)),
]

# Headings of worker reports (`reports/T###.md`) that are not in `SECTIONS`: the added section, the old template's
# names for sections that moved, and `Evidence`, which is recognised only to be ignored (None).
_REPORT_EXTRA: list[tuple[str, str | None]] = [
    ("Result vs acceptance", "Result vs acceptance"),
    ("What was done", "Done"),
    ("Tested and did not work", "Dead ends"),
    ("Next steps", "Next"),
    ("Evidence", None),
]


def _matcher(names: list[tuple[str, str | None]]) -> re.Pattern:
    names = sorted(names, key=lambda p: -len(p[0]))
    return re.compile(
        r"^\s*(?:#{1,6}\s*)?(\*\*)?\s*(?:\d+[.)]\s*)?(" + "|".join(re.escape(n) for n, _ in names) + r")"
        r"(?:\s*\([^)]*\))?\s*(?:\*\*)?\s*(:)?\s*(?:\*\*)?\s*(.*?)\s*$", re.I)


_SECTION_NAMES: list[tuple[str, str | None]] = [(n, h) for h, al in SECTIONS for n in (h, *al)]
_REPORT_NAMES = _SECTION_NAMES + _REPORT_EXTRA
_HEADING = _matcher(_SECTION_NAMES)
_REPORT_HEADING = _matcher(_REPORT_NAMES)
_PLAIN = re.compile(r"^\s*(?:#{1,6}\s*|\d+[.)]\s*|\*\*)")


def _heading(line: str, rx: re.Pattern = _HEADING, names: list[tuple[str, str | None]] = _SECTION_NAMES,
             bare: tuple[str, ...] = ()) -> tuple[str | None, str] | None:
    """Return (English header, inline text after the name) when `line` is a section heading of `names`.

    `bare` lists names that are also accepted without Markdown, bold or number markup when a colon follows them
    (`Result vs acceptance: pass`).
    """
    plain = _PLAIN.match(line)
    if not plain and not bare:
        return None
    m = rx.match(line)
    if not m or (m.group(4) and not m.group(3)):
        return None
    if not plain and not (m.group(3) and m.group(2).lower() in bare):
        return None
    name = m.group(2).lower()
    header = next(h for n, h in names if n.lower() == name)
    return header, m.group(4)


_UNKNOWN_HEADING = re.compile(r"^\s*#{1,2}\s+\S")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_BARE = ("result vs acceptance",)
_HASHES = re.compile(r"^\s*(#{1,6})")
_OLD_NAMES = {n.lower() for n, h in _REPORT_EXTRA if n != "Result vs acceptance"}


def _report_heading(line: str) -> tuple[str | None, str] | None:
    """Return (English header or None for `Evidence`, inline text) when `line` is a heading of a worker report.

    A Markdown heading (`#` to `######`) of any known name counts, an old-template name (`What was done`, `Tested
    and did not work`, `Next steps`, `Evidence`) only at level 1 or 2. A bold or numbered line (`**Goal**`,
    `**Next:**`, `2. Dead ends`, `**1. Done**`) counts only for a name of the hand-off template (or its Polish
    alias) and only when the label is the whole line: `**Evidence:** ...` or `1. Done: edited a.py` is body text.
    `Result vs acceptance` is also accepted with text after its colon, bare or bold.
    """
    m = _REPORT_HEADING.match(line)
    if not m or (m.group(4) and not m.group(3)):
        return None
    name = m.group(2).lower()
    header = next(h for n, h in _REPORT_NAMES if n.lower() == name)
    hashes = _HASHES.match(line)
    if hashes:
        return None if name in _OLD_NAMES and len(hashes.group(1)) > 2 else (header, m.group(4))
    if name in _OLD_NAMES:
        return None
    if m.group(4) or not _PLAIN.match(line):  # text after the label, or no markup at all
        return (header, m.group(4)) if m.group(3) and name in _BARE else None
    return header, ""


def _closes(fence: str, line: str) -> bool:
    """True when `line` is a closing fence for the opening marker `fence`: same character, no shorter, nothing after."""
    marker = line.strip()
    return marker.startswith(fence[0]) and len(marker) >= len(fence) and marker == marker[0] * len(marker)


def report_sections(text: str) -> dict[str, str]:
    """Split a worker report into the sections of the hand-off template.

    Headings are recognised by `_report_heading`: the English names, the Polish aliases and `Result vs acceptance`
    (also as a bare `Result vs acceptance: ...` line), plus the old report template's `What was done` (read as
    `Done`), `Tested and did not work` (read as `Dead ends`) and `Next steps` (read as `Next`) as `#`/`##`
    headings. `Evidence` and any other `#` or `##` heading end the previous section and are ignored. A label line
    inside a section (`**Evidence:** ...`, `1. Done: ...`) does not end it. A `###` sub-heading such as `Observed`,
    `Unverified` or `Attempt 2` stays in the body of its section, unless it is exactly a section name, which starts
    that section. Text before the first heading is ignored.

    Inside a code fence (``` or ~~~) nothing is a heading, so a `# comment` of pasted code or a quoted `## Next`
    does not cut a section. Only a fence that is never closed later in the text must not swallow the report: inside
    it, a `#` or `##` heading naming a known section closes the fence and starts that section.

    Args:
        text: the report file content.

    Returns:
        English section name (a header of `SECTIONS` or `Result vs acceptance`) -> stripped body text. Sections that
        are absent from the report are absent from the dict; a section named twice keeps its first body.
    """
    lines = text.splitlines()
    bodies: dict[str, list[str]] = {}
    current: str | None = None
    fence: str | None = None  # the opening fence marker while inside a code fence
    closed = False  # the open fence has a closing marker later in the text
    for i, line in enumerate(lines):
        found = _report_heading(line)
        fm = _FENCE.match(line)
        if fence:
            if not closed and found and _UNKNOWN_HEADING.match(line):
                fence = None  # an unclosed fence ends at the next known `#`/`##` section heading
            else:
                if fm and _closes(fence, line):
                    fence = None
                if current is not None:
                    bodies[current].append(line)
                continue
        elif fm and not found:
            fence = fm.group(1)
            closed = any(_closes(fence, later) for later in lines[i + 1:])
            if current is not None:
                bodies[current].append(line)
            continue
        if found:
            current = found[0]
            if current is not None and current not in bodies:
                bodies[current] = [found[1]] if found[1] else []
            elif current is not None:
                current = None  # a repeated heading: keep the first body
        elif _UNKNOWN_HEADING.match(line):
            current = None
        elif current is not None:
            bodies[current].append(line)
    return {h: "\n".join(b).strip() for h, b in bodies.items()}
