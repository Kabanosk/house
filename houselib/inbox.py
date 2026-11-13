"""Answers from Signal: quote-replies (or `Q-007: B`, `fr Q-007: B`) in Note to Self or a contest's group.

Only Note to Self and the groups of running contests are kept; every other conversation of the account is
dropped before anything is stored. The first answer (earliest Signal timestamp) wins. Receiving consumes
messages from signal-cli, so everything kept is written to `inbox.jsonl` first and handled from there; a message
meant for a contest outside the current scope stays there until a run that includes that contest.
"""
import fcntl
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from . import notify, questions, registry
from . import spec as specmod

# Typed answers need the colon (`Q-007: B`, `fr Q7: B`): ordinary group chat ("q1 wyniki ok") is not an answer.
ANSWER_RE = re.compile(r"^\s*(?:(?P<alias>[A-Za-z0-9][A-Za-z0-9-]*)\s+)?Q-?0*(?P<num>\d{1,4})\s*:\s*"
                       r"(?P<answer>.+?)\s*$", re.IGNORECASE | re.DOTALL)
SKIPPED_LOCKED = "another `house inbox` is running; skipped"


@dataclass
class Message:
    ts: int
    chat: str  # "self" or a group id
    sender: str
    text: str
    quote_ts: int | None = None


def _end_of(value) -> datetime | None:
    """timeline.end as a local naive datetime; a bare date means the end of that day."""
    if value is None or str(value).strip() in ("", "null"):
        return None
    text = str(value).strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        dt += timedelta(days=1)
    return dt


def running(contests: list[Path], now: datetime | None = None) -> list[Path]:
    """Contests whose timeline.end is empty, unparsable or not yet passed."""
    now = now or datetime.now()
    out = []
    for c in contests:
        end = _end_of(specmod.get(specmod.load_spec(c / ".house"), "timeline.end"))
        if end is None or end > now:
            out.append(c)
    return out


def all_running() -> list[Path]:
    return running(registry.contests())


def extract(obj: dict, own: str, groups: set[str]) -> Message | None:
    env = obj.get("envelope") or {}
    sent = (env.get("syncMessage") or {}).get("sentMessage") or {}
    data = env.get("dataMessage") or {}
    msg = sent if sent.get("message") else data if data.get("message") else None
    if not msg:
        return None
    gid = (msg.get("groupInfo") or {}).get("groupId")
    if gid:
        if gid not in groups:
            return None
        chat = gid
    elif msg is sent and (sent.get("destinationNumber") == own or sent.get("destination") == own):
        chat = "self"
    else:
        return None  # private conversations are none of house's business
    sender = env.get("sourceName") or env.get("sourceNumber") or "?"
    quote = (msg.get("quote") or {}).get("id")
    return Message(int(msg.get("timestamp") or env.get("timestamp") or 0), chat, str(sender), str(msg["message"]),
                   int(quote) if quote is not None and str(quote).isdigit() else None)


def _lines(name: str) -> list[dict]:
    path = notify.data_dir() / name
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _group_ids(sig, contests: list[Path]) -> dict[str, list[Path]]:
    by_group: dict[str, list[Path]] = {}
    for c in contests:
        name = (specmod.get(specmod.load_spec(c / ".house"), "notify") or {}).get("signal_group")
        if name:
            gid, _ = notify.resolve_group(sig, str(name))
            if gid:
                by_group.setdefault(gid, []).append(c)
    return by_group


def pull(sig, contests: list[Path]) -> list[Message]:
    """Receive and keep Note to Self + the groups of `contests` (pass every running contest)."""
    groups = set(_group_ids(sig, contests))
    kept = []
    for obj in sig.receive(timeout=1):
        try:
            m = extract(obj, sig.account, groups)
        except (TypeError, ValueError, AttributeError):
            continue
        if m:
            kept.append(m)
    path = notify.data_dir() / "inbox.jsonl"
    with path.open("a") as f:
        for m in kept:
            f.write(json.dumps(asdict(m), ensure_ascii=False) + "\n")
    path.chmod(0o600)
    return kept


def _open(c: Path, qid: str) -> bool:
    path = c / ".house/QUESTIONS.md"
    return path.exists() and any(q.id == qid and q.status == "open" for q in questions.parse(path.read_text()))


def route(m: Message, contests: list[Path], by_group: dict[str, list[Path]], sent: dict[int, dict]):
    """(contest, qid, answer) | str (reply asking to disambiguate) | None (not an answer)."""
    by_name = {notify.contest_name(c): c for c in contests}
    if m.quote_ts is not None:
        rec = sent.get(m.quote_ts)
        if rec and rec.get("question") and rec.get("contest") in by_name:
            return by_name[rec["contest"]], rec["question"], m.text.strip()
        return None
    match = ANSWER_RE.match(m.text)
    if not match:
        return None
    qid = f"Q-{int(match['num']):03d}"
    pool = contests if m.chat == "self" else by_group.get(m.chat, [])
    if match["alias"] and match["alias"].lower() != "q":
        pool = [c for c in pool if notify.alias(c).lower() == match["alias"].lower()]
    candidates = [c for c in pool if _open(c, qid)]
    if len(candidates) == 1:
        return candidates[0], qid, match["answer"].strip()
    if len(candidates) > 1:
        options = " albo ".join(f"„{notify.alias(c)} {qid}: …”" for c in candidates)
        return f"{qid} jest otwarte w kilku konkursach; napisz {options} albo odpowiedz cytatem."
    return None


def _reply(sig, contest: Path | None, chat: str, text: str) -> None:
    prefix = notify.prefix(contest) if contest else "🤖 [house]"
    sig.send(f"{prefix} {text}", group_id=None if chat == "self" else chat)


def _handle(m: Message, sig, contests, scope, by_group, sent) -> tuple[bool, str | None]:
    """(done, log line). Not done = meant for a contest outside the current scope: keep for a later run."""
    routed = route(m, contests, by_group, sent)
    if routed is None:
        return True, None
    if isinstance(routed, str):
        _reply(sig, None, m.chat, routed)
        return True, f"asked to disambiguate a {m.chat} message"
    contest, qid, text = routed
    if contest not in scope:
        return False, None
    did = questions.answer(contest / ".house", qid, text, m.sender, "Signal")
    if did:
        _reply(sig, contest, m.chat, f"✓ {qid}: {questions.clean_answer(text)} ({m.sender})")
        return True, f"{notify.contest_name(contest)} {qid} answered by {m.sender} → {did}"
    path = contest / ".house/QUESTIONS.md"
    q = next((q for q in questions.parse(path.read_text()) if q.id == qid), None) if path.exists() else None
    if q is not None:
        _reply(sig, contest, m.chat, f"{qid} już rozstrzygnięte: {q.answer}")
    return True, None


def process(contests: list[Path], sig, scope: list[Path] | None = None) -> list[str]:
    """Pull Signal and apply answers. `contests` = every running contest (routing and the group filter need all
    of them); answers are applied only to `scope` (default: all)."""
    contests = running(contests)
    if sig is None or not contests:
        return []
    scope_set = {Path(c).resolve() for c in (scope if scope is not None else contests)}
    lock = (notify.data_dir() / "inbox.lock").open("w")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return [SKIPPED_LOCKED]
        pull(sig, contests)
        done_path = notify.data_dir() / "inbox_done.json"
        try:
            done = set(json.loads(done_path.read_text()))
        except (OSError, ValueError):
            done = set()
        by_group = _group_ids(sig, contests)
        sent = {int(r["ts"]): r for r in _lines("sent.jsonl") if str(r.get("ts", "")).isdigit()}
        in_scope = [c for c in contests if Path(c).resolve() in scope_set]
        log = []
        for raw in sorted(_lines("inbox.jsonl"), key=lambda r: r.get("ts", 0)):
            try:
                m = Message(**raw)
            except TypeError:
                continue
            if m.ts in done:
                continue
            try:
                finished, line = _handle(m, sig, contests, in_scope, by_group, sent)
            except Exception as e:  # noqa: BLE001 — one bad message must never block the inbox
                finished, line = True, f"skipped a message ({type(e).__name__}: {e})"
            if line:
                log.append(line)
            if finished:
                done.add(m.ts)
                done_path.write_text(json.dumps(sorted(done)) + "\n")
        return log
    finally:
        lock.close()


def deadlines(contest: Path, sig, now: datetime | None = None) -> list[str]:
    """Remind once at half-time; at the deadline apply the judge's default for critical questions."""
    house = contest / ".house"
    apply, remind = questions.due(house, now)
    log = []
    for q in remind:
        if sig is None:  # explicit: no Signal given → never fall back to the machine's config here
            continue
        sent, _ = notify.send(contest, f"⏳ {q.id} {q.title} czeka na decyzję; domyślnie {q.default} o "
                              f"{q.deadline:%H:%M}.", question=q.id, sig=sig)
        if sent:
            questions.mark_reminded(house, q.id)
            log.append(f"reminded {q.id}")
    for q in apply:
        why = f"domyślnie, brak odpowiedzi do {q.deadline:%H:%M}"
        did = questions.answer(house, q.id, q.default, "judge", why, kto=f"judge ({why})")
        if did:
            if sig is not None:
                notify.send(contest, f"⏱ {q.id}: brak odpowiedzi do {q.deadline:%H:%M}, przyjęto domyślnie "
                            f"{q.default} ({did}). Można cofnąć decyzją człowieka.", sig=sig)
            log.append(f"default {q.id} → {q.default} ({did})")
    return log
