"""Answers from Signal: quote-replies (or `Q-007: B`, `fr Q-007: B`) in Note to Self or a contest's group.

`house inbox` is one reader of the message store (`signalstore`): it receives through `signalstore.pull` (every
allowed chat, also when no contest is running, so the linked device stays active) and then applies answers from
the stored events. The first answer (earliest Signal timestamp) wins. A message meant for a contest outside the
current scope stays unhandled until a run that includes that contest.

Used by `house inbox [contest]` (all running contests, or one), by the session-start hook (scope = the current
contest), `house night` and `house deadlines` (default answers for critical questions). Agents never need to call
it; they find answers in QUESTIONS.md / DECISIONS.md. Rules:

* Answer forms: quote-reply to a message house sent (its `out` event's `meta` names contest and question), or
  typed `[alias] Q-007: answer` (colon required; `Q7` and `Q-007` are equal; alias needed only when several
  running contests have that question open). A reply without a match is ignored as ordinary chat.
* Only Note to Self and groups of running contests can answer; groups allowed through `signal.toml` are stored
  and readable but never routed as answers.
* First answer wins: messages are handled in order of Signal timestamp and only an `open` question accepts an
  answer; later answers get a "już rozstrzygnięte" reply. An edit of a not yet handled answer replaces its text;
  an edit after it was recorded changes nothing (it is logged).
* Running contest = spec `timeline.end` empty, unparsable or in the future (a bare date ends at end of that day).
* Files: `signal/inbox_done.json` in the data dir (handled event key → "<contest> Q-###" or null) and
  `inbox.lock` (one `house inbox` at a time).
"""
import fcntl
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from . import evalmode, notify, questions, registry
from . import spec as specmod

# Typed answers need the colon (`Q-007: B`, `fr Q7: B`): ordinary group chat ("q1 wyniki ok") is not an answer.
ANSWER_RE = re.compile(r"^\s*(?:(?P<alias>[A-Za-z0-9][A-Za-z0-9-]*)\s+)?Q-?0*(?P<num>\d{1,4})\s*:\s*"
                       r"(?P<answer>.+?)\s*$", re.IGNORECASE | re.DOTALL)
SKIPPED_LOCKED = "another `house inbox` is running; skipped"
HEARTS = set("❤❣💔💓💕💖💗💘💙💚💛💜💝💞💟🖤🤍🤎🧡🩵🩶🩷")
SKIN_TONES = range(0x1F3FB, 0x1F400)


def reaction_answer(emoji: str) -> str | None:
    """Meaning of a reaction under a question: "yes" (👍), "no" (👎), "default" (any heart), else None.

    Variation selectors (U+FE0F), zero-width joiners and skin tones are ignored, so 👍🏽 is 👍 and ❤️‍🔥 is a heart.
    """
    base = "".join(ch for ch in emoji or "" if ch not in "\ufe0f\u200d" and ord(ch) not in SKIN_TONES)
    if any(ch in HEARTS for ch in base):
        return "default"
    return {"👍": "yes", "👎": "no"}.get(base)


@dataclass
class Message:
    """A stored Signal message as seen by the answer router.

    Attributes:
        ts: Signal timestamp in milliseconds; unique id of the message and the ordering key (earliest wins).
        chat: "self" for Note to Self, otherwise the Signal group id.
        sender: Display name or number of the author ("?" if unknown).
        text: Message body.
        quote_ts: Timestamp of the message this one quotes (a quote-reply), else None.
        key: The store key `chat:author:ts` (marks it as handled).
        reaction: The emoji when this is a reaction to a question (`quote_ts` is then the question message).
    """

    ts: int
    chat: str  # "self" or a group id
    sender: str
    text: str
    quote_ts: int | None = None
    key: str = ""
    reaction: str | None = None


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
        sent: Questions house sent, keyed by Signal timestamp: {"contest": name, "question": "Q-###"}.

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


def _reaction_text(contest: Path, qid: str, emoji: str) -> tuple[str | None, str | None]:
    """Answer text for a reaction under question `qid`: (text, None), (None, reply explaining why not) or
    (None, None) for a reaction that means nothing (e.g. 😂) or a question that is gone."""
    meaning = reaction_answer(emoji)
    path = contest / ".house/QUESTIONS.md"
    q = next((q for q in questions.parse(path.read_text()) if q.id == qid), None) if path.exists() else None
    if meaning is None or q is None:
        return None, None
    if meaning == "default":
        return (q.default, None) if q.default else (None, "brak domyślnej, odpowiedz tekstem")
    if q.options:
        return None, "odpowiedz literą albo ❤ = domyślna"
    return ("tak" if meaning == "yes" else "nie"), None


def _reply(sig, contest: Path | None, chat: str, text: str) -> None:
    """Send `text` back into `chat` ("self" or a group id), prefixed for the contest or `🤖 [house@<host>]` if None."""
    prefix = notify.house_prefix(contest)
    sig.send(f"{prefix} {text}", group_id=None if chat == "self" else chat,
             meta={"origin": "inbox-reply", "contest": notify.contest_name(contest) if contest else None})


def _handle(m: Message, sig, contests, scope, by_group, sent) -> tuple[bool, str | None, str | None]:
    """Route one message and apply it; return (done, log line).

    Args:
        m: Message to handle.
        sig: A `Signal` client, used for confirmations and disambiguation replies.
        contests: Every running contest (for routing).
        scope: Contests in which answers may be applied now.
        by_group: Group id to contests.
        sent: Questions house sent, keyed by Signal timestamp.

    Returns:
        (done, log line, answered): done is False only when the answer belongs to a contest outside `scope`
        (kept for a later run); the log line is None when there is nothing to report; answered is
        "<contest> Q-###" when this message was recorded as the answer, else None.
    """
    routed = route(m, contests, by_group, sent)
    if routed is None:
        return True, None, None
    if isinstance(routed, str):
        _reply(sig, None, m.chat, routed)
        return True, f"asked to disambiguate a {m.chat} message", None
    contest, qid, text = routed
    if contest not in scope:
        return False, None, None
    if m.reaction is not None:
        text, why = _reaction_text(contest, qid, m.reaction)
        if why:
            _reply(sig, contest, m.chat, f"{qid}: {why}")
            return True, f"{notify.contest_name(contest)} {qid}: reakcja {m.reaction} bez odpowiedzi ({why})", None
        if text is None:
            return True, None, None
    did = questions.answer(contest / ".house", qid, text, m.sender, "Signal")
    if did:
        _reply(sig, contest, m.chat, f"✓ {qid}: {questions.clean_answer(text)} ({m.sender})")
        return True, f"{notify.contest_name(contest)} {qid} answered by {m.sender} → {did}", \
            f"{notify.contest_name(contest)} {qid}"
    path = contest / ".house/QUESTIONS.md"
    q = next((q for q in questions.parse(path.read_text()) if q.id == qid), None) if path.exists() else None
    if q is not None:
        _reply(sig, contest, m.chat, f"{qid} już rozstrzygnięte: {q.answer}")
    return True, None, None


def _author_prefix(key: str) -> str:
    """`chat:author` part of a store key (`chat:author:ts`)."""
    return key.rsplit(":", 1)[0]


def _pending(events: list[dict], chats: set[str], done: dict) -> tuple[list[Message], list[dict]]:
    """Unhandled messages and edits from the chats that can answer, oldest first."""
    from .signalstore import latest
    msgs, edits = [], []
    for e in sorted(latest(events), key=lambda e: e.get("ts") or 0):
        if e.get("chat") not in chats or e.get("dir") not in ("in", "own") or e.get("key") in done:
            continue
        if e.get("kind") == "message":
            msgs.append(Message(int(e["ts"]), str(e["chat"]), str((e.get("sender") or {}).get("name") or "?"),
                                str(e.get("text") or ""), (e.get("quote") or {}).get("ts"), str(e["key"])))
        elif e.get("kind") == "edit" and (e.get("target") or {}).get("ts") is not None:
            edits.append(e)
    return msgs, edits


def _reactions(events: list[dict], chats: set[str], done: dict) -> tuple[list[Message], list[str]]:
    """Unhandled reactions as answer candidates, and keys of reactions that are settled without an answer.

    Per sender and target only the latest unhandled reaction counts; when it is a removal, the sender's pending
    reactions to that target are cancelled.
    """
    from .signalstore import latest
    last: dict[tuple, list[dict]] = {}
    for e in sorted(latest(events), key=lambda e: e.get("ts") or 0):
        if (e.get("kind") == "reaction" and e.get("chat") in chats and e.get("dir") in ("in", "own")
                and e.get("key") not in done and (e.get("target") or {}).get("ts") is not None):
            last.setdefault((_author_prefix(str(e["key"])), e["target"]["ts"]), []).append(e)
    out, settled = [], []
    for (_, target), items in last.items():
        final = items[-1]
        settled += [str(e["key"]) for e in items[:-1]]
        if (final.get("reaction") or {}).get("remove"):
            settled.append(str(final["key"]))
            continue
        out.append(Message(int(final["ts"]), str(final["chat"]), str((final.get("sender") or {}).get("name") or "?"),
                           "", int(target), str(final["key"]), str((final.get("reaction") or {}).get("emoji") or "")))
    return out, settled


def process(contests: list[Path], sig, scope: list[Path] | None = None, *, timeout: int = 1,
            max_messages: int | None = None, reader: str | None = None) -> list[str]:
    """Receive Signal into the store and apply the answers found there; the entry point of `house inbox`.

    Takes an exclusive lock (`inbox.lock`) so only one run works at a time. Receives even when no contest is
    running. Unhandled messages from earlier runs are processed too, in timestamp order; each handled event is
    recorded in `signal/inbox_done.json`. A failure on one message is logged and skipped, never raised.

    Args:
        contests: Every contest that may receive answers; routing and the group filter need all of them. Ended
            contests are dropped here.
        sig: A `Signal` client, or None for "Signal not configured" (nothing happens).
        scope: Contests in which answers are applied now. Defaults to None, meaning all of `contests`. A message
            for a running contest outside `scope` stays unhandled for a later run that includes it.
        timeout: Receive timeout in seconds. Defaults to 1.
        max_messages: Receive at most this many (the hook uses 200). Defaults to None.
        reader: When given, a last line tells how many group messages are unread for this reader.

    Returns:
        Human-readable log lines (answers applied, disambiguation asked, edits after recording, migration).
        Empty when there is nothing to report or `sig` is None; `[SKIPPED_LOCKED]` if another run holds the lock.
        Always empty in eval mode (`HOUSE_EVAL=1`): nothing is received or applied, one `inbox.process` line goes
        to the eval log.
    """
    if evalmode.active():
        evalmode.log("inbox.process", contests=len(list(contests)))
        return []
    from . import signalchats, signalstore
    contests = running(contests)
    if sig is None:
        return []
    scope_set = {Path(c).resolve() for c in (scope if scope is not None else contests)}
    lock = (notify.data_dir() / "inbox.lock").open("w")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return [SKIPPED_LOCKED]
        log = list(signalstore.ensure_migrated(inbox_locked=True))
        chats = signalchats.allowed(sig, contests)
        signalstore.pull(sig, chats, timeout=timeout, max_messages=max_messages)
        by_group = {gid: list(c.contests) for gid, c in chats.items() if c.contests}
        done_path = signalstore.store_dir() / "inbox_done.json"
        done = signalstore.read_json(done_path, {})
        done = done if isinstance(done, dict) else {}
        events = list(signalstore.iter_events(raw=False))
        sent = {int(e["ts"]): {"contest": (e.get("meta") or {}).get("contest"),
                               "question": (e.get("meta") or {}).get("question")}
                for e in events if e.get("dir") == "out" and e.get("ts") and (e.get("meta") or {}).get("question")}
        in_scope = [c for c in contests if Path(c).resolve() in scope_set]
        answering = {signalchats.SELF, *by_group}
        msgs, edits = _pending(events, answering, done)
        by_key = {m.key: m for m in msgs}
        reactions, settled = _reactions(events, answering, done)
        for key in settled:
            done[key] = None
        waiting_edits = []  # edits of originals still pending: marked handled only together with the original
        for e in edits:
            original = f"{_author_prefix(str(e['key']))}:{e['target']['ts']}"
            if original in by_key:
                by_key[original].text = str(e.get("text") or "")
                waiting_edits.append((str(e["key"]), original))
                continue
            if done.get(original):
                log.append(f"{str(done[original]).split()[-1]}: odpowiedź edytowana po zapisaniu, decyzja bez zmian")
            done[str(e["key"])] = None
        for m in sorted([*msgs, *reactions], key=lambda m: m.ts):
            try:
                finished, line, answered = _handle(m, sig, contests, in_scope, by_group, sent)
            except Exception as e:  # noqa: BLE001 — one bad message must never block the inbox
                finished, line, answered = True, f"skipped a message ({type(e).__name__}: {e})", None
            if line:
                log.append(line)
            if finished:
                done[m.key] = answered
        for edit_key, original in waiting_edits:
            if original in done:
                done[edit_key] = None
        signalstore.write_json(done_path, done)
        if reader:
            from . import signalread
            n = sum(c for alias, c in signalread.unread_counts(reader).items() if alias != signalchats.SELF)
            if n:
                log.append(f"signal: {signalread.nowe(n)} w grupach (house signal unread)")
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
                              f"{q.deadline:%H:%M}.", question=q.id, sig=sig, origin="deadline")
        if sent:
            questions.mark_reminded(house, q.id)
            log.append(f"reminded {q.id}")
    for q in apply:
        why = f"domyślnie, brak odpowiedzi do {q.deadline:%H:%M}"
        did = questions.answer(house, q.id, q.default, "judge", why, kto=f"judge ({why})")
        if did:
            if sig is not None:
                notify.send(contest, f"⏱ {q.id}: brak odpowiedzi do {q.deadline:%H:%M}, przyjęto domyślnie "
                            f"{q.default} ({did}). Można cofnąć decyzją człowieka.", sig=sig, origin="deadline")
            log.append(f"default {q.id} → {q.default} ({did})")
    return log
