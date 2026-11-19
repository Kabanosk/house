"""`.house/DECISIONS.md`: parse the decision log, find the decisions a human made that still stand.

Used by the SessionStart hook (after a context compaction it prints the full text of every active human decision)
and by `house decisions`. Agents add entries (never edit or delete); this module only reads. File format (Polish
field names; see `core/protocols/decisions.md`):

    ## D-007 Short title
    - **Decyzja:** what was decided
    - **Dlaczego:** why
    - **Kto:** człowiek | człowiek (<who>, <via>) | judge | coordinator
    - **Data:** 2026-11-17 14:30
    - **Zastępuje:** D-004 (reason)     (optional; this entry reverses D-004; ids after a `(` are ignored)

Rules: only `## D-<n>` headers are entries (ids are normalised to three digits, so `D-7` is `D-007`), and a block
runs to the next `## ` header. Fields are `- **Name:** value` lines inside a block. There is no code-fence
handling (a stray ``` in a free-form answer must not hide later entries); the format example in the template has
no `## D-` header, so it is not an entry. `Kto` starting with `człowiek` means a human decision;
`judge` and `coordinator` are agents; no `Kto` is unknown. A human decision stays active until another HUMAN
entry names it in `Zastępuje`: an agent (or unknown) entry that names it only challenges it (`supersession`), and
the human decision stays active, so an agent cannot retire what a person decided. Any other entry is retired by a
later entry that names it.
"""
import re
from dataclasses import dataclass, field

HEADER_RE = re.compile(r"^##\s+D-(\d+)\b\s*(.*?)\s*$")
FIELD_RE = re.compile(r"^-\s+\*\*\s*([^*:]+?)\s*:?\s*\*\*\s*:?\s*(.*?)\s*$")
ID_RE = re.compile(r"\bD-(\d+)\b", re.IGNORECASE)


@dataclass
class Decision:
    """One `## D-### title` entry of DECISIONS.md.

    Attributes:
        id: Normalised id, e.g. "D-007".
        title: Header text after the id.
        fields: `- **Name:** value` lines of the block, name as written (first occurrence wins).
        by: "human" (`Kto` starts with `człowiek`), "agent" (`judge`, `coordinator`) or "unknown" (no or other `Kto`).
        supersedes: Ids named in `Zastępuje` before its first `(` (the bracket is a free-text reason), normalised.
        text: The whole block, header included, trailing blank lines stripped.
        line: 1-based line number of the header.
    """
    id: str
    title: str
    fields: dict[str, str] = field(default_factory=dict)
    by: str = "unknown"
    supersedes: list[str] = field(default_factory=list)
    text: str = ""
    line: int = 0


def _norm(num: str) -> str:
    return f"D-{int(num):03d}"


def _field(fields: dict[str, str], name: str) -> str:
    """Return the value of the field called `name`, ignoring case, or an empty string."""
    return next((v for k, v in fields.items() if k.lower() == name), "")


def _by(kto: str) -> str:
    kto = kto.strip().lower()
    if kto.startswith("człowiek"):
        return "human"
    if kto.startswith(("judge", "coordinator")):
        return "agent"
    return "unknown"


def _build(lines: list[str], line: int) -> Decision:
    """Build a Decision from the lines of one block; `line` is the 1-based line of its header."""
    head = HEADER_RE.match(lines[0])
    fields: dict[str, str] = {}
    for raw in lines[1:]:
        if m := FIELD_RE.match(raw):
            fields.setdefault(m.group(1), m.group(2))
    return Decision(
        id=_norm(head.group(1)), title=head.group(2), fields=fields, by=_by(_field(fields, "kto")),
        supersedes=[_norm(n) for n in ID_RE.findall(_field(fields, "zastępuje").split("(")[0])],
        text="\n".join(lines).rstrip(), line=line)


def parse(text: str) -> list[Decision]:
    """Parse DECISIONS.md into entries, in file order.

    Args:
        text: Content of `.house/DECISIONS.md`.

    Returns:
        One Decision per `## D-###` header.
    """
    out: list[Decision] = []
    block: list[str] = []
    start = 0

    def flush():
        if block:
            out.append(_build(block, start))

    for n, raw in enumerate(text.splitlines(), 1):
        if raw.startswith("## "):
            flush()
            block, start = ([raw], n) if HEADER_RE.match(raw) else ([], 0)
            continue
        if block:
            block.append(raw)
    flush()
    return out


def supersession(decisions: list[Decision]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Split the `Zastępuje` links into real retirements and agent challenges of human decisions.

    A human entry is retired only by a human entry. When an agent (or unknown) entry names a human entry, the link is
    only a challenge. Every other entry is retired by any entry that names it. An entry never supersedes itself. An id
    counts as human when ANY entry with that id is human, so a later agent `## D-001` cannot make the human D-001
    retirable by an agent.

    Args:
        decisions: Entries from `parse()` (one contest: ids are per contest).

    Returns:
        `(retired, challenged)`: each maps an id to the ids of the entries that name it, in file order, in the first
        case retiring it, in the second only challenging a human decision.
    """
    human = {d.id for d in decisions if d.by == "human"}  # any human entry with the id: a duplicate cannot flip it
    retired: dict[str, list[str]] = {}
    challenged: dict[str, list[str]] = {}
    for d in decisions:
        for gone in d.supersedes:
            if gone == d.id:
                continue
            target = challenged if gone in human and d.by != "human" else retired
            if d.id not in target.setdefault(gone, []):
                target[gone].append(d.id)
    return retired, challenged


def active_human(decisions: list[Decision]) -> list[Decision]:
    """Return the human decisions no human entry has superseded, in file order.

    Args:
        decisions: Entries from `parse()`.

    Returns:
        Decisions with `by == "human"` whose id is not retired (see `supersession`): an agent entry that names one
        does not remove it.
    """
    retired, _ = supersession(decisions)
    return [d for d in decisions if d.by == "human" and d.id not in retired]
