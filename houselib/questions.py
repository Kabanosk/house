"""`.house/QUESTIONS.md`: parse entries, record answers (first one wins), deadlines with defaults (spec §7a)."""
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
    """First `YYYY-MM-DD HH:MM` in the value (local time); trailing notes like "(koniec − 10 min)" are fine."""
    m = DATETIME_RE.search(value or "")
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def clean_answer(text: str) -> str:
    """One line, no markdown structure: a reply must not be able to add `## Q-…` / `## D-…` entries."""
    one_line = " ".join(str(text).split())
    return one_line.lstrip("#").strip()[:500] or "—"


def parse(text: str) -> list[Question]:
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
    repo = house.parent
    if subprocess.run(["git", "rev-parse", "--git-dir"], cwd=repo, capture_output=True, check=False).returncode:
        return
    subprocess.run(["git", "add", "-A", "--", ".house"], cwd=repo, capture_output=True, check=False)
    subprocess.run(["git", "commit", "-q", "-m", message, "--", ".house"], cwd=repo, capture_output=True,
                   check=False)


def answer(house: Path, qid: str, text: str, who: str, via: str, today: str | None = None,
           kto: str | None = None) -> str | None:
    """Record the answer to an open question: QUESTIONS.md, D-###, unblock tasks, commit. None if not open."""
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
    try:
        return json.loads((house / "state.json").read_text())
    except (OSError, ValueError):
        return {}


def _save_state(house: Path, data: dict) -> None:
    (house / "state.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def mark_reminded(house: Path, qid: str) -> None:
    data = _state(house)
    data.setdefault("reminded", [])
    if qid not in data["reminded"]:
        data["reminded"].append(qid)
    _save_state(house, data)


def due(house: Path, now: datetime | None = None) -> tuple[list[Question], list[Question]]:
    """(critical questions whose deadline passed → apply the default, questions past half-time → remind once)."""
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
