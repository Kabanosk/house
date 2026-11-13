"""Answers from Signal: quote-replies (or `Q-007: B`, `fr Q-007: B`) in Note to Self or a contest's group.

Only Note to Self and the groups of running contests are kept; every other conversation of the account is
dropped before anything is stored. The first answer (earliest Signal timestamp) wins. Receiving consumes
messages from signal-cli, so everything kept is written to `inbox.jsonl` first and handled from there; a message
meant for a contest outside the current scope stays there until a run that includes that contest.

Used by `house inbox [contest]` (all running contests, or one), by the session-start hook (scope = the current
contest) and `house deadlines` (default answers for critical questions). Agents never need to call it; they find
answers in QUESTIONS.md / DECISIONS.md. Rules:

* Answer forms: quote-reply to a message house sent (the quoted `sent.jsonl` entry names contest and question),
  or typed `[alias] Q-007: answer` (colon required; `Q7` and `Q-007` are equal; alias needed only when several
  running contests have that question open). A reply without a match is ignored as ordinary chat.
* First answer wins: messages are handled in order of Signal timestamp and only an `open` question accepts an
  answer; later answers get a "już rozstrzygnięte" reply.
* Privacy filter: only Note to Self and groups of running contests are stored; other chats are dropped.
* Running contest = spec `timeline.end` empty, unparsable or in the future (a bare date ends at end of that day).
* Files in the data dir (~/.local/share/house, or $HOUSE_DATA_DIR): `inbox.jsonl` (kept messages),
  `inbox_done.json` (handled timestamps), `inbox.lock` (one `house inbox` at a time), `sent.jsonl` (see notify).
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
    """A kept Signal message, as stored in `inbox.jsonl`.

    Attributes:
        ts: Signal timestamp in milliseconds; unique id of the message and the ordering key (earliest wins).
        chat: "self" for Note to Self, otherwise the Signal group id.
        sender: Display name or number of the author ("?" if unknown).
        text: Message body.
        quote_ts: Timestamp of the message this one quotes (a quote-reply), else None.
    """

    ts: int
    chat: str  # "self" or a group id
    sender: str
    text: str
    quote_ts: int | None = None


def _end_of(value) -> datetime | None:
    """Convert a spec timeline.end value to a local naive datetime (bare date = end of day); None if unusable."""
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
    """Filter contests down to those still running.

    Args:
        contests: Contest repository paths; each is read through its `.house` spec.
        now: Reference time (local, naive). Defaults to the current time.

    Returns:
        The contests whose spec `timeline.end` is empty, unparsable or not yet passed, in input order.
    """
    now = now or datetime.now()
    out = []
    for c in contests:
        end = _end_of(specmod.get(specmod.load_spec(c / ".house"), "timeline.end"))
        if end is None or end > now:
            out.append(c)
    return out


def all_running() -> list[Path]:
    """Return every running contest from the registry (`registry.contests()`)."""
    return running(registry.contests())


def extract(obj: dict, own: str, groups: set[str]) -> Message | None:
    """Turn one signal-cli JSON object into a `Message`, applying the privacy filter.

    Kept: text messages of the groups in `groups`, and messages the owner sent to their own number (Note to Self,
    seen as a sync message from the linked device). Private conversations and other groups yield None. A sync
    message is preferred over a data message when both carry text.

    Args:
        obj: One parsed line of `signal-cli -o json receive` (with an "envelope" key).
        own: The account's own number (`Signal.account`), used to recognise Note to Self.
        groups: Ids of the groups to keep (those of the running contests).

    Returns:
        A `Message`, or None when the object is not a kept text message.

    Raises:
        TypeError, ValueError, AttributeError: On malformed field types; `pull` catches and skips those.
    """
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
    """Read a JSON-lines file of the data dir, skipping unparsable lines; [] if it does not exist."""
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
    """Map Signal group id to the contests using it (spec `notify.signal_group`); unresolved groups are omitted."""
    by_group: dict[str, list[Path]] = {}
    for c in contests:
        name = (specmod.get(specmod.load_spec(c / ".house"), "notify") or {}).get("signal_group")
        if name:
            gid, _ = notify.resolve_group(sig, str(name))
            if gid:
                by_group.setdefault(gid, []).append(c)
    return by_group


def pull(sig, contests: list[Path]) -> list[Message]:
    """Receive new Signal messages and append the ones that pass the privacy filter to `inbox.jsonl`.

    Receiving consumes messages in signal-cli, so call this only when the result gets persisted (it does that).

    Args:
        sig: A `Signal` client.
        contests: Every running contest; their groups define which group chats are kept.

    Returns:
        The messages kept in this call (not those already in the file).
    """
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
    """Tell whether contest `c` has question `qid` with status open."""
    path = c / ".house/QUESTIONS.md"
    return path.exists() and any(q.id == qid and q.status == "open" for q in questions.parse(path.read_text()))


def route(m: Message, contests: list[Path], by_group: dict[str, list[Path]], sent: dict[int, dict]):
    """Decide whether a message answers a question, and which one.

    A quote-reply wins over typed text: if `m.quote_ts` is set, only the `sent` record of the quoted message is
    used (a quote of an unknown message is not an answer). Otherwise the text must match `ANSWER_RE`
    (`[alias] Q-007: answer`); the candidates are the contests of the chat (all of them in Note to Self, the
    group's contests in a group), narrowed by the alias when given, that have the question open.

    Args:
        m: The message to classify.
        contests: Every running contest.
        by_group: Group id to contests, as built from spec `notify.signal_group`.
        sent: Records of `sent.jsonl` keyed by Signal timestamp (keys "contest" and "question").

    Returns:
        A tuple (contest path, question id such as "Q-007", answer text) for an unambiguous answer; a str with
        the (Polish) reply to send when the question is open in several contests; None when the message is not
        an answer or no contest has that question open.
    """
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
    """Send `text` back into `chat` ("self" or a group id), prefixed for the contest or `🤖 [house]` if None."""
    prefix = notify.prefix(contest) if contest else "🤖 [house]"
    sig.send(f"{prefix} {text}", group_id=None if chat == "self" else chat)


def _handle(m: Message, sig, contests, scope, by_group, sent) -> tuple[bool, str | None]:
    """Route one message and apply it; return (done, log line).

    Args:
        m: Message to handle.
        sig: A `Signal` client, used for confirmations and disambiguation replies.
        contests: Every running contest (for routing).
        scope: Contests in which answers may be applied now.
        by_group: Group id to contests.
        sent: Records of `sent.jsonl` keyed by timestamp.

    Returns:
        done is False only when the answer belongs to a contest outside `scope` (kept for a later run);
        the log line is None when there is nothing to report.
    """
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
    """Pull Signal and apply the answers found in the inbox; the entry point of `house inbox` and the hook.

    Takes an exclusive lock (`inbox.lock`) so only one run works at a time. Unhandled messages from earlier runs
    are processed too, in timestamp order; each handled message is recorded in `inbox_done.json`. A failure on
    one message is logged and skipped, never raised.

    Args:
        contests: Every contest that may receive answers; routing and the group filter need all of them. Ended
            contests are dropped here.
        sig: A `Signal` client, or None for "Signal not configured" (nothing happens).
        scope: Contests in which answers are applied now. Defaults to None, meaning all of `contests`. A message
            for a running contest outside `scope` stays in the inbox for a later run that includes it.

    Returns:
        Human-readable log lines (answers applied, disambiguation asked, skipped messages). Empty when there is
        nothing to do, `sig` is None or no contest is running; `[SKIPPED_LOCKED]` if another run holds the lock.
    """
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
    """Remind once at half-time and apply the judge's default when a critical question's deadline has passed.

    Only open questions of level `krytyczny` with both a deadline and a default are considered
    (see `questions.due`). A reminder is marked as given only if it was delivered. A default is recorded like
    an answer (by "judge", with a D-### entry) and can be overridden by a human decision.

    Args:
        contest: Path to the contest repository.
        sig: A `Signal` client, or None. With None no message is sent (no reminders, no notice of applied
            defaults) and the machine's Signal config is never consulted, but defaults are still applied.
        now: Reference time (local, naive). Defaults to the current time.

    Returns:
        Log lines such as "reminded Q-007" and "default Q-007 → B (D-012)"; empty when nothing was due.
    """
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
