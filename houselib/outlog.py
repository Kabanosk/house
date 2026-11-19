"""`house out`: run a long command into a log file and show the agent only a short envelope.

Full test runs, builds and training scripts print far more than a model should read. `run` streams the command's
output (stdout and stderr in one pipe, in order) into `.house/tmp/out/<id>.log` and returns the exit code with an
envelope of at most `ENVELOPE_MAX` characters: exit code, bytes, lines, duration, the last lines of output and a
ready `house out show <id> --grep '<pattern>'` command to look into the log.

Used by: `house out -- CMD ...` (coders and the coordinator for commands that may fail noisily), `house exp`
(shares `cap_tail`, so both log kinds keep the same last `LOG_CAP` characters).

Rules an agent should know:
- `show <id|E-###> --grep PATTERN` prints at most 20 matching lines with 2 lines of context, lines cut to 500
  characters (around the match), 16 000 characters in total, then a `more hits` note; the pattern is literal unless
  `--regex`. `prune` deletes `out` logs older than N days (default 7).
- The log lives in the MAIN contest's `.house/tmp/out/` (git-ignored), also when the command is started from a
  worker worktree; the id is `YYYYMMDD-HHMMSS-<4 hex>`.
- The log keeps the last `LOG_CAP` characters; `\\r` progress updates stay raw in the log and collapse to their
  last state in the envelope.
- The envelope names the log as `$HOUSE_DIR/tmp/out/<id>.log` (works from worktrees, prints no home path).
- The exit code of the command is passed on (128+n when a signal killed it, 127 when the command does not exist,
  126 when it cannot be executed). `run` never raises because the command failed.
- Once the command (the group leader) exits, its output is drained for at most `POST_EXIT_DRAIN` seconds (or until
  EOF); whatever is left of its process group then gets SIGTERM and, after `TERM_GRACE` seconds, SIGKILL. The exit
  code stays the leader's and the envelope says `background processes stopped`.
- Ctrl-C, SIGTERM or SIGHUP (handled by `run` itself): the same signal goes to the command's whole process group,
  the output keeps being drained during the grace period, then SIGKILL; the log keeps what was written, the envelope
  is still returned and the exit code is 128+n (130, 143, 129). The signals are ignored while the log is finished.
- SIGINT/SIGTERM/SIGHUP are blocked while the command is being started (the child gets the old mask back), so an
  interrupt cannot land between the start and the moment `run` knows the process. A SIGKILL of `house out` itself
  cannot be handled: the command's group (its own session) is then orphaned and keeps running.
- Containment: `.house/tmp` and `.house/tmp/out` must not be symlinks (`run`, `show` and `prune` refuse them);
  `show` reads only a regular file and never follows a symlink; the writer holds a shared `flock` on its log (and on
  the temp file of the final cap) for the whole run, and `prune` skips files it cannot lock exclusively.
"""
import contextlib
import fcntl
import os
import re
import secrets
import select
import signal
import stat
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping

from . import guard

LOG_CAP = 1_000_000  # characters kept in a log (`house exp` uses the same limit)
ENVELOPE_MAX = 4000  # hard limit for the whole envelope
LINE_MAX = 500  # an envelope line is cut to this many characters
TAIL_LINES = 40  # lines of output the envelope shows at most
SHOW_MAX = 16000  # hard limit for the text `show` returns
SHOW_HITS = 20  # matching lines `show` prints at most
SHOW_CONTEXT = 2  # lines of context before and after a hit
PRUNE_DAYS = 7
_OUT_ID = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{4}")
_EXP_ID = re.compile(r"E-([0-9]+)")
_TMP_NAME = re.compile(r"\.[0-9]{8}-[0-9]{6}-[0-9a-f]{4}\.log(\.[a-z0-9_]+)?\.tmp")  # `finish`'s mkstemp names
GRACE_SECONDS = 5.0  # between the interrupt signal and SIGKILL
POST_EXIT_DRAIN = 1.0  # after the leader exits: seconds to keep collecting output before stopping the group
TERM_GRACE = 2.0  # after the leader exits: seconds between SIGTERM and SIGKILL for the rest of the group
_STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
_CHUNK = 65536
_MAX_UTF8 = 4  # bytes per character, bounds the byte size of LOG_CAP characters


def cap_tail(text: str, cap: int | None = None) -> str:
    """Keep the last `cap` characters of a log.

    Args:
        text: Full log text.
        cap: Characters to keep; default is the module's `LOG_CAP`.

    Returns:
        `text` itself when it is short enough, else its last `cap` characters.
    """
    cap = LOG_CAP if cap is None else cap
    return text[-cap:] if cap > 0 else ""


class Interrupted(KeyboardInterrupt):
    """SIGTERM/SIGHUP while a command runs (e.g. the agent's tool timeout), raised like Ctrl+C.

    Attributes:
        signum: The signal that arrived (`house out` reports 128 + signum).
    """

    def __init__(self, signum: int = signal.SIGTERM) -> None:
        super().__init__(signum)
        self.signum = signum


@contextlib.contextmanager
def signals_interrupt():
    """Turn SIGTERM and SIGHUP into `Interrupted` while running (main thread only; SIGINT already raises).

    The previous handlers are restored afterwards.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def handler(signum, frame):
        raise Interrupted(signum)
    old = {sig: signal.signal(sig, handler) for sig in (signal.SIGTERM, signal.SIGHUP)}
    try:
        yield
    finally:
        for sig, h in old.items():
            signal.signal(sig, h)


def resolve_contest(cwd: Path, environ: Mapping[str, str] | None = None) -> Path:
    """Find the main contest checkout for logs: `HOUSE_DIR` first, else the contest `cwd` belongs to.

    Args:
        cwd: Directory the command was started in (a worker worktree resolves to its main contest).
        environ: Environment to read `HOUSE_DIR` from; default `os.environ`. `HOUSE_DIR` is the contest's
            `.house` directory, so its parent is the contest.

    Returns:
        Absolute path of the main contest checkout.

    Raises:
        ValueError: Neither `HOUSE_DIR` nor `cwd` points into a house contest.
    """
    env = os.environ if environ is None else environ
    house = env.get("HOUSE_DIR")
    if house:
        return Path(house).resolve().parent
    contest = guard.contest_root(Path(cwd))
    if contest is None:
        raise ValueError("not inside a house contest (no .house/ found and HOUSE_DIR is not set)")
    return contest


def _out_dir(contest: Path, create: bool = False) -> Path:
    """`<contest>/.house/tmp/out`, refusing a symlinked `tmp` or `out` (logs must stay in this contest).

    Args:
        contest: Main contest checkout.
        create: Create the missing directories (each checked before and after it is made).

    Returns:
        The log directory (it may not exist unless `create`).

    Raises:
        ValueError: `.house/tmp` or `.house/tmp/out` is a symlink.
    """
    tmp = Path(contest) / ".house" / "tmp"
    out = tmp / "out"
    for path in (tmp, out):
        for _ in range(2 if create else 1):
            if path.is_symlink():
                raise ValueError(f".house/{path.relative_to(tmp.parent)} is a symlink; refusing to use it for logs")
            if create:
                path.mkdir(parents=path is tmp, exist_ok=True)
    return out


def _new_log(contest: Path, now: Callable[[], datetime]) -> tuple[str, Path]:
    """Create an empty log with a fresh id (`YYYYMMDD-HHMMSS-<4 hex>`); never reuses an existing file."""
    out = _out_dir(contest, create=True)
    stamp = now().strftime("%Y%m%d-%H%M%S")
    while True:
        log_id = f"{stamp}-{secrets.token_hex(2)}"
        path = out / f"{log_id}.log"
        try:
            path.open("xb").close()
        except FileExistsError:
            continue
        return log_id, path


class _LogSink:
    """Append command output to the log as it arrives, with bounded size, and count the whole stream.

    The log is opened without following a symlink, and a shared `flock` is held on it until `finish` or `close`
    (`prune` skips a locked log).
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.size = 0  # bytes of the whole stream
        self.lines = 0
        self._compacted = False
        self.cut = False  # the log holds less than the stream (capped to the last `LOG_CAP` characters)
        self._last = b""
        self._f = os.fdopen(os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o666), "ab")
        try:
            self._lock = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except BaseException:
            self._f.close()
            raise
        fcntl.flock(self._lock, fcntl.LOCK_SH)

    def _release(self) -> None:
        if self._lock >= 0:
            os.close(self._lock)
            self._lock = -1

    def _read_all(self) -> bytes:
        """The log's bytes, read through the locked descriptor (never the path again)."""
        os.lseek(self._lock, 0, os.SEEK_SET)
        parts = []
        while chunk := os.read(self._lock, 1 << 20):
            parts.append(chunk)
        return b"".join(parts)

    def write(self, chunk: bytes) -> None:
        """Append a chunk, compacting the file to its tail when it grows far beyond the cap."""
        self._f.write(chunk)
        self._f.flush()
        self.size += len(chunk)
        self.lines += chunk.count(b"\n")
        self._last = chunk[-1:]
        if self._f.tell() > 2 * _MAX_UTF8 * LOG_CAP:
            self._compact(_MAX_UTF8 * LOG_CAP)

    def _compact(self, keep: int) -> None:
        """Keep the last `keep` bytes of the log (starting on a UTF-8 character boundary), in place."""
        self._f.flush()
        size = os.fstat(self._lock).st_size
        tail = os.pread(self._lock, keep, max(0, size - keep))
        skip = 0
        while skip < min(3, len(tail)) and tail[skip] & 0xC0 == 0x80:
            skip += 1
        self._f.truncate(0)
        self._f.write(tail[skip:])  # append mode: written at the (new) end
        self._f.flush()
        self._compacted = True

    def close(self) -> None:
        """Close the log file without capping it, releasing the lock."""
        try:
            self._f.close()
        finally:
            self._release()

    def finish(self) -> str:
        """Close the log, cap it to `LOG_CAP` characters and return its text.

        A capped log is written to a `mkstemp` file in the same directory (locked like the log) and moved over the
        log, so the replacement is atomic and no predictable name is ever opened.
        """
        try:
            self._f.close()
            if self.size and self._last != b"\n":
                self.lines += 1
            raw = self._read_all()
            full = raw.decode("utf-8", errors="replace")
            text = cap_tail(full)
            self.cut = len(text) < len(full) or self._compacted
            data = text.encode("utf-8")
            if data != raw:
                self._replace(data)
            return text
        finally:
            self._release()

    def _replace(self, data: bytes) -> None:
        fd, tmp = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        finally:
            os.close(fd)


def _cut(line: str, limit: int = LINE_MAX) -> str:
    """Cut a line to `limit` characters and say how much was left out."""
    if len(line) <= limit:
        return line
    return f"{line[:limit]}... [+{len(line) - limit} more characters]"


def _collapse_progress(line: str) -> str:
    """Reduce a `\\r`-updated progress line to its last non-empty state."""
    parts = [p for p in line.split("\r") if p]
    return parts[-1] if parts else ""


def envelope(*, exit_code: int, size: int, lines: int, seconds: float, log_id: str, tail: str,
             note: str = "", log_cut: bool = False) -> str:
    """Build the short summary an agent sees instead of the output.

    Args:
        exit_code: Exit code of the command (128+n for a signal).
        size: Bytes the command printed in total.
        lines: Lines the command printed in total.
        seconds: Wall-clock duration.
        log_id: Id of the log (`house out show <id>`).
        tail: End of the output; the last `TAIL_LINES` lines are shown.
        note: Extra lines (interrupted, command not found, background processes stopped, ...), optional.
        log_cut: The log was capped to its last `LOG_CAP` characters.

    Returns:
        The envelope, at most `ENVELOPE_MAX` characters (the tail is trimmed first, oldest lines first).
    """
    plural = "line" if lines == 1 else "lines"
    head = [f"house out: exit {exit_code} | {size} bytes | {lines} {plural} | {seconds:.1f}s"]
    if note:
        head.append(note)
    foot = []
    if log_cut:
        foot.append(f"log keeps the last {LOG_CAP} characters")
    foot.append(f"log: $HOUSE_DIR/tmp/out/{log_id}.log")
    foot.append(f"house out show {log_id} --grep '<pattern>'")
    rows = tail.split("\n")
    if tail.endswith("\n"):
        rows.pop()
    total_rows = len(rows) if tail else 0
    rows = [_cut(_collapse_progress(r)) for r in rows[-TAIL_LINES:]] if tail else []
    fixed = "\n".join([*head, "---", "---", *foot])
    budget = ENVELOPE_MAX - len(fixed) - 40  # 40: room for the "earlier lines omitted" marker
    shown: list[str] = []
    for row in reversed(rows):
        cost = len(row) + 1
        if cost > budget:
            if not shown and budget > 0:
                shown.append(row[:budget - 1])  # the last line alone is too wide: keep its start
            break
        shown.append(row)
        budget -= cost
    shown.reverse()
    omitted = total_rows - len(shown)
    body = ([f"[{omitted} earlier lines in the log]"] if omitted > 0 else []) + shown
    if not body:
        body = ["(no output)"]
    text = "\n".join([*head, "---", *body, "---", *foot])
    return text[:ENVELOPE_MAX]


def _group_signal(proc: subprocess.Popen, sig: int) -> None:
    """Send a signal to the command's process group; a group that is already gone is fine."""
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


@contextlib.contextmanager
def _signals_ignored():
    """Ignore SIGINT, SIGTERM and SIGHUP (main thread only), so no handler can cut cleanup or finishing short."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    old = {sig: signal.signal(sig, signal.SIG_IGN) for sig in _STOP_SIGNALS}
    try:
        yield
    finally:
        for sig, h in old.items():
            signal.signal(sig, h)


def _group_alive(proc: subprocess.Popen) -> bool:
    """Whether a live (not zombie) process of the command's group still exists.

    An orphan that has just exited stays a zombie until its new parent reaps it, so `killpg(pgid, 0)` alone would
    count it; `/proc` tells the states apart (without `/proc`, any member counts).
    """
    try:
        os.killpg(proc.pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    try:
        pids = [e for e in os.listdir("/proc") if e.isdigit()]
    except OSError:
        return True
    for pid in pids:
        try:
            with open(f"/proc/{pid}/stat", "rb") as f:
                fields = f.read().rsplit(b")", 1)[1].split()  # state, ppid, pgrp, ...
        except (OSError, IndexError):
            continue
        if len(fields) > 2 and fields[2] == str(proc.pid).encode() and fields[0] != b"Z":
            return True
    return False


def _read(proc: subprocess.Popen, sink: _LogSink, timeout: float) -> bool | None:
    """Wait up to `timeout` seconds for output and copy one chunk; True at EOF, False after a chunk, None if idle."""
    fd = proc.stdout.fileno()
    if not select.select([fd], [], [], max(0.0, timeout))[0]:
        return None
    chunk = os.read(fd, _CHUNK)
    if not chunk:
        return True
    sink.write(chunk)
    return False


def _drain_until(proc: subprocess.Popen, sink: _LogSink, deadline: float, done: Callable[[bool], bool],
                 eof: bool) -> bool:
    """Keep collecting output until `done(eof)` or the deadline (just wait after EOF); return whether EOF was seen."""
    while not done(eof):
        left = deadline - time.monotonic()
        if left <= 0:
            break
        if eof:
            time.sleep(min(left, 0.05))
        elif _read(proc, sink, min(left, 0.05)) is True:
            eof = True
    return eof


def _drain(proc: subprocess.Popen, sink: _LogSink, limit: float = 1.0) -> None:
    """After a stop: collect what is still buffered in the pipe, for at most `limit` seconds."""
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if _read(proc, sink, 0.2) in (True, None):
            return


def _cleanup(proc: subprocess.Popen, sink: _LogSink, grace: float, sig: int = signal.SIGINT) -> None:
    """Stop the command and collect its remaining output; repeated signals only restart this (it is idempotent)."""
    while True:
        try:
            with _signals_ignored():
                _stop(proc, sink, grace, sig)
                _drain(proc, sink)
            return
        except KeyboardInterrupt:  # arrived before the handlers were ignored
            continue


def _stop(proc: subprocess.Popen, sink: _LogSink, grace: float, sig: int) -> None:
    """Interrupt: `sig` to the group, drain output for up to `grace` seconds while the leader shuts down, then
    SIGKILL; always leaves the group dead."""
    _group_signal(proc, sig)
    _drain_until(proc, sink, time.monotonic() + grace, lambda _eof: proc.poll() is not None, eof=False)
    _group_signal(proc, signal.SIGKILL)  # also stragglers that outlived the leader
    proc.wait()


def _pump(proc: subprocess.Popen, sink: _LogSink) -> bool:
    """Copy the command's output into the sink until the leader exits (checked on every pass); True at EOF."""
    while proc.poll() is None:
        if _read(proc, sink, 0.1) is True:
            proc.wait()  # the leader closed its output but still runs
            return True
    return False


def _after_exit(proc: subprocess.Popen, sink: _LogSink, eof: bool) -> bool:
    """Once the leader has exited: drain for at most `POST_EXIT_DRAIN` seconds (or until EOF), then SIGTERM what is
    left of the group, keep draining for `TERM_GRACE` seconds, SIGKILL. Returns whether any process was stopped."""
    eof = _drain_until(proc, sink, time.monotonic() + POST_EXIT_DRAIN, lambda seen: seen, eof)
    if not _group_alive(proc):
        if not eof:
            _drain(proc, sink)
        return False
    _group_signal(proc, signal.SIGTERM)
    eof = _drain_until(proc, sink, time.monotonic() + TERM_GRACE, lambda _eof: not _group_alive(proc), eof)
    _group_signal(proc, signal.SIGKILL)
    if not eof:
        _drain(proc, sink)
    return True


def run(argv: list[str], contest: Path, now: Callable[[], datetime] = datetime.now,
        grace: float = GRACE_SECONDS) -> tuple[int, str]:
    """Run a command with its whole output going to a log; return its exit code and a short envelope.

    The command runs in the current directory, in its own process group (so a stop signal can reach everything it
    started), with stdin closed and `PYTHONUNBUFFERED=1`. Stderr is merged into stdout, in order. `run` handles
    SIGINT, SIGTERM and SIGHUP itself while the command runs, and ignores them while the log is finished.

    Args:
        argv: Command and arguments (no shell).
        contest: Main contest checkout; the log goes to `<contest>/.house/tmp/out/<id>.log`.
        now: Clock for the log id (tests inject a fixed one).
        grace: Seconds between the interrupt signal and SIGKILL.

    Returns:
        `(exit code, envelope)`. The code is the leader's own (128+n for a signal, also after its background
        processes were stopped), 128+n after an interrupt by signal n (130 Ctrl-C, 143 SIGTERM, 129 SIGHUP), 127
        when the command is not found, 126 when it cannot be executed. Command failures never raise.

    Raises:
        ValueError: `argv` is empty, or `.house/tmp` or `.house/tmp/out` is a symlink.
        OSError: The log cannot be created.
    """
    if not argv:
        raise ValueError("no command given")
    log_id, path = _new_log(contest, now)
    sink = _LogSink(path)
    started = time.monotonic()
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    notes = []
    stopped_by = 0  # the interrupt signal, if any
    proc = None
    try:
        with signals_interrupt():
            try:
                # blocked until `proc` is set, so an interrupt cannot orphan a command that has just started
                old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, _STOP_SIGNALS)
                try:
                    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, env=env, start_new_session=True,
                                            preexec_fn=lambda: signal.pthread_sigmask(signal.SIG_SETMASK, old_mask))
                finally:
                    signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)  # a pending interrupt is raised here
                eof = _pump(proc, sink)
                code = proc.returncode
                if _after_exit(proc, sink, eof):
                    notes.append("background processes stopped")
            except OSError as e:
                if proc is not None:  # e.g. the log's disk is full: do not leave the command running
                    _cleanup(proc, sink, 0.0)
                    sink.close()
                    raise
                code = 127 if isinstance(e, FileNotFoundError) else 126
                reason = "command not found" if code == 127 else "cannot execute"
                notes.append(f"{reason}: {argv[0]} ({e.strerror or e})")
                sink.write(f"house out: {notes[-1]}\n".encode())
            except KeyboardInterrupt as e:
                stopped_by = getattr(e, "signum", signal.SIGINT)
                code = 128 + stopped_by
                if proc is not None:
                    _cleanup(proc, sink, grace, stopped_by)
    finally:
        if proc is not None and proc.stdout is not None:
            proc.stdout.close()
    with _signals_ignored():
        if not stopped_by and code < 0:
            code = 128 - code
        if stopped_by:
            name = "Ctrl-C" if stopped_by == signal.SIGINT else signal.Signals(stopped_by).name
            notes.insert(0, f"interrupted ({name}); the log keeps the output up to then")
        seconds = time.monotonic() - started
        text = sink.finish()
        return code, envelope(exit_code=code, size=sink.size, lines=sink.lines, seconds=seconds, log_id=log_id,
                              tail=text, note="\n".join(notes), log_cut=sink.cut)


def _log_path(contest: Path, ref: str) -> Path:
    """Map `<YYYYMMDD-HHMMSS-xxxx>` or `E-###` to its log file; any other spelling (paths included) is refused.

    `E-7` is read as `E-007` (the id `house exp` prints).
    """
    if _OUT_ID.fullmatch(ref):
        return _out_dir(contest) / f"{ref}.log"  # refuses a symlinked `.house/tmp` or `.house/tmp/out`
    if m := _EXP_ID.fullmatch(ref):
        return Path(contest) / ".house" / "experiments" / f"E-{int(m.group(1)):03d}.log"
    raise ValueError(f"bad log id {ref!r}: expected YYYYMMDD-HHMMSS-xxxx (house out) or E-### (house exp)")


def _window(line: str, start: int) -> str:
    """Cut a long hit line to `LINE_MAX` characters around the match that starts at `start`."""
    if len(line) <= LINE_MAX:
        return line
    begin = max(0, min(start - 100, len(line) - LINE_MAX))
    end = begin + LINE_MAX
    return ("..." if begin else "") + line[begin:end] + (f"... [+{len(line) - end} more characters]"
                                                          if end < len(line) else "")


def _read_log(path: Path, ref: str) -> bytes:
    """Read a log without following a symlink or blocking on a FIFO; only a regular file is read."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise ValueError(f"no log {ref}") from None
    except OSError:
        raise ValueError(f"log {ref} cannot be read (a symlink or not a regular file)") from None
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise ValueError(f"log {ref} cannot be read (a symlink or not a regular file)")
        return f.read()


def show(contest: Path, ref: str, pattern: str, regex: bool = False) -> str:
    """Search a log and return a bounded excerpt: the matching lines with a little context.

    Args:
        contest: Main contest checkout (see `resolve_contest`).
        ref: A `house out` id (`YYYYMMDD-HHMMSS-xxxx`) or an experiment id (`E-###`, its `house exp` log).
        pattern: Text to look for; literal unless `regex`. `\\r` progress updates are collapsed to their last
            state before matching.
        regex: Treat `pattern` as a regular expression (Python `re`).

    Returns:
        Text of at most `SHOW_MAX` characters: a header, up to `SHOW_HITS` hits as `N: line` with
        `SHOW_CONTEXT` context lines (`N- line`), and a `more hits: N not shown` note when some hits were left
        out (by count or by the total size).

    Raises:
        ValueError: Bad id, no such log (named by its id, never by a path), a log that is a symlink or not a regular
            file, a symlinked `.house/tmp` or `.house/tmp/out`, empty pattern or an invalid regular expression.
    """
    path = _log_path(contest, ref)
    ref = path.stem if _EXP_ID.fullmatch(ref) else ref
    if not pattern:
        raise ValueError("empty pattern")
    if regex:
        try:
            rx = re.compile(pattern)
        except re.error as e:
            raise ValueError(f"bad regex {pattern!r}: {e}") from e
        find = lambda line: (m.start() if (m := rx.search(line)) else -1)  # noqa: E731
    else:
        find = lambda line: line.find(pattern)  # noqa: E731
    raw = _read_log(path, ref)
    lines = [_collapse_progress(x) for x in raw.decode("utf-8", errors="replace").split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    hits = [(i, pos) for i, line in enumerate(lines) if (pos := find(line)) >= 0]
    label = pattern if len(pattern) <= 80 else pattern[:80] + "..."
    if not hits:
        return f"{ref}: no hits for {label!r} ({len(lines)} lines)"
    header = f"{ref}: {len(hits)} hits for {label!r} ({len(lines)} lines)"
    note_room = 120
    out = [header]
    used = len(header) + note_room
    shown = 0
    last = -1  # index of the last printed line
    marked = dict(hits[:SHOW_HITS])  # only these lines are hits; later matches in the context are plain context
    for i, pos in hits[:SHOW_HITS]:
        block = []
        for j in range(max(last + 1, i - SHOW_CONTEXT), min(len(lines), i + SHOW_CONTEXT + 1)):
            if j in marked:
                block.append(f"{j + 1}: {_window(lines[j], marked[j])}")
            else:
                block.append(f"{j + 1}- {_cut(lines[j])}")
        sep = ["--"] if last >= 0 and i - SHOW_CONTEXT > last + 1 else []
        cost = sum(len(x) + 1 for x in [*sep, *block])
        if used + cost > SHOW_MAX:
            break
        out.extend([*sep, *block])
        used += cost
        shown += 1
        last = min(len(lines) - 1, i + SHOW_CONTEXT)
    if shown < len(hits):
        out.append(f"more hits: {len(hits) - shown} not shown (narrow the pattern or search the log with grep)")
    return "\n".join(out)[:SHOW_MAX]


def prune(contest: Path, days: float = PRUNE_DAYS, now: Callable[[], datetime] = datetime.now) -> int:
    """Delete `house out` logs (and orphaned temp files) not modified for more than `days` days.

    Experiment logs are never touched, nor symlinks, nor a file a running `house out` still holds (it keeps a shared
    `flock`; prune needs an exclusive one, without waiting).

    Args:
        contest: Main contest checkout.
        days: Age limit in days; 0 deletes every log.
        now: Clock (tests inject a fixed one).

    Returns:
        Number of files deleted: old logs plus orphaned `.<id>.log.tmp` files left by an interrupted finish.

    Raises:
        ValueError: `days` is negative, or `.house/tmp` or `.house/tmp/out` is a symlink.
    """
    if days < 0:
        raise ValueError("days must not be negative")
    out = _out_dir(contest)
    if not out.is_dir():
        return 0
    cutoff = now().timestamp() - days * 86400
    deleted = 0
    for p in out.iterdir():
        if not (_OUT_ID.fullmatch(p.stem) and p.suffix == ".log" or _TMP_NAME.fullmatch(p.name)):
            continue
        try:
            fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)  # a symlink is never followed or removed
        except OSError:
            continue
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_mtime >= cutoff:
                continue
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # BlockingIOError: a writer still holds it
            now_st = p.lstat()
            if (now_st.st_dev, now_st.st_ino) == (st.st_dev, st.st_ino):
                p.unlink()
                deleted += 1
        except OSError:
            continue
        finally:
            os.close(fd)
    return deleted
