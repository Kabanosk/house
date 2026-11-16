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
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from . import notify

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


def _append_locked(event: dict) -> int:
    """Append one event with the next seq; the caller holds `store.lock`."""
    state = read_state()
    seq = int(state.get("next_seq") or _scan_last_seq() + 1)
    rest = {k: v for k, v in event.items() if k not in ("v", "seq")}
    _append_bytes(store_dir() / MESSAGES, _line({"v": event.get("v", 1), "seq": seq, **rest}))
    state["next_seq"] = seq + 1
    write_json(store_dir() / STATE, state)
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
    """Deduplicate by `key`: the last line of each key wins, in order of that key's first appearance."""
    by_key: dict[str, dict] = {}
    for e in events:
        by_key[str(e.get("key"))] = e
    return list(by_key.values())


def find(chat: str, ts: int) -> list[dict]:
    """Latest version of every event in `chat` with Signal timestamp `ts` (one per author, usually one)."""
    return latest(e for e in iter_events(raw=False) if e.get("chat") == chat and e.get("ts") == ts)


def last_seq() -> int:
    """Seq of the newest event in the store (0 when empty)."""
    nxt = read_state().get("next_seq")
    return int(nxt) - 1 if nxt else _scan_last_seq()
