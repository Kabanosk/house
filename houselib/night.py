"""`house night`: work through ready tasks unattended, one fresh worker session per task.

preflight → (pick ready tasks → fresh session in the task worktree → `house merge` gate)* → morning report.
The dispatcher never writes code; workers never wait for a human (questions go to Signal, the task is blocked).

Used by: `house night <contest> [--hours H] [--max-tasks N] [--dry-run]` (see `cli.cmd_night`), run by the owner
or a scheduler. Workers are `claude -p --agent coder` sessions (or `bin/codex-task` for `coder-codex` tasks).

Worker logs are machine-readable: `.house/tmp/night/<date>/T###-<HHMMSS>-N.jsonl` holds Claude's stream-json output
(the session id is chosen here, passed as `--session-id` and kept in `Attempt.session`) or, for Codex, the
`codex exec --json` events written by `bin/codex-task --json-log` (the thread id is read from the log later).
A Codex wrapper's own stdout/stderr go to the same name with `.out`.
From the second attempt of a task on, `next_brief` is written to `.house/tmp/night/<date>/T###-<HHMMSS>-N.brief.md`:
Claude gets its content in the prompt, Codex gets the file through `bin/codex-task --brief-file`.

Rules an agent should know:
- Task status flow is todo → doing → review → merged → done, or blocked. Night picks `todo` tasks whose
  `depends_on` are all merged/done, sets `doing`, and after a worker run sets `review` and calls `house merge`.
- A worker must finish when `house check <contest> T### --worktree <wt>` prints GREEN, commit on the task branch
  and set `status: review`. A decision only a human can take: write a Q-### (protocols/questions.md), set
  `status: blocked`, stop. Never wait for input: stdin is /dev/null.
- Frozen criteria (`.house/checks/T###.json`) and `merged`/`done` statuses are written by house only. Tampering
  is restored from HEAD, the attempt counts as failed and the report lists it.
- A task is blocked after MAX_ATTEMPTS failed attempts; the night stops after MAX_CONSECUTIVE_FAILURES failures
  in a row, on `.house/STOP`, at the time limit, at `--max-tasks`, or when nothing is ready.
- Only the merge gate (`merge.merge_task`) puts work on the main line; night never merges by itself. The other
  direction is allowed: after an attempt that failed on a merge conflict (register `reason: conflict`), the next
  attempt starts with main merged into the task worktree, uncommitted (`merge_main`); the driver commits the merge
  at the end of the attempt only when every conflicted path is really resolved (`conflicts_left`, no conflict
  markers in the staged diff), else the merge stays in progress for the next attempt (never aborted).
- Night commits only files it wrote itself (task file, PROGRESS.md, report, the attempt register), never all of
  `.house/`.
- Every finished attempt (merged, failed, could not start, interrupted) gets one row in the attempt register
  `.house/attempts.jsonl` (see `attempts`), committed right after it is written; attempt numbers continue over
  nights. The morning report shows the short evidence form of every attempt (see `evidence`).
"""
import hashlib
import json
import os
import re
import shutil
import signal as sig_mod
import stat
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

from . import HOUSE_ROOT, checks, evidence, guard, handoff, merge
from . import attempts as register
from . import spec as specmod
from .fm import parse_frontmatter, set_fields

MIN_FREE_GB, WARN_FREE_GB = 5, 10
MAX_ATTEMPTS, MAX_CONSECUTIVE_FAILURES = 3, 5
DEFAULT_TASK_MINUTES = 45
POLL_SECONDS = 5
MAX_BRIEF = 8000  # ceiling of the hand-off brief of attempt 2+, in characters
MAX_REPORT_COPY = 256 * 1024  # ceiling of a kept report copy (`reports/T###-attempt-N.md`), in bytes
_COPY_MARGIN = 64 * 1024  # read past the ceiling so that redaction sees whole tokens at the cut


@dataclass
class Attempt:
    """One worker run on one task and how it ended.

    Attributes:
        task: Task id, e.g. "T001".
        n: Attempt number of the task in the attempt register (counted over all nights and sources), from 1.
        outcome: One of "merged", "merge-refused", "reverted", "no-commits", "timeout", "blocked-by-worker",
            "tampered", "interrupted", "error".
        message: Human-readable details (the merge message, the tampering list, the error text).
        log: Path of the worker's `.jsonl` stream log, relative to the contest when possible; "" if no worker was
            started.
        session: Claude session id the worker was started with; "" for Codex workers (the thread id is in the log)
            and when no worker was started.
        head: Task branch head the gate evaluated (read after the worker's leftovers were committed and before
            the merge removes the branch); "" when `_finish` returned before that point.
        reason: `conflict` when the attempt failed on a `git merge` conflict with the main line (the gate's merge,
            or a merge of main the worker left unresolved); "" otherwise. Stored in the register row.
        main_merged: How the merge of the main line into the task branch went before the attempt started
            (`clean`, `conflict in N files`, `failed: ...`); "" when there was none. Stored in the register row.
    """
    task: str
    n: int
    outcome: str  # merged | merge-refused | reverted | no-commits | timeout | blocked-by-worker | error
    message: str
    log: str
    session: str = ""
    head: str = ""
    reason: str = ""
    main_merged: str = ""


@dataclass
class NightReport:
    """Result of `run_night`.

    Attributes:
        attempts: Every attempt in order, merged and failed.
        stopped_because: Why the night ended, e.g. "STOP file", "time limit", "nothing ready", "interrupted".
        report_path: The written `.house/reports/night-<date>.md`, or None before it is written.
        signal_seq: Signal store seq at the start; the report counts messages stored after it (counts only).
    """
    attempts: list[Attempt] = field(default_factory=list)
    stopped_because: str = ""
    report_path: Path | None = None
    signal_seq: int | None = None


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Run `git <args>` in cwd and return the CompletedProcess (text mode, never raises on a non-zero exit)."""
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def _spec(contest: Path) -> dict:
    """Load the contest's `.house/spec.yaml` as a dict."""
    return specmod.load_spec(contest / ".house")


def _tools(contest: Path) -> dict:
    """Read `.house/tools.json` (written by `house doctor`); {} when missing or unreadable."""
    try:
        return json.loads((contest / ".house/tools.json").read_text())
    except (OSError, ValueError):
        return {}


def now_iso() -> str:
    """Current local time as an ISO string without microseconds (the `start`/`end` of a register row)."""
    return datetime.now().isoformat(timespec="seconds")


# --- preflight -------------------------------------------------------------------------------------------

def preflight(contest: Path, hours: float, now: datetime | None = None, run=subprocess.run,
              disk_free=shutil.disk_usage) -> tuple[list[str], list[str]]:
    """Check that an unattended night run can start.

    Args:
        contest: Path of the contest checkout (the directory that holds `.house/`).
        hours: Planned length of the run in hours; the Claude login must stay valid that long.
        now: Start time used to compute the end of the run; defaults to the current UTC time.
        run: Callable with the signature of `subprocess.run`, used to call `claude auth status` and
            `codex login status`; replace in tests.
        disk_free: Callable with the signature of `shutil.disk_usage`, called with the contest path.

    Returns:
        A pair `(problems, warnings)` of message lists. Any problem means the night must not start: STOP file
        present, uncommitted changes outside `.house/`, missing `.claude/settings.local.json`, a merged/done task
        that is red on main, less than MIN_FREE_GB free, Claude not logged in or its login expiring before the end.
        Warnings do not stop the run (low disk, Codex not logged in).
    """
    house = contest / ".house"
    problems, warnings = [], []
    if (house / "STOP").exists():
        problems.append(f"{house / 'STOP'} exists (remove it to allow a night run)")
    dirty = merge._dirty_outside_house(contest)
    if dirty:
        problems.append("uncommitted changes in the contest checkout: " + ", ".join(dirty))
    if not (contest / ".claude/settings.local.json").exists():
        problems.append("missing .claude/settings.local.json (run: house adapt <contest>)")
    frozen, _ = merge._finished_frozen(house)
    for tid in frozen:
        if not checks.run_checks(house, tid, contest, record=False).green:
            problems.append(f"{tid} is red on main (house check <contest> --all); fix main first")
    free_gb = disk_free(contest).free / 1024 ** 3
    if free_gb < MIN_FREE_GB:
        problems.append(f"disk: only {free_gb:.1f} GB free")
    elif free_gb < WARN_FREE_GB:
        warnings.append(f"disk: {free_gb:.1f} GB free")
    end = (now or datetime.now(UTC)) + timedelta(hours=hours)
    try:
        auth = json.loads(run(["claude", "auth", "status"], capture_output=True, text=True, check=False).stdout)
    except (OSError, ValueError):
        auth = {}
    if not auth.get("loggedIn"):
        problems.append("claude is not logged in (claude auth login, or claude setup-token for unattended runs)")
    elif auth.get("loginExpiresAt"):
        expires = datetime.fromisoformat(auth["loginExpiresAt"].replace("Z", "+00:00"))
        if expires < end:
            problems.append(f"claude login expires at {expires:%Y-%m-%d %H:%M} UTC, before the night ends")
    if _tools(contest).get("codex"):
        try:
            ok = run(["codex", "login", "status"], capture_output=True, text=True, check=False).returncode == 0
        except OSError:
            ok = False
        if not ok:
            warnings.append("codex is not logged in; coder-codex tasks fall back to coder")
    return problems, warnings


# --- tasks and workers -------------------------------------------------------------------------------------

def _tasks(house: Path) -> dict[str, dict]:
    """Map task id (e.g. "T001") to the frontmatter of every parseable `.house/tasks/T*-*.md`."""
    out = {}
    for path in sorted((house / "tasks").glob("T*-*.md")):
        try:
            out[path.name.split("-", 1)[0]] = parse_frontmatter(path.read_text())[0]
        except ValueError:
            continue
    return out


def pick_ready(house: Path, running: set[str], limit: int) -> list[str]:
    """Choose tasks a worker can start now.

    Args:
        house: Path of the contest's `.house/` directory.
        running: Task ids that already have a live worker; they are skipped.
        limit: Maximum number of ids to return; zero or negative returns none.

    Returns:
        Task ids, in file order, whose status is `todo` and whose `depends_on` tasks are all `merged` or `done`.
    """
    tasks = _tasks(house)
    finished = {t for t, m in tasks.items() if m.get("status") in ("merged", "done")}
    ready = [t for t, m in tasks.items() if m.get("status") == "todo" and t not in running
             and set(m.get("depends_on") or []) <= finished]
    return ready[:max(limit, 0)]


def _set(task: checks.Task, **fields) -> None:
    """Set frontmatter keys of the task file in place (other lines stay byte-identical)."""
    task.path.write_text(set_fields(task.path.read_text(), fields))


def _commit_paths(contest: Path, paths: list[Path], message: str) -> None:
    """Commit only files the night itself wrote. Never all of .house/: workers write there too, and committing
    their edits before the gate would hide tampering from `guard.frozen_tampering`."""
    rels = [str(p.relative_to(contest)) for p in paths if p.exists()]
    if not rels:
        return
    _git(contest, "add", "--", *rels)
    if _git(contest, "diff", "--cached", "--quiet", "--", *rels).returncode != 0:
        _git(contest, "commit", "-q", "-m", message, "--", *rels)


def _file_hash(path: Path) -> str | None:
    """SHA-256 of the file's bytes; None when it does not exist or cannot be read."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def record_attempt(contest: Path, row: register.Row, message: str | None = None,
                   report_from: Path | None = None) -> int | None:
    """Append `row` to the attempt register and commit exactly that file, as one unit (all or nothing).

    Everything happens under the register lock (`attempts.lock`), so a night and an interactive `house merge` never
    interleave: the attempt number is checked against the register first and, when `row.n` is already taken (or 0),
    the next free number is used. The register is only written while it equals HEAD: a hand edit (which the merge
    gate reports as tampering) is never committed on house's behalf, and the row is dropped with a line on stderr.
    Other uncommitted state under `.house/` stays as it is (the commit is limited to the register). When anything
    goes wrong after the write (a failed commit, Ctrl+C) the register is put back to HEAD's bytes, so a row that is
    not committed never stays behind to be reported as tampering. When `report_from` is given, the file is copied
    (redacted and capped by `_report_copy_bytes`; never through a symlink or from a non-regular file, and never onto
    a symlink) to `.house/reports/<task>-attempt-<n>.md` with the number actually used (so a number that moved
    on is followed), `row.report` is set to that path (relative to the contest) and the copy is committed together
    with the register; if the recording fails the copy is removed again (from disk and index, unless HEAD has it).
    An existing `<task>-attempt-<n>.md` with other content is never overwritten: the row is then recorded without
    `report` and a line goes to stderr. A plain failure (also a lock that stays busy for `attempts.LOCK_TIMEOUT`
    seconds) is logged on stderr and gives None; `KeyboardInterrupt` and `SystemExit` are
    re-raised after the restore.

    Args:
        contest: Path of the contest checkout.
        row: The attempt to record.
        message: Commit message; default `chore(house): attempt register <task> attempt <n>` with the number used.
        report_from: The worker's report to keep as this attempt's copy; None keeps `row.report` as given.

    Returns:
        The attempt number the row was recorded with; None when it was not recorded (a line is printed to stderr).
    """
    try:
        with register.lock(contest):
            return _record_locked(contest, row, message, report_from)
    except (TimeoutError, OSError) as e:
        print(f"house: attempt register not updated for {row.task} attempt {row.n}: {e}", file=sys.stderr)
        return None


def _record_locked(contest: Path, row: register.Row, message: str | None,
                   report_from: Path | None = None) -> int | None:
    """`record_attempt` with the register lock held."""
    try:
        if guard.register_changed(contest):
            print(f"house: attempt register {guard.REGISTER} differs from HEAD; not recording {row.task} "
                  f"attempt {row.n}", file=sys.stderr)
            return None
        n = max(row.n, register.next_n(contest / ".house", row.task))
    except Exception as e:  # noqa: BLE001 — recording trouble never changes what the caller does
        print(f"house: attempt register not updated for {row.task} attempt {row.n}: {e!r}", file=sys.stderr)
        return None
    row = replace(row, n=n)
    copy: Path | None = None
    try:
        if report_from is not None:
            copy = contest / ".house" / "reports" / f"{row.task}-attempt-{n}.md"
            try:
                data = _report_copy_bytes(report_from)
                if data is None:
                    print(f"house: report of {row.task} attempt {n} is no regular file; not kept", file=sys.stderr)
                    copy = None
                elif copy.is_symlink() or copy.parent.is_symlink() or (
                        copy.exists() and copy.read_bytes() != data):
                    # never overwrite a copy that belongs to something else (a lost row, a hand edit)
                    print(f"house: {copy.name} exists with other content; {row.task} attempt {n} recorded "
                          "without its report", file=sys.stderr)
                    copy = None
                else:
                    copy.parent.mkdir(parents=True, exist_ok=True)
                    copy.write_bytes(data)
                    row = replace(row, report=copy.relative_to(contest).as_posix())
            except OSError as e:  # the copy is a bonus: the row is recorded without it
                print(f"house: report of {row.task} attempt {n} not kept: {e!r}", file=sys.stderr)
                if copy is not None:
                    copy.unlink(missing_ok=True)
                copy = None
        path = register.record(contest / ".house", row)
        _commit_paths(contest, [path] + ([copy] if copy else []),
                      message or f"chore(house): attempt register {row.task} attempt {n}")
        if guard.register_changed(contest):
            raise RuntimeError("the register commit did not go through")
        return n
    except BaseException as e:
        try:
            guard.restore_register(contest)
        except Exception:  # noqa: BLE001, S110 — nothing more to do; the gate will report the register
            pass
        if copy is not None:
            rel = copy.relative_to(contest).as_posix()
            if _git(contest, "cat-file", "-e", f"HEAD:{rel}").returncode != 0:
                # the commit did not happen: the copy is no more than an orphan, staged or not
                _git(contest, "rm", "--cached", "-q", "--ignore-unmatch", "--", rel)
                copy.unlink(missing_ok=True)
        if not isinstance(e, Exception):
            raise
        print(f"house: attempt register not updated for {row.task} attempt {row.n}: {e!r}", file=sys.stderr)
        return None


def _report_copy_bytes(path: Path) -> bytes | None:
    """The bytes to keep as a report copy: the file's content passed through `guard.redact`, at most
    `MAX_REPORT_COPY` bytes (cut at a line end, then a last line `… truncated`). Byte for byte the file when nothing
    was redacted or cut. None when `path` is a symlink or no regular file (it is never followed).

    Raises:
        OSError: The file cannot be read.
    """
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, "rb") as f:
            fd = -1
            raw = f.read(MAX_REPORT_COPY + _COPY_MARGIN + 1)
    finally:
        if fd >= 0:
            os.close(fd)
    more = len(raw) > MAX_REPORT_COPY + _COPY_MARGIN  # the file goes on: its last, partial line is dropped
    if more:
        raw = raw[:raw.rfind(b"\n") + 1]
    text = raw.decode("utf-8", "surrogateescape")
    red = guard.redact(text)
    data = raw if red == text else red.encode("utf-8", "surrogateescape")
    if len(data) > MAX_REPORT_COPY:
        cut = data[:MAX_REPORT_COPY]
        data, more = cut[:cut.rfind(b"\n") + 1] or cut, True
    if more:
        data += (b"" if not data or data.endswith(b"\n") else b"\n") + "\u2026 truncated\n".encode()
    return data


def rev(contest: Path, ref: str) -> str:
    """Commit id `ref` points to, or "" when git cannot tell."""
    r = _git(contest, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return r.stdout.strip() if r.returncode == 0 else ""


def merge_base(contest: Path, ref: str) -> str:
    """Merge-base of `ref` with the contest's HEAD, or "" when git cannot tell."""
    r = _git(contest, "merge-base", "HEAD", ref)
    return r.stdout.strip() if r.returncode == 0 else ""


def _codex_thread(contest: Path, log: str) -> str:
    """Read the Codex `thread_id` from the first `thread.started` event of the worker log ("" when there is none)."""
    try:
        with (contest / log).open(encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get("type") == "thread.started":
                    thread = event.get("thread_id")
                    return thread if isinstance(thread, str) else ""
    except OSError:
        pass
    return ""


@dataclass
class _Started:
    """What the night knows about a running attempt when it starts (the register row is written when it ends)."""
    n: int
    start: str
    agent: str = "coder"
    tool: str = "claude"
    base: str = ""
    questions: list[str] | None = None  # Q-### ids in the main checkout's QUESTIONS.md at the start; None if unknown
    report_hash: str | None = None  # hash of the main checkout's reports/<task>.md at the start; None when absent
    main_merged: str = ""  # how the merge of main into the task branch went first (`Attempt.main_merged`)


def _question_ids(house: Path) -> list[str] | None:
    """Q-### ids in the main checkout's `.house/QUESTIONS.md` (workers write there through `$HOUSE_DIR`), in file
    order; `[]` when the file does not exist, None when it cannot be read or parsed."""
    from . import questions
    path = house / "QUESTIONS.md"
    if not path.exists():
        return []
    try:
        return [q.id for q in questions.parse(path.read_text(encoding="utf-8", errors="replace"))]
    except Exception:  # noqa: BLE001 — an unreadable file only makes the fact unknown
        return None


def prepare_worktree(contest: Path, task_id: str) -> Path:
    """Create (or reuse) the task's git worktree and mark the task `doing`.

    Args:
        contest: Path of the contest checkout.
        task_id: Task id, e.g. "T001".

    Returns:
        The worktree path, `<contest>/.worktrees/<branch without task/>`. The branch is the task's `branch`
        field, else `task/<id>-<slug>`; it is created when it does not exist yet. The task file gets
        `status: doing`, `owner` (its `agent`, default "coder") and `branch`, and is committed on its own.

    Raises:
        RuntimeError: `git worktree add` failed.
        FileNotFoundError: The task file does not exist.
    """
    house = contest / ".house"
    task = checks.load_task(house, task_id)
    slug = task.path.stem.split("-", 1)[1] if "-" in task.path.stem else task_id.lower()
    branch = str(task.meta.get("branch") or f"task/{task_id}-{slug}")
    wt = contest / ".worktrees" / branch.removeprefix("task/")
    if not wt.is_dir():
        exists = _git(contest, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0
        args = [str(wt), branch] if exists else [str(wt), "-b", branch]
        r = _git(contest, "worktree", "add", "-q", *args)
        if r.returncode != 0:
            raise RuntimeError(f"git worktree add failed for {task_id}: {r.stderr.strip()}")
    _set(task, status="doing", owner=str(task.meta.get("agent") or "coder"), branch=branch)
    _commit_paths(contest, [task.path], f"chore(house): night starts {task_id}")
    return wt


class _NoWorker(Exception):
    """The attempt is not given to a worker (`outcome` and the message say why); `paths` are the conflicted files a
    human resolves when the outcome is `blocked-by-worker`."""

    def __init__(self, outcome: str, message: str, paths: list[str] | None = None):
        super().__init__(message)
        self.outcome, self.paths = outcome, paths or []


def _merging(worktree: Path) -> bool:
    """True when a merge is in progress in `worktree` (`MERGE_HEAD` exists)."""
    return _git(worktree, "rev-parse", "-q", "--verify", "MERGE_HEAD").returncode == 0


def _unmerged(worktree: Path) -> list[str]:
    """Paths git still marks as unmerged in `worktree`, sorted."""
    out = _git(worktree, "-c", "core.quotePath=false", "diff", "--name-only", "-z", "--diff-filter=U").stdout
    return sorted({p for p in out.split("\0") if p})


def _content_sha(path: Path) -> str | None:
    """sha256 of a file's bytes (of the target text for a symlink); None when there is no such file."""
    try:
        data = os.readlink(path).encode() if path.is_symlink() else path.read_bytes() if path.is_file() else None
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest() if data is not None else None


def _blob(worktree: Path, spec: str) -> str | None:
    """Object id of `spec` (`HEAD:<path>`, `:<path>`) in `worktree`; None when it does not exist."""
    r = _git(worktree, "rev-parse", "-q", "--verify", spec)
    return r.stdout.strip() if r.returncode == 0 else None


def _record_path(contest: Path, task_id: str) -> Path:
    """`.house/tmp/night/<task>.merge.json`: the conflicted paths of the task's merge of main (outside git)."""
    return contest / ".house" / "tmp" / "night" / f"{task_id}.merge.json"


def _load_record(contest: Path, task_id: str) -> dict | None:
    """The saved merge record of a task (`{"main": sha, "paths": {path: sha256 or None}}`), or None."""
    try:
        data = json.loads(_record_path(contest, task_id).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("paths"), dict) else None


def _save_record(contest: Path, task_id: str, record: dict) -> None:
    """Write the merge record of a task (see `_load_record`); errors are ignored (the record is rebuilt)."""
    try:
        path = _record_path(contest, task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2) + "\n")
    except OSError:
        pass


def _drop_record(contest: Path, task_id: str) -> None:
    """Remove the merge record of a task once its merge is committed."""
    _record_path(contest, task_id).unlink(missing_ok=True)


def merge_record(contest: Path, task_id: str, worktree: Path) -> dict:
    """The record of the merge of main in progress in `worktree`: the saved one when it belongs to this merge (its
    `main` is the current `MERGE_HEAD`), else one built from the paths still unmerged now (each with its current
    content hash, so it counts as untouched)."""
    record = _load_record(contest, task_id)
    if record is None or record.get("main") != (_blob(worktree, "MERGE_HEAD") or ""):
        record = {"main": _blob(worktree, "MERGE_HEAD") or "",
                  "paths": {p: _content_sha(worktree / p) for p in _unmerged(worktree)}}
        _save_record(contest, task_id, record)
    return record


def after_conflict(house: Path, task_id: str, n: int) -> bool:
    """Tell whether the attempt before attempt `n` at a task failed on a merge conflict with the main line.

    Args:
        house: The contest's `.house/` directory.
        task_id: Task id.
        n: Register number of the attempt about to start.

    Returns:
        True when the register row with the highest number below `n` has `reason == "conflict"` (a conflict
        `merge-refused`, or any attempt that ended with a merge of main still in progress).
    """
    last = None
    for row in register.rows(house, task_id):
        if row.n < n and (last is None or row.n >= last.n):
            last = row
    return last is not None and last.reason == "conflict"


def _brought_nothing(worktree: Path) -> bool:
    """True when the staged merge changes nothing outside `.house/` against HEAD (main had no new work)."""
    out = _git(worktree, "-c", "core.quotePath=false", "diff", "--cached", "--name-only", "-z", "HEAD").stdout
    return all(p == ".house" or p.startswith(".house/") for p in out.split("\0") if p)


def merge_main(contest: Path, worktree: Path, task_id: str) -> tuple[str, list[str], dict]:
    """Bring the contest's main line into the task worktree without committing (`git merge --no-ff --no-commit`).

    Only the task branch receives main; main never receives anything here. A merge already in progress (left by an
    earlier attempt, which may have resolved part of it) is kept as it is, never aborted. Otherwise main is merged
    afresh: a clean merge stays staged (the driver's end-of-attempt commit records it), a conflict leaves its markers
    for the worker. The conflicted paths and the hash of each file right after the merge are saved in
    `.house/tmp/night/<task>.merge.json` (see `merge_record`), so the driver can tell later which ones the worker
    really resolved.

    Args:
        contest: Path of the contest checkout; its current branch is the main line.
        worktree: The task worktree.
        task_id: Task id (names the merge record).

    Returns:
        `(note, conflicted paths still unresolved, record)`. The note is `in progress` (a kept merge), `clean`,
        `conflict in N file(s)`, `up to date` (main brought nothing outside `.house/`) or `failed: <git's first
        line>` (nothing was merged, e.g. local changes in the way).
    """
    if _merging(worktree):
        record = merge_record(contest, task_id, worktree)
        return "in progress", conflicts_left(contest, worktree, record), record
    main = _git(contest, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if not main or main == "HEAD":
        main = rev(contest, "HEAD")
    r = _git(worktree, "merge", "--no-ff", "--no-commit", main)
    paths = _unmerged(worktree)
    record = {"main": rev(contest, main), "paths": {p: _content_sha(worktree / p) for p in paths}}
    if paths:
        _save_record(contest, task_id, record)
        return f"conflict in {len(paths)} file{'s' if len(paths) > 1 else ''}", paths, record
    if _merging(worktree):
        _save_record(contest, task_id, record)
        return ("up to date" if _brought_nothing(worktree) else "clean"), [], record
    if r.returncode == 0:
        return "up to date", [], record
    first = next((ln.strip() for ln in (r.stderr + r.stdout).splitlines() if ln.strip()), "unknown error")
    return f"failed: {_line(guard.redact(first), 200)}", [], record


def conflicts_left(contest: Path, worktree: Path, record: dict) -> list[str]:
    """Paths of the merge of main in progress that are not resolved yet, sorted.

    A recorded conflicted path still unmerged in the index counts as resolved only when it is a both-modified or
    both-added conflict (`UU`, `AA`; the driver stages it) whose content changed since `merge_main` (an untouched
    binary file would silently keep "ours"). A recorded path the worker staged (no longer unmerged) counts as
    resolved, a deliberate "keep ours" included. Either way it must hold no conflict marker line main lacks
    (`guard.conflict_markers`) and must not be a protected, `.house/` or secret-looking path (the driver never
    commits those). Unmerged paths missing from the record and tracked files whose added lines (`git diff HEAD`)
    hold such a marker are unresolved too.

    Args:
        contest: Path of the contest checkout (source of the protected paths).
        worktree: The task worktree.
        record: The merge record (`merge_record`).
    """
    protected = guard.protected_paths(contest)
    unmerged = set(_unmerged(worktree))
    codes = {path: code for code, path in guard._status(worktree)}
    marked = guard.conflict_markers(worktree, record.get("main") or None, ("HEAD",), list(record["paths"]))
    left = {p for p in unmerged if p not in record["paths"]} | marked
    for rel, before in record["paths"].items():
        path = worktree / rel
        resolved = rel not in unmerged or (codes.get(rel) in ("UU", "AA") and _content_sha(path) != before)
        risky = rel == ".house" or rel.startswith(".house/") or rel in protected or guard._secret_file(path)
        if risky or not resolved:
            left.add(rel)
    return sorted(left)


def _block_for_human(contest: Path, task_id: str, branch: str, paths: list[str]) -> None:
    """Set the task `blocked` and add a PROGRESS line naming the conflicted paths and the branch to resolve them on
    (committed by `_after_failure`)."""
    house = contest / ".house"
    _set(checks.load_task(house, task_id), status="blocked")
    with (house / "PROGRESS.md").open("a") as f:
        f.write(f"- {datetime.now():%Y-%m-%d %H:%M} · house · {task_id} zablokowane: konflikt scalania main "
                f"w plikach chronionych lub wyglądających na sekret ({', '.join(paths)}); resolve by hand on branch "
                f"{branch}\n")


def _mark_merge(attempt: "Attempt", worktree: Path | None) -> None:
    """A merge of main still in progress when the attempt ends stays for the next attempt: the attempt gets
    `reason` `conflict` and `main_merged` `in progress`."""
    try:
        if worktree is not None and worktree.is_dir() and _merging(worktree):
            attempt.reason, attempt.main_merged = "conflict", "in progress"
    except OSError:
        pass


def brief(contest: Path, task_id: str, worktree: Path, previous: str | None = None,
          handoff: str | None = None) -> str:
    """Build the prompt for a worker session.

    Args:
        contest: Path of the contest checkout.
        task_id: Task id, e.g. "T001".
        worktree: The task worktree the worker runs in.
        previous: Outcome text of the last failed attempt, if any; the first 600 characters are added so the worker
            fixes that first (used only when `handoff` is empty).
        handoff: The `next_brief` text of this attempt; when given it follows the first-attempt text under a
            `Hand-off from earlier attempts:` line and replaces `previous`.

    Returns:
        The brief: which task file to implement, the HOUSE_DIR, the GREEN condition (`house check ... --worktree`),
        the `status: review` hand-off, and the rule to raise a Q-### and block instead of waiting for a human.
    """
    task = checks.load_task(contest / ".house", task_id)
    text = (f"You are working unattended on contest `{contest.name}` (house night). Implement exactly one task: "
            f"{task.path} . HOUSE_DIR={contest / '.house'} . Your worktree: {worktree} . "
            f"Follow your agent instructions. Finish when `{HOUSE_ROOT / 'bin/house'} check {contest.name} "
            f"{task_id} --worktree {worktree}` prints GREEN: commit your work on the task branch, write the report, "
            "set `status: review` in the task file. A decision only a human can take: write a Q-### per "
            "protocols/questions.md, set `status: blocked` and stop. Never wait for a human.")
    if handoff:
        text += f"\n\nHand-off from earlier attempts:\n{handoff}"
    elif previous:
        text += f" Previous attempt failed: {previous[:600]} . Fix that first."
    return text


_NO_TEXT = {"none", "brak", "-", "n/a"}
# `Next action: x`, `**Next action:** x`, `**Next action**: x`, or the bare words alone on a line; not `Next actions to try:`
_ACTION = re.compile(r"^\s*(?:[-*]\s+)?\**\s*Next action\s*(?::\s*\**|\**\s*:|\**\s*$)\s*(.*?)\s*$",
                     re.IGNORECASE)
# any line that starts with the words `Done when` (with or without a colon): code writes that condition
_DONE_WHEN = re.compile(r"^\s*(?:[-*]\s+)?\**\s*Done when\b", re.IGNORECASE)
_FENCE_LINE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_DEAD_MAX = 2500  # Dead ends budget, used only when the whole brief would pass `MAX_BRIEF`
_DEAD_ENTRY_MAX = 600  # a budgeted dead end longer than this is cut (with `…`), not dropped
_GATE_MAX = 1500  # the gate's verbatim output tails under Errors and fixes, all criteria together
_UNVERIFIED = re.compile(r"^\s*(?:#{1,6}\s*)?\**\s*Unverified\s*\**\s*:?\s*\**\s*$", re.IGNORECASE)
_SUBHEADING = re.compile(r"^\s*#{3,6}\s+\S")
_BULLET = re.compile(r"^([-*+]|\d+[.)])(?:\s+(.*)|\s*$)")
_SUFFIX = re.compile(r"\s*\(worker report, attempt \d+\)\s*$")
_PLACEHOLDER = re.compile(r"^<[^<>]*>$")
_SUMMARY = re.compile(r"^\d+ files? changed")
_GOAL_MAX, _NEXT_MAX, _ACTION_MAX = 1500, 2500, 600
_KEY_SECTIONS = ("Goal", "Next", "Dead ends")  # a report copy with none of them counts as no report
# outcomes set by the gate or the night (not by the worker): the next action is to fix what they say
_GATE_OUTCOMES = ("merge-refused", "reverted", "no-commits", "error", "tampered", "timeout")
_PORCELAIN_MAX = 20


def _cap(text: str, limit: int) -> str:
    """Cut `text` to `limit` characters, the last one `…` when something was cut."""
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _close_fences(text: str) -> str:
    """`text`, with the closing line of a code fence added when the last fence is still open."""
    fence = None
    for line in text.splitlines():
        m = _FENCE_LINE.match(line)
        if not m:
            continue
        marker = m.group(1)
        if fence is None:
            fence = marker
        elif marker[0] == fence[0] and len(marker) >= len(fence) and line.strip() == marker:
            fence = None
    return text if fence is None else f"{text}\n{fence}"


def _cap_text(text: str, limit: int) -> str:
    """`_cap` for Markdown: a code fence that the cut left open is closed."""
    return _close_fences(_cap(text, limit))


def _line(text: str, limit: int = 300) -> str:
    """`text` on one line (whitespace collapsed), cut to `limit` characters."""
    return _cap(" ".join(text.split()), limit)


def _code(text: str) -> str:
    """`text` as Markdown inline code that a backtick inside it cannot end (``` `` a `b` `` ``` when needed)."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    if not longest:
        return f"`{text}`"
    ticks = "`" * (longest + 1)
    return f"{ticks} {text} {ticks}"


def _time(text: str | None) -> float | None:
    """An ISO time as a POSIX time (a time without offset is local); None when it cannot be read."""
    try:
        return datetime.fromisoformat(text).timestamp() if text else None
    except (TypeError, ValueError):
        return None


def _decoded(data: bytes) -> str:
    """Report bytes as redacted text, at most `MAX_REPORT_COPY` bytes of them."""
    return guard.redact(data[:MAX_REPORT_COPY].decode("utf-8", "replace"))


def _head_report(contest: Path, rel: str) -> str | None:
    """The report copy `rel` as committed at HEAD (`git cat-file`), redacted; None when HEAD has no regular file
    there or `rel` does not name a path inside the contest."""
    parts = PurePosixPath(rel).parts if rel else ()
    if not parts or rel.startswith("/") or ".." in parts:
        return None
    ls = _git(contest, "ls-tree", "-z", "HEAD", "--", rel)
    meta, _, path = ls.stdout.rstrip("\0").partition("\t")
    fields = meta.split()
    if ls.returncode != 0 or path != rel or len(fields) != 3 or fields[0] not in ("100644", "100755") \
            or fields[1] != "blob":
        return None
    blob = subprocess.run(["git", "cat-file", "blob", fields[2]], cwd=contest, capture_output=True, check=False)
    return _decoded(blob.stdout) if blob.returncode == 0 else None


def _live_report(contest: Path, row: register.Row) -> str | None:
    """The main checkout's `reports/<task>.md` for an attempt that kept no copy, when it changed after the attempt
    started (mtime later than `row.start`); redacted and capped as a copy would be. None otherwise."""
    start = _time(row.start)
    path = contest / ".house" / "reports" / f"{row.task}.md"
    try:
        if start is None or path.is_symlink() or not path.is_file() or path.stat().st_mtime <= start:
            return None
        data = _report_copy_bytes(path)
    except OSError:
        return None
    return None if data is None else _decoded(data)


@dataclass
class _Report:
    """The sections of one earlier attempt's report, as the brief reads them."""
    k: int
    sections: dict[str, str]
    origin: str  # how the brief names it: `the report of attempt 2`, `the live report <path> ...`
    live: bool = False


def _reports(contest: Path, earlier: dict[int, register.Row], prev: int) -> list[_Report]:
    """Reports of the earlier attempts, newest first. A copy is read from HEAD; a copy without any of Goal, Next and
    Dead ends counts as none. The previous attempt without a copy falls back to the live report (`_live_report`)."""
    out = []
    for k in sorted(earlier, reverse=True):
        row, live = earlier[k], False
        text = _head_report(contest, row.report) if row.report else None
        if text is None and k == prev and not row.report:
            text, live = _live_report(contest, row), True
        if text is None:
            continue
        sections = handoff.report_sections(text)
        if not any(_text_of(sections.get(h)) for h in _KEY_SECTIONS):
            continue
        origin = (f"the live report {contest / '.house' / 'reports' / f'{row.task}.md'} (changed during attempt {k}, "
                  "which kept no copy)") if live else f"the report of attempt {k}"
        out.append(_Report(k, sections, origin, live))
    return out


def _text_of(body: str | None) -> str:
    """A report section body, or "" when it is missing or only a `none` marker."""
    body = (body or "").strip()
    return "" if body.lower().strip("*_. -") in _NO_TEXT else _close_fences(body)


def _unverified(sections: dict[str, str]) -> str:
    """The `### Unverified` part of the report's `Critical context` ("" when there is none)."""
    lines: list[str] = []
    inside = False
    for line in sections.get("Critical context", "").splitlines():
        if _UNVERIFIED.match(line):
            inside = True
        elif inside and _SUBHEADING.match(line):
            break
        elif inside:
            lines.append(line)
    return _text_of("\n".join(lines))


def _is_action(action: str) -> bool:
    """False for an empty action, a `none`/`n/a`/`-` marker or a template placeholder `<...>` (markup stripped)."""
    plain = action.strip().strip("*_`").strip()
    return bool(plain) and plain.lower().rstrip(".") not in _NO_TEXT and not _PLACEHOLDER.match(plain)


def _next_parts(body: str) -> tuple[str, str]:
    """Split a report's `Next` body into (text, action): the first real `Next action:` line is taken out (one that
    `_is_action` refuses is dropped) and so is any line starting with `Done when` (code writes that condition)."""
    action, rest = "", []
    for line in body.splitlines():
        m = _ACTION.match(line)
        if m and not action and _is_action(m.group(1)):
            action = m.group(1)
        elif not m and not _DONE_WHEN.match(line):
            rest.append(line)
    return "\n".join(rest).strip(), action


def _dead_end_entries(body: str) -> list[list[str]]:
    """Split a `Dead ends` body into entries, each a list of lines (the first one without its list marker).

    An entry is an unindented list item with its continuation lines (a bare `-` takes its first continuation line as
    its text), or, in a body without list items, a paragraph. A `###` sub-heading is no entry and ends the one
    before it. A code fence keeps its lines (blank ones too) in the entry it opened in; one left open is closed.
    """
    entries: list[list[str]] = []
    current: list[str] | None = None
    fence: str | None = None
    indent = 0  # indentation of the opening fence line, taken off the lines inside it
    for line in body.splitlines():
        m = _FENCE_LINE.match(line)
        if fence is not None:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and line.strip() == m.group(1):
                fence = None
                current.append(line.strip())
            else:
                current.append(line.rstrip()[indent:] if not line[:indent].strip() else line.strip())
            continue
        if not line.strip() or _SUBHEADING.match(line):
            current = None
            continue
        bullet = _BULLET.match(line)
        if bullet or current is None:
            current = [(bullet.group(2) or "") if bullet else line.strip()]
            entries.append(current)
        elif current == [""]:
            current[0] = line.strip()
        else:
            current.append(line.strip())
        if m:
            fence, indent = m.group(1), len(line) - len(line.lstrip())
    return [e for e in entries if e and e[0]]


def _dead_end_items(reports: list[_Report]) -> list[tuple[int, list[str]]]:
    """`(attempt, lines)` of every dead end of the reports, newest attempt first.

    An entry that repeats one of an older report (whitespace normalised, list marker and a copied `(worker report,
    attempt N)` suffix ignored) stays only with the oldest attempt it appears in.
    """
    first: set[str] = set()
    per: dict[int, list[list[str]]] = {}
    for rep in sorted(reports, key=lambda r: r.k):
        for lines in _dead_end_entries(_text_of(rep.sections.get("Dead ends"))):
            lines[-1] = _SUFFIX.sub("", lines[-1])
            if not lines[-1].strip() and len(lines) > 1:
                lines.pop()
            key = " ".join(" ".join(lines).split())
            if key and key not in first:
                first.add(key)
                per.setdefault(rep.k, []).append(lines)
    return [(k, lines) for k in sorted(per, reverse=True) for lines in per[k]]


def _dead_end_item(k: int, lines: list[str], limit: int | None) -> str:
    """One dead end as a list item with its ` (worker report, attempt k)` suffix; cut to `limit` when given."""
    text = "\n".join(lines)
    lines = (_cap_text(text, limit) if limit else _close_fences(text)).splitlines()
    suffix = f"(worker report, attempt {k})"
    if _FENCE_LINE.match(lines[-1]):  # the fence must stay closed: the suffix goes on its own line
        lines.append(suffix)
    else:
        lines[-1] += " " + suffix
    return "- " + "\n  ".join(lines)


def _dead_ends(items: list[tuple[int, list[str]]], contest: Path, task_id: str, budget: int | None = None) -> str:
    """The Dead ends section from `_dead_end_items`, all of them when `budget` is None.

    With a budget (characters), each entry is cut to `_DEAD_ENTRY_MAX`; the first entry of every attempt is kept
    (space is reserved for it), the others in order while they fit (a long one that does not fit is skipped, shorter
    later ones still go in); a last line counts the entries left out and names the report copies.
    """
    if budget is None:
        return "\n".join(_dead_end_item(k, lines, None) for k, lines in items) or "none"
    rendered = [(k, _dead_end_item(k, lines, _DEAD_ENTRY_MAX)) for k, lines in items]
    firsts = {next(i for i, (kk, _) in enumerate(rendered) if kk == k) for k, _ in rendered}
    reserve = sum(len(rendered[i][1]) + 1 for i in firsts)
    out: list[str] = []
    size = left = 0
    for i, (_, item) in enumerate(rendered):
        if i in firsts:
            reserve -= len(item) + 1
        if i in firsts or size + len(item) + 1 + reserve <= budget:
            out.append(item)
            size += len(item) + 1
        else:
            left += 1
    if left:
        out.append(f"… {left} more dead ends in {contest / '.house' / 'reports'}/{task_id}-attempt-*.md")
    return "\n".join(out) or "none"


def _fenced(text: str) -> str:
    """`text` in a code fence long enough not to be closed by a fence inside it."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}\n{text}\n{fence}"


@dataclass
class _Gate:
    """What the contest's checks file says about the last check: when, and the failing criteria's output tails."""
    path: Path
    at: str = ""
    failing: list[tuple[str, str]] = field(default_factory=list)  # (`AC-1 (exit 1)`, redacted output tail)


def _gate(contest: Path, task_id: str) -> _Gate:
    """Read `<contest>/.house/checks/<task>.json`: the time of the last recorded check (the gate's `at`, else the
    newest `ran_at`, else the file's mtime) and the output tail of each criterion whose last result failed."""
    path = contest / ".house" / "checks" / f"{task_id}.json"
    gate = _Gate(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        results = data.get("results") or {}
        g = data.get("gate") if isinstance(data.get("gate"), dict) else {}
        times = [r.get("ran_at") for r in results.values() if isinstance(r, dict) and isinstance(r.get("ran_at"), str)]
        if results or g.get("at"):
            gate.at = str(g.get("at") or max(times, default="") or
                          datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"))
        gate.failing = [(str(cid) + (f" (exit {r['exit']})" if isinstance(r.get("exit"), int) else ""),
                         guard.redact(str(r.get("output_tail") or "")))
                        for cid, r in results.items() if isinstance(r, dict) and r.get("passes") is False]
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return gate


def _gate_errors(gate: _Gate) -> list[str]:
    """The gate's failing output tails as the newest Errors and fixes entry (at most `_GATE_MAX` characters of
    output, each tail keeping its end)."""
    if not gate.failing:
        return []
    shown = gate.failing[:_GATE_MAX // 150]
    each = max(150, _GATE_MAX // len(shown))
    out = [f"last check at {gate.at}: output of the failing criteria, verbatim (source: {gate.path})"]
    for cid, tail in shown:
        tail = tail.strip() or "(no output)"
        if len(tail) > each:
            tail = "…" + tail[-(each - 1):]
        out.append(f"{cid}:\n{_fenced(tail)}")
    if len(gate.failing) > len(shown):
        out.append(f"\u2026 {len(gate.failing) - len(shown)} more failing criteria in {gate.path}")
    return out


def _paths_text(paths: list[str], limit: int = 10) -> str:
    """`a, b, c` for at most `limit` paths, then `and N more`."""
    shown = ", ".join(_line(guard.redact(p), 200) for p in paths[:limit])
    return shown + (f" and {len(paths) - limit} more" if len(paths) > limit else "")


def _pending_merge(contest: Path, task_id: str, worktree: Path) -> list[str] | None:
    """The merge of main in progress in `worktree`: its paths still unresolved (`conflicts_left`; `[]` when it is
    clean), or None when no merge is in progress."""
    if not worktree.is_dir() or not _merging(worktree):
        return None
    return conflicts_left(contest, worktree, merge_record(contest, task_id, worktree))


def _merge_action(pending: list[str], check: str, nothing_new: bool = False) -> str:
    """The next action while a merge of main is in progress: resolve the listed conflicts, or only re-run the check
    (saying so when main brought nothing new); the driver commits the merge either way."""
    if pending:
        return (f"main is merged into the worktree with conflicts in {_paths_text(pending)}: resolve the conflict "
                "markers there keeping both sides' behaviour, mark each file resolved with `git add <path>`, then "
                f"run {check}; do not commit, the driver commits the merge")
    if nothing_new:
        return (f"main was already merged; the conflict must come from main moving again; re-run {check}; do not "
                "commit, the driver commits the merge")
    return (f"main merged cleanly (staged, not committed); re-run {check} and fix what it reports; do not commit, "
            "the driver commits the merge")


def _observed(contest: Path, row: register.Row | None, k: int, facts: evidence.Facts, gate: _Gate,
              worktree: Path | None = None, pending: list[str] | None = None) -> str:
    """The `Observed` list: facts of attempt `k` computed by code, each with its source; with a merge of main in
    progress in `worktree` (`pending`, see `_pending_merge`) a line says how that merge stands."""
    register_path = contest / ".house" / "attempts.jsonl"
    merge_line = []
    if pending is not None:
        state = (f"conflict in {len(pending)} file{'s' if len(pending) > 1 else ''} ({_paths_text(pending)}), "
                 "merge in progress" if pending else "clean (staged)")
        merge_line = [f"- main merged into the worktree before this attempt: {state}, not committed "
                      f"(source: `git status` in {worktree})"]
    if row is None:
        return "\n".join([f"- no register row for attempt {k}: no facts (source: {register_path})", *merge_line])
    log = f"log of attempt {k}"
    ended = f"- attempt {k} ended `{row.outcome or 'unknown'}`" + (f" ({row.reason})" if row.reason else "")
    if row.message:
        ended += f": {_line(guard.redact(row.message))}"
    last = f"- last check: {facts.last_check}"
    if gate.at:
        last += f" at {gate.at}"
        at, start = _time(gate.at), _time(row.start)
        if at is not None and start is not None and at < start:
            last += f", before attempt {k} started"
    lines = [f"{ended} (source: {register_path})",
             f"- check after last edit: {facts.check_after_last_edit} (source: {log})",
             f"{last} (source: {gate.path})"]
    lines += [f"- guard violation: {_line(v)} (source: guard check of the worktree)" for v in facts.violations]
    if facts.questions_added:
        lines.append(f"- questions added: {', '.join(facts.questions_added)} (source: {register_path})")
    lines += [f"- repeated failing command: {_code(_line(cmd, 200))} failed {n} times (source: {log})"
              for cmd, n in facts.repeats]
    lines += [f"- command failed here and in an earlier attempt: {_code(_line(cmd, 200))} (source: {log})"
              for cmd in facts.dead_end_repeats or []]
    if facts.test_edits:
        lines.append(f"- test or protected files edited: {', '.join(facts.test_edits)} (source: {log})")
    if facts.unbacked_claim:
        lines.append(f"- the report claims a pass the log does not back (source: {log} and report)")
    return "\n".join(lines + merge_line)


def _branch_work(contest: Path, task_id: str, worktree: Path) -> tuple[list[str], str, list[str]] | None:
    """`(diffstat lines, diff command, porcelain lines)` of the task branch against its merge-base with the contest's
    HEAD, plus the worktree's uncommitted state; None when the branch cannot be found."""
    branch = ""
    if worktree.is_dir():
        r = _git(worktree, "rev-parse", "--abbrev-ref", "HEAD")
        branch = r.stdout.strip() if r.returncode == 0 else ""
    if not branch or branch == "HEAD":
        try:
            branch = str(checks.load_task(contest / ".house", task_id).meta.get("branch") or "")
        except Exception:  # noqa: BLE001
            branch = ""
    tip = rev(contest, branch) if branch else ""
    base = merge_base(contest, tip) if tip else ""
    if not base:
        return None
    r = _git(contest, "diff", "--stat", f"{base}..{tip}")
    if r.returncode != 0:
        return None
    stat = [guard.redact(ln.strip()) for ln in r.stdout.splitlines() if ln.strip()]
    porcelain: list[str] = []
    if worktree.is_dir():
        p = _git(worktree, "status", "--porcelain")
        porcelain = [guard.redact(ln) for ln in p.stdout.splitlines() if ln.strip()] if p.returncode == 0 else []
    return stat, f"git diff --stat {base}..{branch}", porcelain


def _done(contest: Path, task_id: str, worktree: Path, facts: evidence.Facts, row: register.Row | None) -> str:
    """The `Done` list: what the task branch holds against its merge-base with the main line, and the worktree's
    uncommitted edits; each file `verified` only when the check after the last code change of the previous attempt
    was GREEN. The diffstat's summary line is no file. Without the branch: the previous attempt's own diffstat.
    A stat longer than `evidence.LONG_DIFFSTAT` lines is shown by its summary and the command that lists it."""
    status = "verified" if facts.check_after_last_edit == "yes, GREEN" else "not verified"
    work = _branch_work(contest, task_id, worktree)
    if work is None:
        stat = [ln.strip() for ln in facts.diffstat.splitlines() if ln.strip()]
        command = f"git diff --stat {row.base}..{row.head}" if row is not None else ""
        porcelain: list[str] = []
        intro: list[str] = []
    else:
        stat, command, porcelain = work
        if len(stat) > evidence.LONG_DIFFSTAT:
            stat = stat[-1:]
        intro = ["The worktree already holds the work of earlier attempts.",
                 f"(source: {_code(command)} in {contest}, `git status --porcelain` in {worktree})"]
    if not stat and not porcelain:
        return "none"
    out = list(intro)
    out += [ln if _SUMMARY.match(ln) else f"- {ln} ({status})" for ln in stat]
    if len(stat) == 1 and _SUMMARY.match(stat[0]) and command:
        out.append(f"- full list: {command}")
    out += [f"- uncommitted: {_line(ln, 200)} ({status})" for ln in porcelain[:_PORCELAIN_MAX]]
    if len(porcelain) > _PORCELAIN_MAX:
        out.append(f"- … {len(porcelain) - _PORCELAIN_MAX} more uncommitted paths: git status --porcelain")
    return "\n".join(out)


def _check_command(contest: Path, task_id: str, tool: str) -> str:
    """How the next action names the check: the done-when line for Claude, the `house check` command for Codex."""
    if tool == "codex":
        return f"`{HOUSE_ROOT / 'bin/house'} check {contest.name} {task_id} --worktree .`"
    return "the Done-when check"


def _default_action(contest: Path, task_id: str, tool: str, gate_failing: bool, has_errors: bool) -> str:
    """The next action written by code when the report gives none: run the check, starting from the gate's failing
    criteria, else from the newest error."""
    text = f"run {_check_command(contest, task_id, tool)} and fix what it reports"
    if gate_failing:
        return text + ", starting from the failing criteria of the last check under Errors and fixes"
    if has_errors:
        return text + ", starting from the newest error under Errors and fixes"
    return text


def _done_when(contest: Path, task_id: str, worktree: Path, tool: str) -> str:
    """The completion condition written by code (the report's own wording is never used)."""
    if tool == "codex":
        return ("the acceptance criteria pass in the working directory and the report is written "
                "(do not commit; the driver does)")
    return (f"`{HOUSE_ROOT / 'bin/house'} check {contest.name} {task_id} --worktree {worktree}` prints GREEN, "
            "then set `status: review`")


def _full_record(contest: Path, task_id: str, row: register.Row | None, prev: int) -> str:
    """The brief's last line: the previous attempt's log and the `house evidence` command for it."""
    if prev < 1:
        return "Full record: none (no earlier attempt)"
    log = str(contest / row.log) if row is not None and row.log else f"no log of attempt {prev}"
    return f"Full record: {log}; {HOUSE_ROOT / 'bin/house'} evidence {contest.name} {task_id} --attempt {prev}"


def _fit(head: str, rest: str, tail: str, contest: Path, task_id: str) -> str:
    """Join `head` (intro, Goal, Next: never cut), `rest` and the `tail` line (the full-record pointer, always last).

    Over `MAX_BRIEF` characters `rest` is cut from the end, at a line end, a code fence the cut left open is closed,
    and a line before `tail` says what happened."""
    text = head + rest
    if len(text) + len(tail) + 1 <= MAX_BRIEF:
        return f"{text}\n{tail}"
    note = f"(cut to {MAX_BRIEF} characters; full reports: {contest / '.house' / 'reports'}/{task_id}-attempt-*.md)"
    keep = MAX_BRIEF - len(note) - len(tail) - 3
    for _ in range(5):  # closing a fence adds a line; shrink until everything fits
        cut = text[:max(keep, 0)]
        end = cut.rfind("\n")
        if end >= len(head.rstrip()):
            cut = cut[:end]
        out = f"{_close_fences(cut.rstrip())}\n\n{note}\n{tail}"
        if len(out) <= MAX_BRIEF:
            return out
        keep -= len(out) - MAX_BRIEF + 1
    return text[:MAX_BRIEF - len(tail) - 1] + "\n" + tail


def next_brief(contest: Path, task_id: str, worktree: Path, n: int, tool: str) -> str:
    """Build the hand-off brief of attempt `n` (2 or 3) at a task, from what the earlier attempts left.

    The brief has the eight sections of `handoff.SECTIONS` in order, each filled or `none`, and ends with a
    `Full record:` line (the previous attempt's log and its `house evidence` command). Every pointer is absolute: the
    worker runs in the worktree, whose `.house/` is a stale snapshot.

    Reports: the copies named by the register rows (`Row.report`) are read as committed at HEAD; a copy without any
    of Goal, Next and Dead ends counts as no report; the previous attempt without a copy falls back to the live
    `reports/<task>.md` when it changed after the attempt started (named as such). From the newest report: Next,
    Constraints and human decisions, Decisions and facts, and Unverified; Goal from the newest report that has one,
    else the task title. When attempt n-1 has no report the placeholder `no report from attempt <n-1>` stands
    there, and an older report's text is named by its attempt. The `Next action:` of the report is used unless it
    is empty, a `none` marker or a template placeholder; with a merge of main in progress in the worktree (see
    `merge_main`) code writes the action: resolve the conflict markers in the listed paths and `git add` them, or,
    when main merged cleanly, only re-run the check; either way the worker does not commit, the driver commits the
    merge. Otherwise after an outcome of the gate (`_GATE_OUTCOMES`) code writes the action (fix that outcome
    first); else code writes a default (run the check, starting from the gate's failing criteria or the newest
    error). The task file path and the `Done when:` line are written by code.

    From code: Observed (A1 facts of attempt n-1 with its `reason`, the last check with its time, the state of a
    merge of main in progress), Errors and fixes (the gate's failing output tails from the checks file first, then
    the error tails of the log, newest first) and Done (the task branch against its merge-base plus uncommitted
    edits, `verified` only when the check after the last edit was GREEN). Dead ends come from all reports, newest
    attempt first, each entry once with the oldest attempt it appears in. Only report text, the checks file and A1
    facts enter, all through `guard.redact`.

    Over `MAX_BRIEF` characters the Dead ends get a budget first (`_dead_ends`), then the brief is cut from the end
    (Done first, then Errors and fixes, ...); the intro, Goal, the whole Next section and the `Full record:` line
    always stay, and a line says what was cut. Never raises.

    Args:
        contest: Path of the contest checkout.
        task_id: Task id, e.g. "T001".
        worktree: The task worktree the worker runs in.
        n: Number of the attempt the brief is for; earlier attempts are the register rows with a lower number.
        tool: `claude` or `codex`: decides the done-when line and how the next action names the check.

    Returns:
        The brief, at most `MAX_BRIEF` characters.
    """
    done_when = _done_when(contest, task_id, worktree, tool)
    try:
        return _next_brief(contest, task_id, worktree, n, tool, done_when)
    except Exception:  # noqa: BLE001 — a failing worker brief must never stop the night
        return _minimal_brief(contest, task_id, worktree, n, tool, done_when)


def _minimal_brief(contest: Path, task_id: str, worktree: Path, n: int, tool: str, done_when: str) -> str:
    """The brief of last resort: the eight sections empty, the task file and the done-when line in Next."""
    try:
        task_file = str(checks.load_task(contest / ".house", task_id).path)
    except Exception:  # noqa: BLE001
        task_file = f"{contest / '.house' / 'tasks'}/{task_id}-*.md"
    out = []
    for header, _ in handoff.SECTIONS:
        if header == "Next":
            body = (f"Task file: {task_file}\nWorktree: {worktree}\n"
                    f"Next action: {_default_action(contest, task_id, tool, False, True)}\nDone when: {done_when}")
        elif header == "Critical context":
            body = "### Observed\nnone\n### Unverified\nnone"
        else:
            body = "none"
        out.append(f"## {header}\n{body}")
    tail = (f"Full record: {contest / '.house' / 'attempts.jsonl'}; {HOUSE_ROOT / 'bin/house'} evidence "
            f"{contest.name} {task_id} --attempt {max(n - 1, 1)}")
    return ("\n\n".join(out)[:MAX_BRIEF - len(tail) - 2] + "\n\n" + tail)[:MAX_BRIEF]


def _next_brief(contest: Path, task_id: str, worktree: Path, n: int, tool: str, done_when: str) -> str:
    house = contest / ".house"
    earlier: dict[int, register.Row] = {}
    for row in register.rows(house, task_id):
        if row.n < n:
            earlier[row.n] = row  # the last row of a number wins, as in `attempts.find`
    prev = n - 1
    reports = _reports(contest, earlier, prev)
    last = reports[0] if reports else None
    row = earlier.get(prev)
    facts = _facts(contest, row)
    gate = _gate(contest, task_id)
    try:
        task = checks.load_task(house, task_id)
        task_file, title = str(task.path), _line(guard.redact(str(task.meta.get("title") or "")), 300)
    except Exception:  # noqa: BLE001 — no task file: say where it should be
        task_file, title = f"{house / 'tasks'}/{task_id}-*.md", ""

    has_prev = any(r.k == prev for r in reports)
    missing = "none" if prev < 1 else f"no report from attempt {prev}"

    def lead(rep: _Report, name: str) -> str:
        """The line naming where a section's text comes from, when that is not attempt n-1's own copy."""
        if rep.k == prev:
            return f"from {rep.origin}:" if rep.live else ""
        why = f"the report of attempt {prev} has no {name}" if has_prev else missing
        return f"{why}; from {rep.origin}:"

    def from_report(name: str, limit: int | None = None) -> str:
        if last is None:
            return missing
        body = _text_of(last.sections.get(name)) or "none"
        src = lead(last, name)
        return (src + "\n" if src else "") + (_cap_text(body, limit) if limit else body)

    goal_rep = next((r for r in reports if _text_of(r.sections.get("Goal"))), None)
    if goal_rep is not None:
        src = lead(goal_rep, "Goal")
        goal = (src + "\n" if src else "") + _cap_text(_text_of(goal_rep.sections.get("Goal")), _GOAL_MAX)
    else:
        why = missing if not has_prev else "no Goal in the earlier reports"
        goal = f"{why}; task title: {title or 'unknown'} (source: {task_file})"

    next_text, action = _next_parts(_text_of(last.sections.get("Next"))) if last else ("", "")
    tails = list(reversed(facts.error_tails))  # evidence keeps them in log order; the newest goes first
    outcome = row.outcome if row is not None else ""
    pending = _pending_merge(contest, task_id, worktree)
    if pending is not None or outcome in _GATE_OUTCOMES:
        if action:
            next_text = (next_text + "\n" if next_text else "") + \
                f"The earlier worker planned (worker report, attempt {last.k}): {_line(action, _ACTION_MAX)}"
        if pending is not None:
            action = _merge_action(pending, _check_command(contest, task_id, tool),
                                   not pending and _brought_nothing(worktree))
        else:
            action = (f"attempt {prev} ended `{outcome}` (see Observed and Errors and fixes): fix that first, then "
                      f"run {_check_command(contest, task_id, tool)}")
    else:
        action = _cap(action, _ACTION_MAX) or _default_action(contest, task_id, tool, bool(gate.failing),
                                                              bool(tails))
    next_lines = [src] if last is not None and (src := lead(last, "Next")) else []
    next_lines += [_cap_text(next_text, _NEXT_MAX)] if next_text else ([missing] if last is None else [])
    next_lines += [f"Task file: {task_file}", f"Worktree: {worktree}", f"Next action: {action}",
                   f"Done when: {done_when}"]
    if last is None:
        unverified = missing
    else:
        unverified = _unverified(last.sections)
        unverified = ((lead(last, "Unverified") + "\n") if lead(last, "Unverified") and unverified else "") + \
            (unverified or "none")
    errors = _gate_errors(gate)
    if tails:
        log = str(contest / row.log) if row is not None and row.log else f"log of attempt {prev}"
        errors += [f"log of attempt {prev}, newest first (source: {log})"] + [_fenced(t) for t in tails]

    head = (f"# Hand-off for attempt {n} at {task_id} (contest `{contest.name}`)\n\n"
            "Earlier attempts did not finish. Facts under Observed, Errors and fixes and Done were measured by code; "
            "everything marked `worker report` or listed under Unverified is an earlier worker's claim: check it "
            f"before relying on it. House files are in {house} (HOUSE_DIR); the `.house` copy inside the worktree is "
            "stale.\n\n"
            f"## Goal\n{goal}\n\n"
            "## Next\n" + "\n".join(next_lines) + "\n")
    items = _dead_end_items(reports)

    def rest(budget: int | None) -> str:
        return "\n".join([
            "\n## Critical context\n### Observed\n" + _observed(contest, row, prev, facts, gate, worktree, pending),
            f"### Unverified\n{unverified}",
            f"\n## Constraints and human decisions\n{from_report('Constraints and human decisions')}",
            f"\n## Dead ends\n{_dead_ends(items, contest, task_id, budget)}",
            f"\n## Decisions and facts\n{from_report('Decisions and facts')}",
            "\n## Errors and fixes\n" + ("\n".join(errors) or "none"),
            f"\n## Done\n{_done(contest, task_id, worktree, facts, row)}\n"])

    tail = _full_record(contest, task_id, row, prev)
    body = rest(None)
    if len(head) + len(body) + len(tail) + 1 > MAX_BRIEF:
        body = rest(_DEAD_MAX)
    return _fit(head, body, tail, contest, task_id)


def _facts(contest: Path, row: register.Row | None) -> evidence.Facts:
    """A1 facts of an attempt (state, then log); defaults without a row. Never raises."""
    if row is None:
        return evidence.Facts()
    try:
        return evidence.log_facts(contest, row, evidence.state_facts(contest, row))
    except Exception:  # noqa: BLE001
        return evidence.Facts()


def uses_codex(task: checks.Task, tools: dict) -> bool:
    """True when `worker_command` runs this task through `bin/codex-task` (a `coder-codex` task, Codex available)."""
    return task.meta.get("agent") == "coder-codex" and bool(tools.get("codex"))


def _write_brief(contest: Path, task: checks.Task, worktree: Path, tools: dict, n_reg: int, log: Path) -> Path | None:
    """Write the hand-off brief of attempt `n_reg` next to the attempt's log (`<log stem>.brief.md`).

    Only from attempt 2 on, and only when the register has an earlier row of the task (numbering continues across
    nights, so the first attempt of a later night counts). The done-when line follows the tool that is actually
    launched. Never raises.

    Returns:
        The file's path, or None when there is no earlier attempt or the brief could not be written.
    """
    try:
        if n_reg < 2 or not any(r.n < n_reg for r in register.rows(contest / ".house", task.id)):
            return None
        tool = "codex" if uses_codex(task, tools) else "claude"
        text = next_brief(contest, task.id, worktree, n_reg, tool)
        path = log.with_suffix(".brief.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n")
        return path
    except Exception:  # noqa: BLE001 — the brief must never fail an attempt; the old failure text stays as fallback
        return None


def worker_command(contest: Path, task: checks.Task, worktree: Path, tools: dict,
                   previous: str | None = None, session_id: str | None = None,
                   json_log: Path | None = None, brief_file: Path | None = None) -> list[str]:
    """Build the command line of a worker session.

    Args:
        contest: Path of the contest checkout (its `spec.yaml` supplies `night.model`, default "sonnet").
        task: The loaded task (`checks.load_task`).
        worktree: The task worktree; passed to `bin/codex-task` or named in the brief.
        tools: Contents of `.house/tools.json`; a truthy `codex` entry enables Codex workers.
        previous: Outcome text of the last failed attempt, forwarded to `brief`.
        session_id: Claude session id (a UUID); when given, the Claude command streams its events
            (`--session-id <id> --output-format stream-json --verbose`) to stdout.
        json_log: File that receives the Codex JSON event stream; when given, `bin/codex-task` gets `--json-log`.
        brief_file: A hand-off brief written by night (`next_brief`): Codex gets `--brief-file <file>`; the Claude
            prompt gets the file's content (unreadable: the `previous` text is used instead).

    Returns:
        `[bin/codex-task, (--json-log, <file>,) (--brief-file, <file>,) <task file>, <worktree>]` for a
        `coder-codex` task when Codex is available, otherwise `claude -p --agent coder --model <model> ...
        (stream flags) <brief>` with the contest's local settings, auto permission mode and no permission prompts;
        the brief is always the last argument.
    """
    if uses_codex(task, tools):
        log_args = ["--json-log", str(json_log)] if json_log else []
        brief_args = ["--brief-file", str(brief_file)] if brief_file else []
        return [str(HOUSE_ROOT / "bin/codex-task"), *log_args, *brief_args, str(task.path), str(worktree)]
    handoff = None
    if brief_file:
        try:
            handoff = brief_file.read_text().strip() or None
        except OSError:
            handoff = None
    model = specmod.get(_spec(contest), "night.model") or "sonnet"
    stream = ["--session-id", session_id, "--output-format", "stream-json", "--verbose"] if session_id else []
    return ["claude", "-p", "--agent", "coder", "--model", str(model),
            "--settings", str(contest / ".claude/settings.local.json"),
            "--permission-mode", "auto", "--permission-prompts", "none", *stream,
            brief(contest, task.id, worktree, previous, handoff)]


class Worker:
    """A worker session in its own process group (so a timeout kills everything it started).

    Attributes:
        proc: The underlying `subprocess.Popen`.
        pid: Process id (also the process group id).
    """

    def __init__(self, cmd: list[str], cwd: Path, log: Path):
        """Start the worker process.

        Args:
            cmd: Command line to execute.
            cwd: Working directory (the task worktree). `HOUSE_DIR` is set to the contest's `.house/` when the
                contest can be found from it.
            log: File that receives stdout and stderr (overwritten; parent directories are created). stdin is /dev/null.
        """
        log.parent.mkdir(parents=True, exist_ok=True)
        self._log = log.open("w")
        house = guard.contest_root(cwd)
        env = {**os.environ, **({"HOUSE_DIR": str(house / ".house")} if house else {})}
        self.proc = subprocess.Popen(cmd, cwd=cwd, stdout=self._log, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, start_new_session=True, env=env)
        self.pid = self.proc.pid

    def poll(self):
        """Check whether the worker has finished.

        Returns:
            The exit code, or None while it is still running. The log file is closed once it has finished.
        """
        code = self.proc.poll()
        if code is not None:
            self._log.close()
        return code

    def wait(self, timeout=None):
        """Block until the worker finishes.

        Args:
            timeout: Seconds to wait; None waits forever.

        Returns:
            The exit code.

        Raises:
            subprocess.TimeoutExpired: The worker is still running after `timeout` seconds.
        """
        return self.proc.wait(timeout)

    def kill(self):
        """Kill the worker's whole process group with SIGKILL, reap it and close the log."""
        try:
            os.killpg(self.proc.pid, sig_mod.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        self.proc.wait()
        self._log.close()


def _launch(cmd: list[str], cwd: Path, log: Path) -> Worker:
    """Start a `Worker`; the default launcher of `run_night` (tests pass a fake one)."""
    return Worker(cmd, cwd, log)


# --- the loop ----------------------------------------------------------------------------------------------

def _ahead(contest: Path, branch: str) -> int:
    """Count commits on `branch` that main (HEAD) does not have yet; 0 when git fails."""
    r = _git(contest, "rev-list", "--count", f"HEAD..{branch}")
    return int(r.stdout.strip() or 0) if r.returncode == 0 else 0


def _signal_round(contest: Path) -> None:
    """Process Signal answers and question deadlines for this contest; any error is swallowed."""
    try:
        from . import inbox
        from .signal import from_config
        s = from_config()
        if s is not None:
            everyone = inbox.all_running() or [contest]
            if contest.resolve() not in {c.resolve() for c in everyone}:
                everyone.append(contest)
            inbox.process(everyone, s, scope=[contest])
            inbox.deadlines(contest, s)
    except Exception:  # noqa: BLE001, S110 — Signal trouble never stops the night
        pass


def _open_questions(house: Path) -> list[str]:
    """List `<id> <title>` of open questions in `.house/QUESTIONS.md`."""
    from . import questions
    path = house / "QUESTIONS.md"
    if not path.exists():
        return []
    return [f"{q.id} {q.title}" for q in questions.parse(path.read_text()) if q.status == "open"]


def _evidence_lines(contest: Path, a: Attempt) -> list[str]:
    """Markdown bullets with the short evidence of one attempt and the command for the full form.

    Never raises: when the register has no row for the attempt or the facts cannot be computed, the facts are
    replaced by one `evidence unavailable` line (the command stays).
    """
    command = f"- `house evidence {contest.name} {a.task} --attempt {a.n}`"
    try:
        row = register.find(contest / ".house", a.task, a.n)
        if row is None:
            raise LookupError("no register row")
        facts = evidence.log_facts(contest, row, evidence.state_facts(contest, row))
        return [f"- {line}" for line in evidence.render_short(facts, row)] + [command]
    except Exception:  # noqa: BLE001 — evidence must never break the morning report
        return ["- evidence unavailable", command]


def _write_report(contest: Path, rep: NightReport, started: datetime, tampering: list[str]) -> Path:
    """Write the morning report `.house/reports/night-<date>.md` and append a line to `.house/PROGRESS.md`.

    The report has the attempts table; below it, per attempt, `T### attempt N:` with the short evidence form
    (`evidence.render_short` over the register row's state and log facts, or `evidence unavailable`) and the
    command `house evidence <contest> T### --attempt N`; then Signal counts, open questions and tampering.

    Args:
        contest: Path of the contest checkout.
        rep: The finished night report.
        started: Start time of the run (used in the file name).
        tampering: Tampering messages to list in the report.

    Returns:
        The path of the report file.
    """
    house = contest / ".house"
    path = house / "reports" / f"night-{started:%Y-%m-%d-%H%M}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = [a for a in rep.attempts if a.outcome == "merged"]
    lines = [f"# Night run {started:%Y-%m-%d %H:%M} – {datetime.now():%H:%M}", "",
             f"Stopped because: {rep.stopped_because}", f"Merged: {len(merged)} · attempts: {len(rep.attempts)}", "",
             "| Task | Attempt | Outcome | Details | Log |", "|---|---|---|---|---|"]
    for a in rep.attempts:
        details = (f"main merged: {a.main_merged} · " if a.main_merged else "") + a.message
        details = details.replace("\n", " ").replace("|", "/")[:300]
        outcome = f"{a.outcome} ({a.reason})" if a.reason else a.outcome
        lines.append(f"| {a.task} | {a.n} | {outcome} | {details} | `{a.log}` |")
    if rep.attempts:
        lines += ["", "## Evidence (short form)"]
        for a in rep.attempts:
            lines += ["", f"{a.task} attempt {a.n}:", *_evidence_lines(contest, a)]
    if rep.signal_seq is not None:
        try:
            from .signalread import night_line
            lines += ["", night_line(rep.signal_seq)]
        except Exception:  # noqa: BLE001, S110 — the report must be written even if the store is unreadable
            pass
    questions = _open_questions(house)
    lines += ["", "## Open questions", *([f"- {q}" for q in questions] or ["- none"])]
    if tampering:
        heading = ("## Tampering found (uncommitted changes stay as they are and `house merge` refuses them; "
                   "a register edit is restored from HEAD)")
        lines += ["", heading, *[f"- {t}" for t in tampering]]
    path.write_text("\n".join(lines) + "\n")
    with (house / "PROGRESS.md").open("a") as f:
        f.write(f"- {datetime.now():%Y-%m-%d %H:%M} · house · noc: {len(merged)} zadań scalonych, "
                f"{len(rep.attempts) - len(merged)} nieudanych prób, koniec: {rep.stopped_because} · "
                f"{path.relative_to(contest)}\n")
    return path


def _after_failure(contest: Path, tid: str, n: int, outcome: str, n_reg: int | None = None) -> None:
    """Status after a failed attempt: 3rd failure → blocked; doing/review → todo; blocked (by the worker or a
    reverted merge) stays blocked.

    `n` is the night's own count of the task's attempts (it drives MAX_ATTEMPTS); `n_reg` is the attempt's register
    number, used in the commit message (default `n`).
    """
    house = contest / ".house"
    task = checks.load_task(house, tid)
    progress = house / "PROGRESS.md"
    if n >= MAX_ATTEMPTS and task.meta.get("status") != "blocked":
        _set(task, status="blocked")
        with progress.open("a") as f:
            f.write(f"- {datetime.now():%Y-%m-%d %H:%M} · house · {tid} zablokowane po {n} nieudanych próbach "
                    "w nocy\n")
    elif task.meta.get("status") in ("doing", "review"):
        _set(task, status="todo")
    _commit_paths(contest, [task.path, progress],
                  f"chore(house): night {tid} attempt {n if n_reg is None else n_reg} {outcome}")


def run_night(contest: Path, hours: float = 8, max_tasks: int | None = None,
              launcher: Callable | None = None, now: Callable = time.monotonic,
              sleep: Callable = time.sleep) -> NightReport:
    """Run the unattended loop: start workers, gate their results through `house merge`, write the morning report.

    Args:
        contest: Path of the contest checkout.
        hours: Time limit of the whole run in hours (default 8). No new task is started after it.
        max_tasks: Stop after starting this many attempts; None means no limit.
        launcher: Callable `(cmd, cwd, log) -> worker` that starts a worker; defaults to `_launch` (a real process).
            `log` receives the worker's stdout and stderr: the `.jsonl` stream for a Claude worker, `<name>.out` for
            a Codex wrapper (whose stream goes to the `.jsonl` named in `cmd`). The worker needs `poll()` and
            `kill()`. Replace in tests.
        now: Monotonic clock in seconds; replace in tests.
        sleep: Sleep function used between polls (POLL_SECONDS); replace in tests.

    Returns:
        A `NightReport`. Parallelism is `limits.max_parallel_agents` (default 3), the per-task limit is
        `night.task_minutes` (default 45). After MAX_ATTEMPTS failures a task becomes `blocked`; the run stops after
        MAX_CONSECUTIVE_FAILURES failed attempts in a row (a worker that blocks its task does not count). On exit,
        surviving workers are killed, their tasks go back to `todo`, the report is written and committed, and a
        Signal message is sent (errors there are ignored). Ctrl+C ends the run cleanly with "interrupted".

        Every finished attempt (merged, failed, could not start, interrupted) is recorded in the attempt register
        `.house/attempts.jsonl` and that file is committed at once (`record_attempt`). The register numbers
        (`next_n`, continuing over nights and sources, and never below the last number this night used for the
        task + 1, so log names never repeat after a lost row) name the worker log and are the `Attempt.n` shown in
        the report and the night's commit messages; the night's own per-task counter only drives MAX_ATTEMPTS.
        `base` is the task branch head after `prepare_worktree`, `head` the branch head when the worker finished
        (read before the merge removes the branch), `questions` the Q-### ids added to the main checkout's
        QUESTIONS.md between the start and the end of the attempt. The morning report adds the short evidence of
        every attempt (see `_write_report`). The worker's report (`$HOUSE_DIR/reports/<task>.md`) is hashed when the
        attempt starts; when it exists and differs after the attempt (any outcome: timeout, killed, interrupted,
        could not start), it is copied (redacted, capped, never through a symlink; see `record_attempt`) to
        `.house/reports/<task>-attempt-<n>.md` with the number the
        row was recorded with, named in the row's `report` (relative to the contest), and committed with the
        register row; an unchanged or missing report gives no copy and `report == ""`.

        When the task's previous register row has `reason == "conflict"` (or a merge is in progress in the
        worktree), main is merged into the worktree before the worker starts (`merge_main`, which keeps a merge in
        progress; the row gets `main_merged`). A merge that fails (`failed: ...`) ends the attempt as `error`
        without a worker. A conflict in a protected, `.house/` or secret-looking file is not given to a worker: a
        fresh merge is aborted (a kept one stays), the task becomes `blocked`, PROGRESS names the paths and the branch
        to resolve them on, and the attempt is recorded as `blocked-by-worker` (reason `conflict`). Whatever way an
        attempt ends (timeout, tampering, an exception, an interrupt included), a merge still in progress stays and
        the row gets `reason` `conflict` and `main_merged` `in progress`. Errors while finishing (recording, healing
        the register, listing tampering) are printed on stderr and never prevent the morning report.
    """
    launcher = launcher or _launch
    house = contest / ".house"
    spec = _spec(contest)
    limit = int(specmod.get(spec, "limits.max_parallel_agents") or 3)
    task_seconds = float(specmod.get(spec, "night.task_minutes", DEFAULT_TASK_MINUTES)) * 60
    tools = _tools(contest)
    started_at, t0 = datetime.now(), now()
    rep = NightReport()
    try:
        from . import signalstore
        rep.signal_seq = signalstore.last_seq()
    except Exception:  # noqa: BLE001, S110 — Signal trouble never stops the night
        pass
    running: dict[str, tuple] = {}  # task → (proc, start, worktree, .jsonl log, attempt n, branch, session)
    started: dict[str, _Started] = {}  # task → register facts of the attempt in progress
    attempts: dict[str, int] = {}
    merges: dict[str, dict] = {}  # task → record of the merge of main made before its attempt (`merge_main`)
    last_n: dict[str, int] = {}  # task → highest register number used this night (rows may be lost)
    last_failure: dict[str, str] = {}
    consecutive, started_count = 0, 0

    restored: list[str] = []  # register hand-edits put back to HEAD; listed in the report

    def heal() -> None:
        """Put a hand-edited register back to HEAD (house writes it only through `record_attempt`), so a forged row
        can neither be committed by house nor inflate the attempt numbers; the report lists it. Runs under the
        register lock; any error (a busy lock included) is printed on stderr and the night goes on."""
        try:
            with register.lock(contest):
                if not guard.register_changed(contest):
                    return
                guard.restore_register(contest)
        except Exception as e:  # noqa: BLE001 — healing trouble never ends the night or loses the report
            print(f"house: attempt register not checked: {e!r}", file=sys.stderr)
            return
        note = f"{guard.REGISTER} changed outside house (restored from HEAD)"
        if note not in restored:
            restored.append(note)

    def record(tid: str, attempt: Attempt, head: str = "") -> None:
        """Write the register row of a finished attempt and commit it; the attempt is added to the report.

        `head` is the branch head read before the attempt was evaluated; `attempt.head` (the commit the gate
        evaluated) wins when `_finish` got that far.
        """
        rep.attempts.append(attempt)
        head = attempt.head or head
        heal()
        st = started.pop(tid, None) or _Started(attempt.n, now_iso())
        session = attempt.session
        if st.tool == "codex" and not session and attempt.log:
            session = _codex_thread(contest, attempt.log)
        asked = None
        if st.questions is not None and (now_ids := _question_ids(house)) is not None:
            asked = [q for q in now_ids if q not in st.questions]
        attempt.main_merged = attempt.main_merged or st.main_merged
        row = register.Row(task=tid, n=attempt.n, source="night", agent=st.agent, tool=st.tool, session=session,
                           log=attempt.log, start=st.start, end=now_iso(), base=st.base, head=head,
                           outcome=attempt.outcome, message=attempt.message, questions=asked,
                           reason=attempt.reason, main_merged=attempt.main_merged)
        report = house / "reports" / f"{tid}.md"
        now_hash = _file_hash(report)
        changed = now_hash is not None and now_hash != st.report_hash
        n = record_attempt(contest, row, report_from=report if changed else None)
        if n is not None and n != attempt.n:  # the number was taken meanwhile (an interactive merge)
            attempt.n = n
            last_n[tid] = max(last_n.get(tid, 0), n)

    def failed(tid: str, n: int, attempt: Attempt, head: str = "") -> None:
        nonlocal consecutive
        record(tid, attempt, head)
        attempts[tid] = n
        last_failure[tid] = f"{attempt.outcome}: {attempt.message}"
        if attempt.outcome != "blocked-by-worker":
            consecutive += 1
        _after_failure(contest, tid, n, attempt.outcome, attempt.n)

    try:
        while True:
            stopping = ""
            if (house / "STOP").exists():
                stopping = "STOP file"
            elif now() - t0 >= hours * 3600:
                stopping = "time limit"
            elif max_tasks is not None and started_count >= max_tasks:
                stopping = f"--max-tasks {max_tasks} reached"
            _signal_round(contest)
            if not stopping:
                for tid in pick_ready(house, set(running), limit - len(running)):
                    if max_tasks is not None and started_count >= max_tasks:
                        break
                    n = attempts.get(tid, 0) + 1
                    heal()  # numbers come from HEAD's register, never from a forged working copy
                    n_reg = max(register.next_n(house, tid), last_n.get(tid, 0) + 1)
                    last_n[tid] = n_reg
                    started_count += 1
                    started[tid] = st = _Started(n_reg, now_iso(), questions=_question_ids(house),
                                                 report_hash=_file_hash(house / "reports" / f"{tid}.md"))
                    log = house / "tmp/night" / f"{started_at:%Y-%m-%d}" / f"{tid}-{started_at:%H%M%S}-{n_reg}.jsonl"
                    wt = None
                    try:
                        wt = prepare_worktree(contest, tid)
                        task = checks.load_task(house, tid)
                        st.agent = str(task.meta.get("agent") or "coder")
                        st.base = rev(contest, str(task.meta.get("branch")))
                        if after_conflict(house, tid, n_reg) or _merging(wt):
                            st.main_merged, conflicted, merges[tid] = merge_main(contest, wt, tid)
                            if st.main_merged.startswith("failed: "):
                                raise _NoWorker("error", f"merging main into the task branch {st.main_merged}; "
                                                "no worker started")
                            protected = guard.protected_paths(contest)
                            stuck = [p for p in conflicted if p in protected or p.startswith(".house/")
                                     or guard._secret_file(wt / p)]
                            if stuck:
                                if st.main_merged != "in progress":  # nothing of a worker's is in it yet
                                    _git(wt, "merge", "--abort")
                                raise _NoWorker("blocked-by-worker", "merging main into the task branch conflicts "
                                                f"in protected, .house/ or secret-looking file(s) {', '.join(stuck)}; "
                                                "a human decides (no worker is asked to edit them)", stuck)
                        session_id = str(uuid.uuid4())
                        brief_file = _write_brief(contest, task, wt, tools, n_reg, log)
                        cmd = worker_command(contest, task, wt, tools, last_failure.get(tid), session_id, log,
                                             brief_file)
                        codex = cmd[0].endswith("codex-task")
                        st.tool = "codex" if codex else "claude"
                        proc = launcher(cmd, wt, log.with_suffix(".out") if codex else log)
                        running[tid] = (proc, now(), wt, log, n, str(task.meta.get("branch")),
                                        "" if codex else session_id)
                    except _NoWorker as e:  # no worker: an error, or the task waits for a human
                        if e.outcome == "blocked-by-worker":
                            try:
                                _block_for_human(contest, tid, str(task.meta.get("branch")), e.paths)
                            except Exception as err:  # noqa: BLE001 — blocking trouble never ends the night
                                print(f"house: blocking {tid} failed: {err!r}", file=sys.stderr)
                        attempt = Attempt(tid, n_reg, e.outcome, str(e), "", reason="conflict")
                        _mark_merge(attempt, wt)
                        failed(tid, n, attempt)
                    except Exception as e:  # noqa: BLE001 — one broken task must not end the night
                        if st.agent == "coder":
                            try:
                                st.agent = str(checks.load_task(house, tid).meta.get("agent") or "coder")
                            except Exception:  # noqa: BLE001, S110 — the task file is unreadable: keep the default
                                pass
                        attempt = Attempt(tid, n_reg, "error", f"could not start: {e}", "")
                        _mark_merge(attempt, wt)
                        failed(tid, n, attempt)
            if not running and not rep.stopped_because:
                rep.stopped_because = stopping or (
                    f"{MAX_CONSECUTIVE_FAILURES} failed attempts in a row"
                    if consecutive >= MAX_CONSECUTIVE_FAILURES else "nothing ready")
                if not stopping and consecutive < MAX_CONSECUTIVE_FAILURES and pick_ready(house, set(), 1):
                    rep.stopped_because = ""
                    continue
                break
            for tid in list(running):
                proc, start, wt, log, n, branch, session = running[tid]
                code = proc.poll()
                timed_out = code is None and now() - start >= task_seconds
                if code is None and not timed_out:
                    continue
                if timed_out:
                    proc.kill()
                del running[tid]
                n_reg = started[tid].n
                head = rev(contest, branch)  # before `_finish`: the merge removes the branch
                try:
                    st = started[tid]
                    attempt = _finish(contest, tid, n_reg, branch, wt, log, timed_out, merges.pop(tid, None),
                                      st.base, st.main_merged)
                except Exception as e:  # noqa: BLE001
                    attempt = Attempt(tid, n_reg, "error", f"evaluating the attempt failed: {e!r}", str(log))
                attempt.session = session
                _mark_merge(attempt, wt)  # a merge of main left in progress stays for the next attempt
                if attempt.outcome == "merged":
                    record(tid, attempt, head)
                    attempts[tid] = n
                    consecutive = 0
                else:
                    failed(tid, n, attempt, head)
                if consecutive >= MAX_CONSECUTIVE_FAILURES:
                    rep.stopped_because = f"{MAX_CONSECUTIVE_FAILURES} failed attempts in a row"
                    break
            if rep.stopped_because:
                break
            if running:
                sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        rep.stopped_because = "interrupted"
    finally:
        for tid, (proc, _start, wt, log, _n, branch, session) in list(running.items()):
            try:
                proc.kill()
                attempt = Attempt(tid, started[tid].n, "interrupted", "session killed when the night ended",
                                  str(log), session)
                _mark_merge(attempt, wt)
                record(tid, attempt, rev(contest, branch))
                task = checks.load_task(house, tid)
                if task.meta.get("status") in ("doing", "review"):
                    _set(task, status="todo")
                _commit_paths(contest, [task.path], f"chore(house): night interrupted {tid}")
            except Exception as e:  # noqa: BLE001 — the morning report must still be written
                print(f"house: finishing {tid} failed: {e!r}", file=sys.stderr)
        rep.stopped_because = rep.stopped_because or "ended"
        try:
            found = guard.frozen_tampering(contest)
        except Exception as e:  # noqa: BLE001 — the morning report must still be written
            found = [f"tampering check failed: {e!r}"]
        tampering = [*found, *restored]
        rep.report_path = _write_report(contest, rep, started_at, tampering)
        _commit_paths(contest, [rep.report_path, house / "PROGRESS.md"], "chore(house): night report")
        try:
            from . import notify
            merged = sum(a.outcome == "merged" for a in rep.attempts)
            notify.send(contest, f"🌙 noc: {merged} scalonych, {len(rep.attempts) - merged} nieudanych prób; "
                        f"koniec: {rep.stopped_because}. Raport: {rep.report_path.name}", origin="night")
        except Exception:  # noqa: BLE001, S110
            pass
    return rep


def _restore_tampering(contest: Path, found: list[str]) -> None:
    """Restore what only house may write from HEAD: frozen criteria, the attempt register and hand-set merged/done
    statuses.

    Args:
        contest: Path of the contest checkout.
        found: Messages from `guard.frozen_tampering`; task files named in "T### set to ..." messages are restored.
    """
    _git(contest, "checkout", "HEAD", "--", ".house/checks")
    guard.restore_register(contest)
    for path in sorted((contest / ".house/tasks").glob("T*-*.md")):
        tid = path.name.split("-", 1)[0]
        if any(line.startswith(f"{tid} set to") for line in found):
            _git(contest, "checkout", "HEAD", "--", str(path.relative_to(contest)))


def _commit_leftovers(wt: Path, tid: str, contest: Path) -> str | None:
    """Commit what the worker left uncommitted, except secrets, protected files and `.house/` (they stay uncommitted,
    so the gate refuses the branch).

    With a merge of main in progress the commit is the merge commit (the merge's own staged changes are part of it;
    the caller has checked `conflicts_left`). Before it, the added lines of the staged diff (`git diff --cached
    HEAD`) are scanned for conflict markers main lacks (`guard.conflict_markers`); a hit, a failed `git commit` (a
    hook refusal, a path still unmerged) or a merge still in progress afterwards leaves the merge in progress and is
    returned as the problem.

    Args:
        wt: The task worktree.
        tid: Task id, used in the commit message.
        contest: Path of the contest checkout (source of the protected-paths list).

    Returns:
        Why the merge of main could not be committed, or None (always None without a merge in progress).
    """
    protected = guard.protected_paths(contest)
    keep = []
    for _, rel in guard._status(wt):
        risky = rel.startswith(".house/") or rel in protected or guard._secret_file(wt / rel)
        if not risky:
            keep.append(rel)
    merging = _merging(wt)  # a merge of main (staged by `merge_main`) is committed even without other leftovers
    if keep:
        _git(wt, "add", "-A", "--", *keep)
    if not merging:
        if keep:
            _git(wt, "commit", "-q", "-m", f"{tid}: uncommitted work at the end of the night session")
        return None
    markers = sorted(guard.conflict_markers(wt, "MERGE_HEAD", ("--cached", "HEAD")))
    if markers:
        return f"unresolved merge conflict: conflict markers in {_paths_text(markers)}; the merge stays in progress"
    r = _git(wt, "commit", "-q", "-m", f"{tid}: merge main into the task branch (night)")
    if r.returncode != 0 or _merging(wt):
        why = " ".join((r.stderr + r.stdout).split()) or "git commit failed"
        return f"the merge commit was refused: {_line(guard.redact(why), 300)}; the merge stays in progress"
    return None


def _undo_commits(wt: Path, base: str, main_ref: str) -> None:
    """Take the attempt's commits off the task branch (`git reset --soft <base>`: their work stays staged in the
    worktree) and, when `main_ref` is not in `base` yet, put the merge of main back in progress (`MERGE_HEAD`), so the
    next commit is again a merge of main."""
    _git(wt, "reset", "-q", "--soft", base)
    if main_ref and _git(wt, "merge-base", "--is-ancestor", main_ref, base).returncode != 0:
        # `git update-ref` refuses pseudorefs; git itself writes MERGE_HEAD as a plain file in the worktree's git dir
        path = Path(_git(wt, "rev-parse", "--path-format=absolute", "--git-path", "MERGE_HEAD").stdout.strip())
        path.write_text(rev(wt, main_ref) + "\n")


def _finish(contest: Path, tid: str, n: int, branch: str, wt: Path, log: Path, timed_out: bool,
            record: dict | None = None, base: str = "", main_merged: str = "") -> Attempt:
    """Evaluate a finished (or timed-out) worker and run the merge gate.

    Args:
        contest: Path of the contest checkout.
        tid: Task id.
        n: Attempt number.
        branch: Task branch name.
        wt: The task worktree.
        log: The worker's log file.
        timed_out: True when the worker was killed for exceeding the time limit.
        record: The merge record of the merge of main made before this attempt (`merge_main`); None loads the saved
            one.
        base: The task branch head when the attempt started.
        main_merged: How the merge of main before this attempt went (`Attempt.main_merged`); "" when none.

    Returns:
        An `Attempt`: "tampered" (restored from HEAD), "timeout", "blocked-by-worker", "no-commits", "error",
        "reverted" or "merge-refused" (from `merge.merge_task`; `reason` "conflict" for a merge conflict), or
        "merged". Otherwise the task is set to `review` and merged through `merge.merge_task`. A merge of main still
        in progress with unresolved paths (`conflicts_left`), or one whose commit fails (`_commit_leftovers`), gives
        "no-commits" with `reason` "conflict" and stays in progress (never aborted); a resolved one is committed as
        a merge commit with the leftovers. When this attempt merged main, the added lines of `git diff
        <merge-base(main, base)>..HEAD` and of the working tree are scanned for conflict markers
        (`guard.conflict_markers`) whoever made the commits; a hit undoes the attempt's commits (`_undo_commits`)
        and gives "no-commits" with `reason` "conflict" before the gate. From "no-commits" on, `Attempt.head` is the
        branch head after the worker's leftovers were committed (the commit the gate evaluates); earlier outcomes
        leave it empty.
    """
    house = contest / ".house"
    rel_log = str(log.relative_to(contest)) if log.is_relative_to(contest) else str(log)
    tampered = guard.frozen_tampering(contest)
    if tampered:
        _restore_tampering(contest, tampered)
        return Attempt(tid, n, "tampered", "; ".join(tampered) + " (restored from HEAD)", rel_log)
    if timed_out:
        return Attempt(tid, n, "timeout", "worker session exceeded night.task_minutes and was killed", rel_log)
    task = checks.load_task(house, tid)
    if task.meta.get("status") == "blocked":
        return Attempt(tid, n, "blocked-by-worker", "worker raised a question / blocked the task", rel_log)
    record = record or _load_record(contest, tid)
    if _merging(wt):  # never aborted: what the worker did stays for the next attempt
        record = merge_record(contest, tid, wt)
        left = conflicts_left(contest, wt, record)
        if left:
            return Attempt(tid, n, "no-commits", f"unresolved merge conflict in {_paths_text(left)}; the merge of "
                           "main stays in progress for the next attempt", rel_log, head=rev(contest, branch),
                           reason="conflict")
    problem = _commit_leftovers(wt, tid, contest)
    if problem:
        return Attempt(tid, n, "no-commits", problem, rel_log, head=rev(contest, branch), reason="conflict")
    if base and (main_merged or record):  # this attempt merged main: no marker may reach the gate, whoever committed
        main_ref = (record or {}).get("main") or rev(contest, "HEAD")
        fork = _git(wt, "merge-base", main_ref, base).stdout.strip() or base
        marked = guard.conflict_markers(wt, main_ref, (f"{fork}..HEAD",)) | guard.conflict_markers(wt, main_ref,
                                                                                                    ("HEAD",))
        if marked:
            _undo_commits(wt, base, main_ref)
            return Attempt(tid, n, "no-commits", f"unresolved merge conflict: conflict markers in "
                           f"{_paths_text(sorted(marked))} were committed; the commits of this attempt were undone "
                           "(their work stays staged) and the merge of main is in progress again", rel_log,
                           head=rev(contest, branch), reason="conflict")
        _drop_record(contest, tid)
    head = rev(contest, branch)  # what the gate evaluates; the merge removes the branch
    if _ahead(contest, branch) == 0:
        return Attempt(tid, n, "no-commits", "worker finished without committing anything", rel_log, head=head)
    _set(task, status="review")
    _commit_paths(contest, [task.path], f"chore(house): {tid} ready for review (night)")
    if str(task.meta.get("review")) == "always":
        from . import review
        res = review.review_task(contest, tid)
        if not res.ok:
            return Attempt(tid, n, "merge-refused", f"review failed: {res.why}", rel_log, head=head)
    try:
        out = merge.merge_task(contest, tid, wt)
    except Exception as e:  # noqa: BLE001
        return Attempt(tid, n, "error", f"merge crashed: {e!r}", rel_log, head=head)
    if out.ok:
        return Attempt(tid, n, "merged", out.message, rel_log, head=head)
    return Attempt(tid, n, "reverted" if out.reverted else "merge-refused", out.message, rel_log, head=head,
                   reason="conflict" if out.conflict else "")
