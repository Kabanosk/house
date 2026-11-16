"""The Signal message store: one append-only file of events from allowed chats, plus state, cursors and locks.

Every receive (`pull`) and every send (`Signal.send` → `record_out`) appends here; every reader (`house signal
read|unread|show`, `house inbox`, the session hook, `night`, `doctor`) reads only from here, never through
`receive`. Format of one event: spec `docs/specs/2026-11-16-signal-groups-design.md` §11 (`v: 1`).

Files in `<data dir>/signal/` (dir 0700, files 0600; data dir = $HOUSE_DATA_DIR or ~/.local/share/house):

* `messages.jsonl` — one event per line, append only (except `compact`); `seq` grows in append order.
* `state.json` — `next_seq`, `last_pull_ok`, `last_pull_error`, `dropped_last_pull`, `bad_lines`, `migrated`, …
* `pending.jsonl` — events written while `store.lock` was unavailable; moved into the store by the next pull.
* `store.lock` — short, blocking lock (≤ LOCK_TIMEOUT s) for one append, seq and state changes.
* `receive.lock` — one receive at a time, never waited for (busy = skip receiving, read what is stored).

Readers take no lock: the file only grows, so a last line without a newline is simply skipped, and so is any
line that is not a JSON object (`bad_lines()` lists them for `house doctor`).
"""
import fcntl
import json
import mimetypes
import os
import re
import shutil
import signal as sigmod
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import notify, signalparse

LOCK_TIMEOUT = 5.0
MESSAGES = "messages.jsonl"
STATE = "state.json"
PENDING = "pending.jsonl"


def store_dir() -> Path:
    """Return the store directory `<data dir>/signal`, creating it with mode 0700."""
    d = notify.data_dir() / "signal"
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)
    return d


@contextmanager
def lock(name: str, blocking: bool, timeout: float | None = None):
    """Hold an exclusive `flock` on `<store dir>/<name>`.

    Args:
        name: Lock file name, e.g. "store.lock" or "receive.lock".
        blocking: False tries once; True retries until `timeout`.
        timeout: Seconds to keep trying when blocking. Defaults to LOCK_TIMEOUT.

    Yields:
        True when the lock is held, False when it could not be taken (the body still runs; check the value).
    """
    path = store_dir() / name
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + (LOCK_TIMEOUT if timeout is None else timeout)
    held = False
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held = True
                break
            except BlockingIOError:
                if not blocking or time.monotonic() >= deadline:
                    break
                time.sleep(0.02)
        yield held
    finally:
        if held:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def write_json(path: Path, obj) -> None:
    """Write JSON atomically (temp file, fsync, rename) with mode 0600."""
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def read_json(path: Path, default):
    """Load a JSON file; `default` when it is missing or unparsable."""
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def read_state() -> dict:
    """Return `state.json` as a dict ({} when missing or broken)."""
    state = read_json(store_dir() / STATE, {})
    return state if isinstance(state, dict) else {}


def update_state(**changes) -> dict:
    """Merge `changes` into `state.json` under `store.lock`; when the lock is unavailable nothing is written.

    Returns:
        The state after the change (or the unchanged state when the lock could not be taken).
    """
    with lock("store.lock", blocking=True) as held:
        state = read_state()
        if held:
            state.update(changes)
            write_json(store_dir() / STATE, state)
        return state


def _line(event: dict) -> bytes:
    return (json.dumps(event, ensure_ascii=False) + "\n").encode()


def _append_bytes(path: Path, data: bytes) -> None:
    """Append bytes with O_APPEND and fsync; start a new line if the file ends inside a partial line."""
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        size = os.fstat(fd).st_size
        if size and os.pread(fd, 1, size - 1) != b"\n":
            data = b"\n" + data
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(path, 0o600)


def _scan_last_seq() -> int:
    """Highest `seq` in the store file (0 when empty); used when `state.json` lost `next_seq`."""
    return max((int(e.get("seq") or 0) for e in iter_events(raw=False)), default=0)


def _tail_seq() -> int:
    """Seq of the last complete line of the store (reads only the file's end); 0 when there is none."""
    path = store_dir() / MESSAGES
    if not path.exists():
        return 0
    with path.open("rb") as f:
        f.seek(max(0, path.stat().st_size - 65536))
        chunk = f.read()
    for e, _ in reversed(list(_parse_lines(chunk))):
        if e is not None and str(e.get("seq", "")).isdigit():
            return int(e["seq"])
    return 0


def _append_locked(event: dict) -> int:
    """Append one event with the next seq; the caller holds `store.lock`.

    `next_seq` is saved before the line is written, so a crash in between leaves a gap, never a reused seq; a
    state that lags behind the file (older crash, restored backup) is corrected from the file's last line.
    """
    state = read_state()
    seq = max(int(state.get("next_seq") or 0), _tail_seq() + 1)
    state["next_seq"] = seq + 1
    write_json(store_dir() / STATE, state)
    rest = {k: v for k, v in event.items() if k not in ("v", "seq", "first_seq")}
    _append_bytes(store_dir() / MESSAGES, _line({"v": event.get("v", 1), "seq": seq, **rest}))
    return seq


def append(event: dict) -> int | None:
    """Append one event to the store and make it durable before returning.

    Args:
        event: Event in store format; any `seq` in it is replaced.

    Returns:
        The assigned seq; None when `store.lock` was not available within LOCK_TIMEOUT (the event then went to
        `pending.jsonl`, which the next `pull` moves into the store).
    """
    with lock("store.lock", blocking=True) as held:
        if held:
            return _append_locked(event)
    _append_bytes(store_dir() / PENDING, _line(event))
    return None


def ingest_pending() -> int:
    """Move complete lines of `pending.jsonl` into the store (fresh seq each) and empty it.

    Returns:
        Number of events moved; 0 when there was nothing or `store.lock` was unavailable.
    """
    path = store_dir() / PENDING
    if not path.exists() or not path.stat().st_size:
        return 0
    with lock("store.lock", blocking=True) as held:
        if not held:
            return 0
        events = list(_parse_lines(path.read_bytes()))
        for e, _ in events:
            if e is not None:
                _append_locked(e)
        fd = os.open(path, os.O_WRONLY | os.O_TRUNC)
        os.close(fd)
        return sum(1 for e, _ in events if e is not None)


def _parse_lines(data: bytes) -> Iterator[tuple[dict | None, int]]:
    """Yield (event or None for a bad line, 1-based line number) for complete lines of a JSON-lines blob."""
    lines = data.split(b"\n")
    for n, raw in enumerate(lines[:-1], start=1):  # the part after the last "\n" is incomplete
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            obj = None
        yield (obj if isinstance(obj, dict) else None), n


def iter_events(raw: bool = True) -> Iterator[dict]:
    """Yield the store's events in file (seq) order, skipping bad lines and an incomplete last line.

    Args:
        raw: False drops the `raw` key (the full signal-cli envelope) from each event. Defaults to True.
    """
    path = store_dir() / MESSAGES
    if not path.exists():
        return
    for e, _ in _parse_lines(path.read_bytes()):
        if e is None:
            continue
        if not raw:
            e.pop("raw", None)
        yield e


def bad_lines() -> list[int]:
    """1-based numbers of complete store lines that are not JSON objects (for `house doctor`)."""
    path = store_dir() / MESSAGES
    return [n for e, n in _parse_lines(path.read_bytes()) if e is None] if path.exists() else []


def latest(events) -> list[dict]:
    """Deduplicate by `key`: the last line of each key wins, in order of that key's first appearance.

    Each returned event also carries `first_seq`, the seq of the key's first line, so a re-delivered or
    re-parsed copy is not mistaken for a new message.
    """
    by_key: dict[str, dict] = {}
    for e in events:
        k = str(e.get("key"))
        prev = by_key.get(k)
        by_key[k] = {**e, "first_seq": prev["first_seq"] if prev else e.get("seq")}
    return list(by_key.values())


def find(chat: str, ts: int) -> list[dict]:
    """Latest version of every event in `chat` with Signal timestamp `ts` (one per author, usually one)."""
    return latest(e for e in iter_events(raw=False) if e.get("chat") == chat and e.get("ts") == ts)


def last_seq() -> int:
    """Seq of the newest event in the store (0 when empty)."""
    nxt = read_state().get("next_seq")
    return int(nxt) - 1 if nxt else _scan_last_seq()


# --- sending ---------------------------------------------------------------------------------------------------

def record_out(account: str, chat: str, text: str, ts: int | None, *, attachments=(), quote=None,
               meta: dict | None = None) -> int | None:
    """Append an `out` event for a message house sent (or tried to send); called by `Signal.send`.

    Args:
        account: The account number (sender of the event and author part of its key).
        chat: "self" or the group id.
        text: The text as sent.
        ts: Signal timestamp from signal-cli, or None when sending failed (status "failed").
        attachments: Paths of attached files (stored with their original path).
        quote: (timestamp, author) of the quoted message; its text is copied from the store when known.
        meta: `origin`, `contest`, `question`.

    Returns:
        The event's seq, or None when it went to `pending.jsonl`.
    """
    from . import signalchats
    now = datetime.now().astimezone()
    stamp = ts if ts is not None else f"out-failed-{int(now.timestamp() * 1000)}"
    quoted = None
    if quote:
        found = find(chat, int(quote[0]))
        quoted = {"ts": int(quote[0]), "author": str(quote[1]), "text": found[0].get("text") if found else None}
    files = []
    for path in attachments:
        p = Path(path)
        files.append({"content_type": mimetypes.guess_type(p.name)[0], "filename": p.name,
                      "size": p.stat().st_size if p.is_file() else None, "id": None, "path": str(p)})
    time_ = datetime.fromtimestamp(ts / 1000).astimezone() if ts is not None else now
    return append({"v": 1, "key": f"{chat}:{account}:{stamp}", "kind": "message", "dir": "out", "chat": chat,
                   "chat_alias": signalchats.alias_of(chat), "ts": ts, "time": time_.isoformat(timespec="seconds"),
                   "received_at": now.isoformat(timespec="seconds"),
                   "sender": {"name": "house", "number": account, "uuid": None}, "text": text, "quote": quoted,
                   "mentions": [], "attachments": files, "expires_in": 0, "target": None, "meta": meta,
                   "status": "ok" if ts is not None else "failed", "raw": None})


# --- receiving ------------------------------------------------------------------------------------------------

@dataclass
class PullResult:
    """Outcome of one `pull`.

    Attributes:
        kept: Events written to the store.
        dropped: Messages from DMs or non-allowed groups that were discarded (only this number is recorded).
        bad_lines: Non-JSON stdout lines plus envelopes the parser could not handle (content never stored).
        error: signal-cli problem ("timeout", "signal-cli not found", first stderr line, …) or None.
        skipped: Why nothing was received: "Signal wyłączony" or "inny odbiór trwa"; None when a receive ran.
        interrupted: SIGTERM/SIGINT arrived; the lines already printed were stored, then the signal was re-raised.
    """

    kept: int = 0
    dropped: int = 0
    bad_lines: int = 0
    error: str | None = None
    skipped: str | None = None
    interrupted: bool = False


def _safe(name: str) -> str:
    """File-name-safe version of an attachment or alias name (letters, digits, `.`, `-`, `_`; ≤ 100 chars)."""
    return re.sub(r"[^\w.\-]+", "_", name).strip("._")[:100] or "plik"


def _private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def _copy_attachments(event: dict, src_dir: Path) -> None:
    """Copy the files signal-cli downloaded for this event into `files/<alias>/` and set each `path`."""
    for i, a in enumerate(event.get("attachments") or []):
        aid = str(a.get("id") or "")
        src = Path(src_dir) / aid
        if not aid or Path(aid).name != aid or not src.is_file():
            continue
        name = _safe(str(a.get("filename") or aid))
        stem = f"{str(event.get('time') or '')[:10]}_{event['ts']}" + (f"_{i + 1}" if i else "")
        try:
            folder = _private_dir(_private_dir(store_dir() / "files") / _safe(str(event.get("chat_alias") or "chat")))
            dst = folder / f"{stem}_{name}"
            shutil.copyfile(src, dst)
            dst.chmod(0o600)
        except OSError:
            continue  # the message itself must still be stored; the file shows as „nie pobrano”
        a["path"] = str(dst)


@contextmanager
def _deferred_signals(stop: list):
    """Turn SIGTERM/SIGINT into a stop request while receiving; re-raise the first one afterwards."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    old = {s: sigmod.signal(s, lambda n, f: stop.append(n)) for s in (sigmod.SIGTERM, sigmod.SIGINT)}
    try:
        yield
    finally:
        for s, handler in old.items():
            sigmod.signal(s, handler)


def pull(sig, chats: dict | None = None, timeout: int = 1, max_messages: int | None = None) -> PullResult:
    """Receive pending Signal messages and append each allowed one to the store before reading the next.

    This is the only caller of `Signal.receive`. It never blocks on another receive: when `receive.lock` is busy
    it returns at once. SIGTERM/SIGINT during the receive stop it after the current line (signal-cli is
    terminated and what it already printed is still stored), then the signal is re-raised to the old handler.

    Args:
        sig: A `Signal` client, or None (Signal not configured: nothing happens).
        chats: Allowed chats (chat id → `signalchats.Chat`). Defaults to None, meaning `signalchats.allowed(sig)`.
        timeout: Seconds signal-cli waits for new messages. Defaults to 1 (hook, reading commands); 5 for
            `house signal pull` and the timer.
        max_messages: Upper bound per call (the hook uses 200; the rest waits on the server). Defaults to None.

    Returns:
        A `PullResult`. State (`last_pull_ok`, `last_pull_error`, `dropped_last_pull`, `bad_lines`) is updated.
    """
    res = PullResult()
    if sig is None:
        res.skipped = "Signal wyłączony"
        return res
    stop: list = []
    with lock("receive.lock", blocking=False) as held:
        if not held:
            res.skipped = "inny odbiór trwa"
            return res
        ingest_pending()
        if chats is None:
            from . import signalchats
            chats = signalchats.allowed(sig)
        aliases = {cid: c.alias for cid, c in chats.items()}

        def handle(obj: dict) -> None:
            try:
                event, dropped = signalparse.extract(obj, sig.account, aliases)
            except Exception:  # noqa: BLE001 — one odd envelope is skipped, never the batch (spec §13)
                res.bad_lines += 1
                return
            res.dropped += int(dropped)
            if event is not None:
                _copy_attachments(event, sig.attachments_dir)
                append(event)
                res.kept += 1

        try:
            with _deferred_signals(stop):
                r = sig.receive(handle, timeout=timeout, max_messages=max_messages, should_stop=lambda: bool(stop))
        except Exception as e:  # a failing append: record why, then let the caller see it
            update_state(last_pull_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                         last_pull_error=f"zapis do magazynu: {type(e).__name__}: {e}"[:200])
            raise
        res.bad_lines += r.bad_lines
        res.interrupted = bool(stop)
        res.error = r.error or ("przerwano" if stop else None)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        state = read_state()
        changes = {"last_pull_at": now, "last_pull_error": res.error, "dropped_last_pull": res.dropped,
                   "bad_lines": int(state.get("bad_lines") or 0) + res.bad_lines}
        if res.error is None:
            changes["last_pull_ok"] = now
        update_state(**changes)
    if stop:
        os.kill(os.getpid(), stop[0])
    return res


# --- migration from the P3 files ------------------------------------------------------------------------------

OLD_FILES = ("inbox.jsonl", "inbox_done.json", "sent.jsonl")


def _set_aside(path: Path, now: datetime) -> str:
    """Rename `path` to `<name>.migrated-<YYYYmmdd-HHMMSS>` (never overwriting) and return the new name."""
    base = path.with_name(f"{path.name}.migrated-{now:%Y%m%d-%H%M%S}")
    target, n = base, 1
    while target.exists():
        n += 1
        target = base.with_name(f"{base.name}-{n}")
    path.rename(target)
    return target.name


def _old_lines(path: Path) -> list[dict]:
    return [e for e, _ in _parse_lines(path.read_bytes()) if e is not None]


def _skeleton(chat: str, author: str, ts: int, dir_: str, sender: dict, text: str, quote, meta: dict) -> dict:
    """A store event for data that never had a signal-cli envelope (migrated lines)."""
    from . import signalchats
    time_ = datetime.fromtimestamp(ts / 1000).astimezone().isoformat(timespec="seconds")
    return {"v": 1, "key": f"{chat}:{author}:{ts}", "kind": "message", "dir": dir_, "chat": chat,
            "chat_alias": signalchats.alias_of(chat), "ts": ts, "time": time_, "received_at": None,
            "sender": sender, "text": text, "quote": quote, "mentions": [], "attachments": [], "expires_in": 0,
            "target": None, "meta": meta, "status": "ok", "raw": None}


@contextmanager
def _inbox_lock(take: bool):
    """Hold `<data dir>/inbox.lock` (as `house inbox` does) for up to LOCK_TIMEOUT s; yields whether it is held."""
    if not take:
        yield True
        return
    fd = os.open(notify.data_dir() / "inbox.lock", os.O_RDWR | os.O_CREAT, 0o600)
    deadline, held = time.monotonic() + LOCK_TIMEOUT, False
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.02)
        yield held
    finally:
        if held:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def migrate(now: datetime | None = None, inbox_locked: bool = False) -> list[str]:
    """Move the P3 files (`inbox.jsonl`, `inbox_done.json`, `sent.jsonl` in the data dir) into the store.

    Idempotent: each old file is renamed to `<name>.migrated-<stamp>` (kept, never deleted) after its lines are
    in the store, and a line whose (chat, ts) is already stored is skipped. So a file that reappears (old code
    still writing `inbox.jsonl`) is migrated again without duplicates.

    Takes `inbox.lock` first (old code appends `inbox.jsonl` under it, and `house inbox` rewrites
    `signal/inbox_done.json` under it), unless the caller already holds it.

    Args:
        now: Time used for the rename stamp and `state.migrated`. Defaults to the current time.
        inbox_locked: The caller (`inbox.process`) already holds `inbox.lock`. Defaults to False.

    Returns:
        Log lines (Polish), one per migrated file; [] when there was nothing to migrate.
    """
    now = now or datetime.now()
    data = notify.data_dir()
    if not any((data / name).exists() for name in OLD_FILES):
        return []
    log = []
    with _inbox_lock(not inbox_locked) as inbox_held, lock("store.lock", blocking=True) as held:
        if not inbox_held:
            return ["migracja pominięta: działa `house inbox`"]
        if not held:
            return ["migracja pominięta: magazyn zajęty"]
        stored = {(e.get("chat"), e.get("ts")) for e in iter_events(raw=False)}
        path = data / "inbox.jsonl"
        if path.exists():
            lines = [x for x in _old_lines(path) if str(x.get("ts", "")).isdigit() and x.get("chat")]
            owners = {x.get("sender") for x in lines if x.get("chat") == "self"}
            added = 0
            for x in sorted(lines, key=lambda x: int(x["ts"])):
                ts, chat = int(x["ts"]), str(x["chat"])
                if (chat, ts) in stored:
                    continue
                q = x.get("quote_ts")
                quote = {"ts": int(q), "author": None, "text": None} if str(q or "").isdigit() else None
                dir_ = "own" if chat == "self" or x.get("sender") in owners else "in"
                sender = {"name": str(x.get("sender") or "?"), "number": None, "uuid": None}
                _append_locked(_skeleton(chat, "migrated", ts, dir_, sender, str(x.get("text") or ""), quote,
                                         {"migrated": True}))
                stored.add((chat, ts))
                added += 1
            log.append(f"inbox.jsonl: {added} wiadomości → magazyn ({len(lines) - added} już były); "
                       f"oryginał: {_set_aside(path, now)}")
        path = data / "sent.jsonl"
        if path.exists():
            lines = [x for x in _old_lines(path) if str(x.get("ts", "")).isdigit()]
            added = 0
            for x in lines:
                ts, chat = int(x["ts"]), str(x.get("chat") or "self")
                if (chat, ts) in stored:
                    continue
                meta = {"origin": "notify", "contest": x.get("contest"), "question": x.get("question"),
                        "migrated": True}
                _append_locked(_skeleton(chat, "migrated-out", ts, "out", {"name": "house", "number": None,
                                                                            "uuid": None}, "", None, meta))
                stored.add((chat, ts))
                added += 1
            log.append(f"sent.jsonl: {added} wysłanych → magazyn; oryginał: {_set_aside(path, now)}")
        path = data / "inbox_done.json"
        if path.exists():
            old = read_json(path, [])
            done_ts = {int(t) for t in old if str(t).isdigit()} if isinstance(old, list) else set()
            new_path = store_dir() / "inbox_done.json"
            done = read_json(new_path, {})
            done = done if isinstance(done, dict) else {}
            for e in iter_events(raw=False):
                if e.get("ts") in done_ts and e.get("dir") != "out":
                    done.setdefault(str(e["key"]), None)
            write_json(new_path, done)
            log.append(f"inbox_done.json: {len(done_ts)} obsłużonych; oryginał: {_set_aside(path, now)}")
        state = read_state()
        state["migrated"] = now.astimezone().isoformat(timespec="seconds")
        write_json(store_dir() / STATE, state)
    return log


def ensure_migrated(inbox_locked: bool = False) -> list[str]:
    """Run `migrate` when any P3 file is present in the data dir; [] otherwise (cheap: three `exists` calls)."""
    data = notify.data_dir()
    return migrate(inbox_locked=inbox_locked) if any((data / name).exists() for name in OLD_FILES) else []
