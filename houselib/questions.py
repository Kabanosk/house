"""`.house/QUESTIONS.md`: parse entries, record answers (first one wins), deadlines with defaults (spec §7a).

Used by agents (they add entries), `notify` (sends open questions), `inbox` (records answers from Signal) and
the human owner. Agents only write new entries; answering is done by `answer()` so DECISIONS.md and the blocked
tasks stay consistent. File format (Polish field names; the parser is tolerant of spacing and case):

    ## Q-007 [open] Short title
    - **Poziom:** krytyczny            (only this level gets a default applied at the deadline)
    - **Termin:** 2026-11-14 18:00     (local time; trailing notes allowed)
    - **Domyślnie:** B                 (default taken at the deadline if nobody answers)
    - **Opcje:** A / B / ...
    - **Rekomendacja:** ...
    - **Blokuje:** T012, T013          (tasks set to todo again once the question is answered)
    - **Odpowiedź:** —                 (filled by `answer()`)

Rules: the status in brackets is `open` until answered, then `answered <date>`; the first answer wins and later
ones are refused; every answer also adds a `D-###` entry to DECISIONS.md and a commit of `.house`. Answers are
flattened to one line (see `clean_answer`) so a reply cannot inject new `## Q-`/`## D-` entries.
"""
import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .checks import _next_decision_id
from .fm import parse_frontmatter, set_fields

HEADER_RE = re.compile(r"^##\s+(Q-\d+)\s+\[([^\]]*)\]\s*(.*?)\s*$", re.MULTILINE)
FIELD_RE = re.compile(r"^-\s+\*\*\s*([^*:]+?)\s*:?\s*\*\*\s*:?\s*(.*?)\s*$", re.MULTILINE)
DEFAULTING_LEVEL = "krytyczny"
EMPTY = {"", "—", "-", "–"}


@dataclass
class Question:
    """One `## Q-### [status] title` entry of QUESTIONS.md.

    Attributes:
        id: Question id, e.g. "Q-007".
        status: "open" or the first word of the bracket, lower case (e.g. "answered").
        title: Text after the bracket on the header line.
        level: Lower-case `Poziom` field (e.g. "krytyczny"), or None.
        deadline: `Termin` as a naive local datetime, or None if absent or unparsable.
        default: `Domyślnie` text, or None.
        options: `Opcje` text, or None.
        recommendation: `Rekomendacja` text, or None.
        blocks: Task ids named in `Blokuje` (e.g. ["T012"]).
        answer: `Odpowiedź` text, or None while it is empty or a dash.
        start: Offset of the entry's header in the file text.
        end: Offset where the entry ends (start of the next header or end of text).
    """

    id: str
    status: str
    title: str
    level: str | None = None
    deadline: datetime | None = None
    default: str | None = None
    options: str | None = None
    recommendation: str | None = None
    blocks: list[str] = field(default_factory=list)
    answer: str | None = None
    start: int = 0
    end: int = 0


DATETIME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})")


def _parse_deadline(value: str | None) -> datetime | None:
    """Return the first `YYYY-MM-DD HH:MM` in `value` (local time) or None; notes like "(koniec − 10 min)" are ok."""
    m = DATETIME_RE.search(value or "")
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def clean_answer(text: str) -> str:
    """Flatten untrusted text to one safe line.

    Whitespace (including newlines) is collapsed, leading `#` characters are removed so the text cannot start a
    markdown heading, and the result is cut to 500 characters.

    Args:
        text: Any answer or name; converted with `str()`.

    Returns:
        The cleaned line, or "—" if nothing is left.
    """
    one_line = " ".join(str(text).split())
    return one_line.lstrip("#").strip()[:500] or "—"


def parse(text: str) -> list[Question]:
    """Parse the content of QUESTIONS.md into questions, in file order.

    Args:
        text: Full file content. Entries start at `## Q-<n> [<status>] <title>`; fields are `- **Name:** value`
            lines below the header (names: Poziom, Termin, Domyślnie, Opcje, Rekomendacja, Blokuje, Odpowiedź).

    Returns:
        One `Question` per header; empty when there are none. Missing fields become None / [].

    Raises:
        IndexError: If a header has empty brackets (`[]`).
    """
    heads = list(HEADER_RE.finditer(text))
    out = []
    for n, m in enumerate(heads):
        end = heads[n + 1].start() if n + 1 < len(heads) else len(text)
        body = text[m.end():end]
        fields = {k.strip().lower(): v for k, v in FIELD_RE.findall(body)}
        status = "open" if m.group(2).strip().lower().startswith("open") else m.group(2).split()[0].lower()
        answer = fields.get("odpowiedź")
        out.append(Question(
            id=m.group(1), status=status, title=m.group(3),
            level=(fields.get("poziom") or "").lower() or None,
            deadline=_parse_deadline(fields.get("termin")),
            default=fields.get("domyślnie") or None,
            options=fields.get("opcje") or None,
            recommendation=fields.get("rekomendacja") or None,
            blocks=re.findall(r"T\d{3,}", fields.get("blokuje") or ""),
            answer=None if answer is None or answer.strip() in EMPTY else answer.strip(),
            start=m.start(), end=end))
    return out


def _commit(house: Path, message: str) -> None:
    """Commit only the `.house` folder of the enclosing git repo; do nothing outside a repo or on failure."""
    repo = house.parent
    if subprocess.run(["git", "rev-parse", "--git-dir"], cwd=repo, capture_output=True, check=False).returncode:
        return
    subprocess.run(["git", "add", "-A", "--", ".house"], cwd=repo, capture_output=True, check=False)
    subprocess.run(["git", "commit", "-q", "-m", message, "--", ".house"], cwd=repo, capture_output=True,
                   check=False)


def answer(house: Path, qid: str, text: str, who: str, via: str, today: str | None = None,
           kto: str | None = None) -> str | None:
    """Record the answer to an open question: update QUESTIONS.md, log a D-### decision, unblock tasks, commit.

    The header becomes `[answered <today>]` and the `Odpowiedź` line `<text> (<who>, <via>)`. DECISIONS.md gets a
    new `D-###` entry. Tasks listed in `Blokuje` (`.house/tasks/T###-*.md`) whose status is `blocked` are set to
    `todo`. The change to `.house` is committed (skipped outside a git repo). First answer wins: an already
    answered question is left untouched.

    Args:
        house: The contest's `.house` directory.
        qid: Question id such as "Q-007".
        text: The answer; flattened with `clean_answer`.
        who: Who answered (name or number); flattened with `clean_answer`.
        via: Channel label for the record, e.g. "Signal" or "CLI".
        today: Date `YYYY-MM-DD` for the header. Defaults to the current date.
        kto: Text for the `Kto` field of the decision. Defaults to "człowiek (<who>)".

    Returns:
        The new decision id (e.g. "D-012"), or None if the question does not exist or is not open.

    Raises:
        OSError: If QUESTIONS.md cannot be read or written.
    """
    path = house / "QUESTIONS.md"
    content = path.read_text()
    q = next((q for q in parse(content) if q.id == qid), None)
    if q is None or q.status != "open":
        return None
    today = today or datetime.now().strftime("%Y-%m-%d")
    text, who = clean_answer(text), clean_answer(who)
    section = content[q.start:q.end]
    section = HEADER_RE.sub(lambda m: f"## {m.group(1)} [answered {today}] {m.group(3)}", section, count=1)
    line = f"- **Odpowiedź:** {text} ({who}, {via})"
    odp = re.compile(r"^-\s+\*\*\s*odpowiedź\s*:?\s*\*\*.*$", re.MULTILINE | re.IGNORECASE)
    if odp.search(section):
        section = odp.sub(lambda _: line, section, count=1)
    else:
        section = section.rstrip("\n") + "\n" + line + "\n" + ("\n" if q.end < len(content) else "")
    path.write_text(content[:q.start] + section + content[q.end:])

    decisions = house / "DECISIONS.md"
    dtext = decisions.read_text() if decisions.exists() else "# Dziennik decyzji\n"
    did = _next_decision_id(dtext)
    entry = (f"\n## {did} {qid}: {q.title}\n\n- **Decyzja:** {text}\n"
             f"- **Dlaczego:** odpowiedź na {qid} ({via})\n- **Kto:** {kto or f'człowiek ({who})'}\n"
             f"- **Data:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    decisions.write_text(dtext.rstrip("\n") + "\n" + entry)

    for tid in q.blocks:
        for tpath in (house / "tasks").glob(f"{tid}-*.md"):
            try:
                if parse_frontmatter(tpath.read_text())[0].get("status") == "blocked":
                    tpath.write_text(set_fields(tpath.read_text(), {"status": "todo"}))
            except ValueError:
                continue
    _commit(house, f"chore(house): {qid} answered ({did})")
    return did


def _state(house: Path) -> dict:
    """Load `.house/state.json`; {} if missing or invalid."""
    try:
        return json.loads((house / "state.json").read_text())
    except (OSError, ValueError):
        return {}


def _save_state(house: Path, data: dict) -> None:
    """Write `data` to `.house/state.json` as indented JSON."""
    (house / "state.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def mark_reminded(house: Path, qid: str) -> None:
    """Remember in `.house/state.json` that the half-time reminder for `qid` was sent (idempotent).

    Args:
        house: The contest's `.house` directory.
        qid: Question id such as "Q-007".
    """
    data = _state(house)
    data.setdefault("reminded", [])
    if qid not in data["reminded"]:
        data["reminded"].append(qid)
    _save_state(house, data)


def due(house: Path, now: datetime | None = None) -> tuple[list[Question], list[Question]]:
    """Find questions that need a default applied or a reminder.

    Only open questions with level `krytyczny`, a deadline and a default qualify. A reminder is due when the
    question was sent (`sent_questions` in state.json), has not been reminded yet and half of the time between
    sending and the deadline has passed. A question never sent through Signal gets no reminder.

    Args:
        house: The contest's `.house` directory.
        now: Reference time (local, naive). Defaults to the current time.

    Returns:
        (apply, remind): questions whose deadline has passed (take the default) and questions past half-time
        (remind once). Both are empty when QUESTIONS.md does not exist.
    """
    now = now or datetime.now()
    path = house / "QUESTIONS.md"
    if not path.exists():
        return [], []
    state = _state(house)
    sent, reminded = state.get("sent_questions", {}), set(state.get("reminded", []))
    apply, remind = [], []
    for q in parse(path.read_text()):
        if q.status != "open" or q.level != DEFAULTING_LEVEL or q.deadline is None or not q.default:
            continue
        if now >= q.deadline:
            apply.append(q)
            continue
        asked = _parse_deadline(sent.get(q.id))
        if asked and q.id not in reminded and now >= asked + (q.deadline - asked) / 2:
            remind.append(q)
    return apply, remind
