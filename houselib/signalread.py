"""Reading the Signal store: fold reactions/edits/deletes onto messages, filter, format, track what is new.

Used by `house signal read|unread|show|status`, the session hook and `house inbox` (unread count). Reads only the
store (`signalstore.iter_events`), never signal-cli. Output is plain text for agents and humans (Polish labels).
"""
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

HOUSE_PREFIX = re.compile(r"^🤖 \[[^\]]*\]\s*")


def snippet(text: str | None, width: int) -> str:
    """Short one-line form of a message: without a leading `🤖 [..]`, whitespace collapsed, cut at a word.

    Args:
        text: Message text (None counts as empty).
        width: Maximum length before the ellipsis.

    Returns:
        The text when it fits; otherwise the longest prefix ending at a space within `width + 1` characters (or
        exactly `width` characters when there is no space), trailing `,.;:!?` removed, plus "…".
    """
    t = " ".join(HOUSE_PREFIX.sub("", text or "").split())
    if len(t) <= width:
        return t
    cut = t[:width + 1]
    space = cut.rfind(" ")
    cut = cut[:space] if space > 0 else t[:width]
    return cut.rstrip(",.;:!? ") + "…"


def fmt_size(n: int | None) -> str:
    """Human file size: `500 B`, `84 KB`, `3.0 MB`; `? B` when unknown."""
    if n is None:
        return "? B"
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{round(n / 1024)} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def display_name(ev: dict) -> str:
    """Name shown for an event's author: `🤖 house` for messages house sent, else the sender's name."""
    if ev.get("dir") == "out":
        return "🤖 house"
    return str((ev.get("sender") or {}).get("name") or "?")


def fmt_time(dt: datetime | None, now: datetime) -> str:
    """Local time for display: `HH:MM` today, `wczoraj HH:MM`, else `YYYY-MM-DD HH:MM`; `nigdy` for None."""
    if dt is None:
        return "nigdy"
    dt, now = dt.astimezone(), now.astimezone()
    if dt.date() == now.date():
        return f"{dt:%H:%M}"
    if dt.date() == (now - timedelta(days=1)).date():
        return f"wczoraj {dt:%H:%M}"
    return f"{dt:%Y-%m-%d %H:%M}"


def parse_iso(text) -> datetime | None:
    """Parse an ISO time from the store; None when missing or invalid."""
    try:
        return datetime.fromisoformat(str(text)) if text else None
    except ValueError:
        return None


# --- folding --------------------------------------------------------------------------------------------------

@dataclass
class Msg:
    """One displayable message with its reactions, edits and deletion folded in.

    Attributes:
        ev: The latest stored version of the message event (kind `message` or `other`).
        text: Text after edits; "" when deleted; the label (e.g. `[naklejka]`) for kind `other`.
        edited: An edit by the author was applied.
        deleted: The author deleted it (remote delete).
        reactions: (emoji, sender name) in the order they were given; removals and replaced reactions dropped.
        replies: Timestamps of messages in the same chat that quote this one.
    """

    ev: dict
    text: str
    edited: bool = False
    deleted: bool = False
    reactions: list[tuple[str, str]] = field(default_factory=list)
    replies: list[int] = field(default_factory=list)

    @property
    def ts(self) -> int | None:
        return self.ev.get("ts")

    @property
    def chat(self) -> str:
        return str(self.ev.get("chat"))

    @property
    def time(self) -> datetime | None:
        return parse_iso(self.ev.get("time"))


def _ids(ev: dict) -> set[str]:
    s = ev.get("sender") or {}
    return {str(x) for x in (s.get("uuid"), s.get("number")) if x}


def _target(index: dict, ev: dict) -> "Msg | None":
    """The message a reaction/edit/delete points at: same chat and ts, narrowed by author when ambiguous."""
    t = ev.get("target") or {}
    found = index.get((ev.get("chat"), t.get("ts")), [])
    if len(found) > 1:
        found = [m for m in found if str(t.get("author")) in _ids(m.ev)]
    return found[0] if len(found) == 1 else None


def _expired(ev: dict, now: datetime) -> bool:
    secs = int(ev.get("expires_in") or 0)
    start = parse_iso(ev.get("received_at")) or parse_iso(ev.get("time"))
    return bool(secs and start and start + timedelta(seconds=secs) < now)


def _order(ev: dict) -> tuple:
    t = parse_iso(ev.get("time"))
    return (ev.get("ts") or (int(t.timestamp() * 1000) if t else 0), int(ev.get("seq") or 0))


def build(events, now: datetime) -> list[Msg]:
    """Turn store events into messages ready to show.

    Deduplicates by `key` (later line wins), folds reactions (one per sender, removal cancels), edits by the
    author (latest wins) and deletions onto their target, links quote-replies, hides expired disappearing
    messages, and drops reactions/edits/deletes whose target is not stored. Sorted by Signal time, then seq.

    Args:
        events: Store events (any order), e.g. `signalstore.iter_events(raw=False)`.
        now: Current time (aware), for expiry.
    """
    from .signalstore import latest
    evs = sorted(latest(events), key=_order)
    msgs: list[Msg] = []
    index: dict[tuple, list[Msg]] = {}
    for e in evs:
        if e.get("kind") in ("message", "other") and not _expired(e, now):
            m = Msg(e, str(e.get("label") or "") if e.get("kind") == "other" else str(e.get("text") or ""))
            msgs.append(m)
            index.setdefault((e.get("chat"), e.get("ts")), []).append(m)
    reacts: dict[int, dict[str, tuple[str, str]]] = {}
    for e in evs:
        kind = e.get("kind")
        if kind not in ("reaction", "edit", "delete"):
            continue
        m = _target(index, e)
        if m is None:
            continue
        if kind == "reaction":
            r = e.get("reaction") or {}
            who = next(iter(sorted(_ids(e))), "?")
            mine = reacts.setdefault(id(m), {})
            if r.get("remove"):
                if who in mine and mine[who][0] == r.get("emoji"):
                    del mine[who]
            else:
                mine.pop(who, None)
                mine[who] = (str(r.get("emoji") or ""), str((e.get("sender") or {}).get("name") or "?"))
        elif not _ids(e) & _ids(m.ev):
            continue  # only the author edits or deletes a message
        elif kind == "edit":
            m.text, m.edited = str(e.get("text") or ""), True
        else:
            m.text, m.deleted = "", True
    for m in msgs:
        m.reactions = list(reacts.get(id(m), {}).values())
        q = m.ev.get("quote") or {}
        for target in index.get((m.ev.get("chat"), q.get("ts")), []) if q.get("ts") is not None else []:
            target.replies.append(m.ts)
    return msgs


# --- filters --------------------------------------------------------------------------------------------------

WHEN_RE = re.compile(r"^(\d+)([mhd])$")


def parse_when(text: str, now: datetime) -> datetime:
    """Parse `--since`/`--until`: `30m`, `2h`, `1d`, `10:00` (today, or yesterday if still ahead), a date, or
    `YYYY-MM-DDTHH:MM` / `YYYY-MM-DD HH:MM`, in local time.

    Raises:
        ValueError: With a Polish hint for anything else.
    """
    t = (text or "").strip()
    m = WHEN_RE.match(t)
    if m:
        return now - timedelta(**{{"m": "minutes", "h": "hours", "d": "days"}[m[2]]: int(m[1])})
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", t)
    if m and int(m[1]) < 24 and int(m[2]) < 60:
        local = now.astimezone()
        dt = local.replace(hour=int(m[1]), minute=int(m[2]), second=0, microsecond=0)
        return dt - timedelta(days=1) if dt > local else dt
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}([T ]\d{1,2}:\d{2}(:\d{2})?)?", t):
        return datetime.fromisoformat(t.replace(" ", "T")).astimezone()
    raise ValueError(f"nie rozumiem czasu `{text}` (np. 30m, 2h, 1d, 10:00, 2026-11-16, 2026-11-16T10:00)")


def _matches_sender(m: Msg, who: str) -> bool:
    if who.lower() == "me":
        return m.ev.get("dir") in ("own", "out")
    s = m.ev.get("sender") or {}
    return who.lower() in str(s.get("name") or "").lower() or who in str(s.get("number") or "")


def _thread(msgs: list[Msg], ts: int) -> list[Msg]:
    """The message `ts`, the chain of messages it quotes (upwards) and every reply below it, in any depth."""
    by_key = {(m.chat, m.ts): m for m in msgs}
    keep = {id(m) for m in msgs if m.ts == ts}
    for m in [m for m in msgs if m.ts == ts]:
        cur = m
        while (cur.ev.get("quote") or {}).get("ts") is not None:
            cur = by_key.get((cur.chat, cur.ev["quote"]["ts"]))
            if cur is None or id(cur) in keep:
                break
            keep.add(id(cur))
        todo = [m]
        while todo:
            node = todo.pop()
            for r in node.replies:
                child = by_key.get((node.chat, r))
                if child is not None and id(child) not in keep:
                    keep.add(id(child))
                    todo.append(child)
    return [m for m in msgs if id(m) in keep]


def select(msgs: list[Msg], *, chat: str | None = None, since: datetime | None = None,
           until: datetime | None = None, sender: str | None = None, grep: str | None = None,
           thread: int | None = None, n: int = 20) -> list[Msg]:
    """Filter messages (already sorted by `build`) and keep the last `n`.

    Args:
        msgs: Output of `build`.
        chat: Chat id to keep; None keeps all.
        since: Keep messages at or after this time. until: at or before.
        sender: "me" (own and house's messages), else a case-insensitive part of the name or part of the number.
        grep: Case-insensitive text to find in the message or in the text it quotes.
        thread: Keep only the thread around this timestamp (`_thread`).
        n: How many of the newest to keep; 0 keeps all. Defaults to 20.
    """
    out = [m for m in msgs if chat is None or m.chat == chat]
    if thread is not None:
        out = _thread(out, thread)
    if since is not None:
        out = [m for m in out if m.time and m.time >= since]
    if until is not None:
        out = [m for m in out if m.time and m.time <= until]
    if sender:
        out = [m for m in out if _matches_sender(m, sender)]
    if grep:
        g = grep.lower()
        out = [m for m in out if g in m.text.lower() or g in str((m.ev.get("quote") or {}).get("text") or "").lower()]
    return out[-n:] if n else out


# --- rendering ------------------------------------------------------------------------------------------------

INDENT = " " * 8
UNKNOWN_QUOTE = "(treść cytatu nieznana, odebrano przed migracją)"


class View:
    """All stored messages folded once, with lookups needed to render quotes and names.

    Attributes:
        events: The store events it was built from.
        msgs: `build(events, now)`.
        now: Reference time for relative dates and expiry.
    """

    def __init__(self, events, now: datetime, aliases: dict[str, str] | None = None):
        """Fold `events`; `aliases` maps chat id → alias for display (defaults to each event's `chat_alias`)."""
        self.events = list(events)
        self.now = now
        self.msgs = build(self.events, now)
        self.aliases = aliases or {}
        self._by_key = {(m.chat, m.ts): m for m in self.msgs}
        self._names: dict[str, str] = {}
        for e in self.events:
            if e.get("kind") in ("message", "other") and e.get("dir") != "out":
                for i in _ids(e):
                    self._names[i] = str((e.get("sender") or {}).get("name") or i)

    def alias(self, m: Msg) -> str:
        return self.aliases.get(m.chat) or str(m.ev.get("chat_alias") or m.chat)

    def find(self, ts: int) -> list[Msg]:
        return [m for m in self.msgs if m.ts == ts]

    def _quoted(self, m: Msg) -> tuple[Msg | None, str, str | None]:
        """(quoted message if stored, author name, quoted text or None)."""
        q = m.ev.get("quote") or {}
        target = self._by_key.get((m.chat, q.get("ts")))
        if target is not None:
            return target, display_name(target.ev), q.get("text") or target.text
        author = q.get("author")
        return None, self._names.get(str(author), str(author or "?")), q.get("text")

    def quote_ref(self, m: Msg, width: int = 28, with_time: bool = True) -> str:
        """`↩ <name> [HH:MM] „<snippet>”` for a quote-reply, "" when the message quotes nothing."""
        if not m.ev.get("quote"):
            return ""
        target, name, text = self._quoted(m)
        when = f" {fmt_time(target.time, self.now)}" if with_time and target is not None and target.time else ""
        body = snippet(text, width) if text else UNKNOWN_QUOTE
        return f" ↩ {name}{when} „{body}”"

    def _body(self, m: Msg) -> str:
        if m.deleted:
            return "[usunięta przez autora]"
        return m.text + (" (edytowano)" if m.edited else "")

    @staticmethod
    def _files_inline(m: Msg) -> str:
        parts = []
        for a in m.ev.get("attachments") or []:
            size = fmt_size(a.get("size")) if a.get("path") else "nie pobrano"
            parts.append(f"[📎 {a.get('filename') or a.get('id') or 'plik'}, {size}]")
        return " ".join(parts)

    def _reactions(self, m: Msg) -> str:
        grouped: dict[str, list[str]] = {}
        for emoji, name in m.reactions:
            grouped.setdefault(emoji, []).append(name)
        return " · ".join(f"{emoji} {', '.join(names)}" for emoji, names in grouped.items())

    def line(self, m: Msg, all_chats: bool = False) -> list[str]:
        """Lines of one message in `read` (text verbatim, continuation lines indented by 8 spaces)."""
        head = f"[{fmt_time(m.time, self.now)}] " + (f"{self.alias(m)} " if all_chats else "")
        head += ("✗ nie wysłano " if m.ev.get("status") == "failed" else f"{m.ts} ") + display_name(m.ev)
        body = " ".join(x for x in (self._body(m), self._files_inline(m)) if x).split("\n")
        if m.ev.get("quote"):
            lines = [f"{head}{self.quote_ref(m)}:", *(INDENT + x for x in body)]
        else:
            lines = [f"{head}: {body[0]}", *(INDENT + x for x in body[1:])]
        lines += [f"{INDENT}📎 {a['path']}" for a in m.ev.get("attachments") or [] if a.get("path")]
        if m.reactions:
            lines.append(INDENT + self._reactions(m))
        return lines

    def render_read(self, selected: list[Msg], *, title: tuple[str, str] | None, total: int,
                    last_pull: datetime | None, note: str | None = None) -> str:
        """Text of `house signal read`.

        Args:
            selected: Messages to show (output of `select`).
            title: (alias, chat name) for one chat; None for all chats (each line then names its chat).
            total: Number of messages in the chat(s) before filtering.
            last_pull: Time of the last successful receive (`state.last_pull_ok`), None if never.
            note: Why receiving was skipped or failed, shown in the header.
        """
        head = [*(title or ("wszystkie",)), f"{len(selected)} z {total}",
                f"odebrano {fmt_time(last_pull, self.now)}"]
        if note:
            head.append(note)
        lines = [" · ".join(head)]
        for m in selected:
            lines += self.line(m, all_chats=title is None)
        return "\n".join(lines) + "\n"

    def render_show(self, m: Msg) -> str:
        """Text of `house signal show`: who, where, when, quoted text in full, body, files, reactions, replies."""
        t, r = m.time, parse_iso(m.ev.get("received_at"))
        when = f"{t:%Y-%m-%d %H:%M:%S}" if t else "?"
        head = [display_name(m.ev), self.alias(m), when]
        if r:
            head.append(f"odebrano {r:%H:%M:%S}" if t and r.date() == t.date() else f"odebrano {r:%Y-%m-%d %H:%M:%S}")
        if m.ev.get("status") == "failed":
            head.append("✗ nie wysłano")
        lines = [" · ".join(head)]
        q = m.ev.get("quote")
        if q:
            target, name, text = self._quoted(m)
            meta = f"{name}, {fmt_time(target.time, self.now)}" if target is not None and target.time else name
            lines.append(f"↩ cytuje {q.get('ts')} ({meta}): „{snippet(text, 500) if text else UNKNOWN_QUOTE}”")
        lines += self._body(m).split("\n")
        for a in m.ev.get("attachments") or []:
            name = a.get("filename") or a.get("id") or "plik"
            lines.append(f"📎 {name}, {fmt_size(a.get('size'))} → {a['path']}" if a.get("path")
                         else f"📎 {name}, nie pobrano")
        tail = []
        if m.reactions:
            tail.append(f"reakcje: {self._reactions(m)}")
        if m.replies:
            tail.append("odpowiedzi cytatem: " + ", ".join(str(x) for x in m.replies))
        if tail:
            lines.append(" · ".join(tail))
        return "\n".join(lines) + "\n"

    def as_json(self, m: Msg) -> dict:
        """Store event without `raw`, with the folded text, flags and reactions (for `--json`)."""
        out = {k: v for k, v in m.ev.items() if k != "raw"}
        out.update(text=m.text, edited=m.edited, deleted=m.deleted, reactions=[list(r) for r in m.reactions],
                   replies=m.replies)
        return out


def maybe_pull(offline: bool, now: datetime | None = None) -> str | None:
    """Quick receive before reading (1 s), unless `offline`, Signal is off, or the last pull was < 60 s ago.

    Returns:
        A header note when receiving was skipped or failed ("Signal wyłączony, pokazuję zapisane",
        "inny odbiór trwa", "błąd odbioru: …"); None otherwise. Never raises for Signal problems.
    """
    if offline:
        return None
    from . import signal, signalstore
    sig = signal.from_config()
    if sig is None:
        return "Signal wyłączony, pokazuję zapisane"
    now = now or datetime.now().astimezone()
    last = parse_iso(signalstore.read_state().get("last_pull_ok"))
    if last and now - last < timedelta(seconds=60):
        return None
    res = signalstore.pull(sig, timeout=1)
    if res.skipped:
        return res.skipped
    return f"błąd odbioru: {res.error}" if res.error else None


# --- what is new for whom -------------------------------------------------------------------------------------

CURSORS = "cursors.json"
NEW_READER_WINDOW = timedelta(hours=24)


def nowe(n: int) -> str:
    """`1 nowa`, `2 nowe`, `5 nowych` (Polish plural of „nowa wiadomość”)."""
    if n == 1:
        return "1 nowa"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} nowe"
    return f"{n} nowych"


def reader_name(cwd: Path) -> str:
    """Whose cursor a session uses: the contest name in a contest repo (or its worktrees), else the name of the git
    main checkout (so every worktree of `house` reads as "house"), else the directory name."""
    from . import guard, notify
    contest = guard.contest_root(Path(cwd))
    if contest is not None:
        return notify.contest_name(contest)
    try:
        r = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=cwd,
                           capture_output=True, text=True, check=False, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        r = None
    if r is not None and r.returncode == 0 and r.stdout.strip():
        return Path(r.stdout.strip()).parent.name
    return Path(cwd).resolve().name


def _cursor(cursors: dict, name: str, events: list[dict], now: datetime) -> dict:
    """The reader's cursor `{"start", "at", "chats": {chat id: seq}}`; a new one starts 24 h back."""
    cur = cursors.get(name)
    if isinstance(cur, dict) and "start" in cur:
        return cur
    since = now - NEW_READER_WINDOW
    start = max((int(e.get("seq") or 0) for e in events
                 if (parse_iso(e.get("time")) or now) < since), default=0)
    return {"start": start, "at": since.isoformat(timespec="seconds"), "chats": {}}


def _new(name: str, now: datetime, chat: str | None) -> tuple[list[Msg], dict, dict, list[dict]]:
    from . import signalchats, signalstore
    events = list(signalstore.iter_events(raw=False))
    cursors = signalstore.read_json(signalstore.store_dir() / CURSORS, {})
    cursors = cursors if isinstance(cursors, dict) else {}
    cur = _cursor(cursors, name, events, now)
    allowed = signalchats.allowed(None)
    msgs = [m for m in build(events, now)
            if m.ev.get("dir") in ("in", "own") and m.chat in allowed and (chat is None or m.chat == chat)
            and int(m.ev.get("seq") or 0) > int((cur.get("chats") or {}).get(m.chat, cur["start"]))]
    return msgs, cur, cursors, events


def unread(name: str, *, chat: str | None = None, peek: bool = False,
           now: datetime | None = None) -> tuple[list[Msg], datetime | None]:
    """Messages from others and the owner (not house's own) that arrived after the reader's cursor.

    Args:
        name: Reader (see `reader_name`; `--as` overrides).
        chat: Only this chat id; the cursors of other chats stay where they are.
        peek: Do not move the cursor.
        now: Reference time. Defaults to now.

    Returns:
        (messages sorted by time, time of the previous read or the 24 h start of a new reader).
    """
    from . import signalstore
    now = now or datetime.now().astimezone()
    msgs, cur, _, _ = _new(name, now, chat)
    since = parse_iso(cur.get("at"))
    if not peek:
        with signalstore.lock("store.lock", blocking=True):
            cursors = signalstore.read_json(signalstore.store_dir() / CURSORS, {})
            cursors = cursors if isinstance(cursors, dict) else {}
            fresh = cursors[name] if isinstance(cursors.get(name), dict) and "start" in cursors[name] else cur
            top = signalstore.last_seq()
            if chat is None:
                fresh = {"start": top, "chats": {}}
            else:
                fresh = {**fresh, "chats": {**(fresh.get("chats") or {}), chat: top}}
            fresh["at"] = now.isoformat(timespec="seconds")
            cursors[name] = fresh
            signalstore.write_json(signalstore.store_dir() / CURSORS, cursors)
    return msgs, since


def unread_counts(name: str, now: datetime | None = None) -> dict[str, int]:
    """Alias → number of unread messages for the reader, without moving any cursor (hook, inbox)."""
    msgs, _, _, _ = _new(name, now or datetime.now().astimezone(), None)
    out: dict[str, int] = {}
    for m in msgs:
        alias = str(m.ev.get("chat_alias") or m.chat)
        out[alias] = out.get(alias, 0) + 1
    return out


def compact_line(view: View, m: Msg, width: int = 16) -> str:
    """One-line form for `unread`: `[HH:MM] ts name[ ↩ quoted „…”]: text [📎 …]` (newlines shown as ⏎)."""
    body = " ".join(x for x in (view._body(m), view._files_inline(m)) if x).replace("\n", " ⏎ ")
    return (f"[{fmt_time(m.time, view.now)}] {m.ts} {display_name(m.ev)}"
            f"{view.quote_ref(m, width=width, with_time=False)}: {body}")


def render_unread(view: View, msgs: list[Msg], since: datetime | None, name: str, peek: bool) -> str:
    """Text of `house signal unread`: count, per-chat groups of one-line messages, and whether they were marked."""
    when = f"{since.astimezone():%Y-%m-%d %H:%M}" if since else "początku"
    if not msgs:
        return f"brak nowych od {when} (dla: {name})\n"
    lines = [f"{nowe(len(msgs))} od {when} (dla: {name})"]
    groups: dict[str, list[Msg]] = {}
    for m in msgs:
        groups.setdefault(view.alias(m), []).append(m)
    for alias, items in sorted(groups.items(), key=lambda kv: -max(_order(m.ev)[0] for m in kv[1])):
        lines.append(f"{alias} · {len(items)}")
        lines += [f"  {compact_line(view, m)}" for m in items]
    lines.append("(podgląd: nic nie oznaczono jako przeczytane)" if peek
                 else "(oznaczone jako przeczytane; `--peek` nie oznacza)")
    return "\n".join(lines) + "\n"
