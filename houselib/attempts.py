"""Attempt register: one content-free row per attempt at a task, in `.house/attempts.jsonl` (tracked in git).

Used by `night` (every finished worker attempt) and `house merge` (every interactive gate run), and read by
`house evidence`. Rules: one row per attempt; attempt numbers continue across nights and across sources (a `merge`
row and a `night` row for the same task share one counter); a row holds facts and pointers only, never session
content (the transcript stays in the log the row points to); `log` and `report` are paths relative to the contest,
never absolute ones, and `message` holds no absolute contest or home path (they become `.` and `~`); `message` is
`guard.redact`ed and cut to `MAX_MESSAGE` characters; workers never write the file (house does, and `guard` refuses a
worker's edit); a line that is not a valid row is skipped when reading. Writers take `lock` (one contest-wide lock in
the git directory) around number allocation, append and commit, so a night and an interactive merge never write at
the same time.
"""
import contextlib
import fcntl
import hashlib
import json
import subprocess
import tempfile
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

from houselib import guard

FILE = "attempts.jsonl"
MAX_MESSAGE = 300
LOCK_FILE = "house-register.lock"
LOCK_TIMEOUT = 30.0  # seconds


@dataclass
class Row:
    """One attempt at a task.

    Attributes:
        task: Task id, for example `T001`.
        n: Attempt number of the task, counted over all nights and sources, from 1.
        source: `night` (a worker run) or `merge` (an interactive `house merge`).
        agent: Agent profile that ran the attempt.
        tool: `claude` or `codex`.
        session: Claude session id, or the Codex `thread_id` when known; empty for `merge`.
        log: Worker log path relative to the contest; empty for `merge`.
        start: ISO time the attempt started.
        end: ISO time the attempt ended.
        base: Commit the attempt started from.
        head: Commit the attempt ended at.
        outcome: How it ended, for example `merged`, `failed`, `interrupted`.
        message: Short human-readable note (redacted, absolute paths cut and the text cut when recorded).
        report: Report that covers the attempt, a path relative to the contest root like `log` (for example
            `.house/reports/T001-attempt-2.md`); empty until a later step fills it.
        questions: Q-### ids that appeared in the main checkout's `.house/QUESTIONS.md` during the attempt (workers
            write questions through `$HOUSE_DIR`, never on the task branch); `[]` for none; None when not recorded
            (rows written before the field existed).
        reason: Why the attempt failed, when code knows it: `conflict` for a `git merge` conflict with the main line
            (a conflict `merge-refused`, or a `no-commits` that left a merge of main unresolved); empty otherwise.
        main_merged: How the night's merge of the main line into the task branch went before this attempt
            started (`clean`, `conflict in N files`, `failed: ...`); empty when there was none.
    """
    task: str
    n: int
    source: str
    agent: str
    tool: str
    session: str = ""
    log: str = ""
    start: str = ""
    end: str = ""
    base: str = ""
    head: str = ""
    outcome: str = ""
    message: str = ""
    report: str = ""
    questions: list[str] | None = None
    reason: str = ""
    main_merged: str = ""


_STR_FIELDS = [f.name for f in fields(Row) if f.name not in ("n", "questions")]


def _parse(line: str) -> Row | None:
    """Parse one register line into a Row.

    Args:
        line: One line of `attempts.jsonl`.

    Returns:
        The row, or None when the line is not a JSON object with a string `task` and an integer `n`.
    """
    try:
        data = json.loads(line)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("task"), str):
        return None
    n = data.get("n")
    if not isinstance(n, int) or isinstance(n, bool):
        return None
    qs = data.get("questions")
    qs = list(qs) if isinstance(qs, list) and all(isinstance(q, str) for q in qs) else None
    return Row(task=data["task"], n=n, questions=qs,
               **{k: data[k] if isinstance(data.get(k), str) else "" for k in _STR_FIELDS if k != "task"})


def rows(house: Path, task: str | None = None) -> list[Row]:
    """Read the register, in file order.

    Args:
        house: The contest's `.house/` directory.
        task: Keep only the rows of this task id; None keeps all.

    Returns:
        The valid rows; broken lines and a missing file give nothing.
    """
    try:
        text = (house / FILE).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    # split on "\n" only: `str.splitlines` also splits on U+2028, U+0085 etc., which `json.dumps` leaves unescaped
    parsed = (_parse(line) for line in text.split("\n") if line.strip())
    return [r for r in parsed if r is not None and (task is None or r.task == task)]


def next_n(house: Path, task: str) -> int:
    """Return the number the next attempt at `task` gets.

    Args:
        house: The contest's `.house/` directory.
        task: Task id.

    Returns:
        1 + the highest `n` of the task's rows (any source), or 1 when there are none.
    """
    return 1 + max((r.n for r in rows(house, task)), default=0)


def find(house: Path, task: str, n: int) -> Row | None:
    """Return the row of attempt `n` of `task`, or None (the last one wins when a number repeats)."""
    return next((r for r in reversed(rows(house, task)) if r.n == n), None)


def _relative(house: Path, log: str) -> str:
    """Make `log` a path relative to the contest (`house.parent`): an absolute path is cut, else its file name."""
    if not log or not Path(log).is_absolute():
        return log
    try:
        return Path(log).relative_to(house.parent).as_posix()
    except ValueError:
        return Path(log).name


def _no_absolute_paths(house: Path, text: str) -> str:
    """Replace the contest root (resolved and as given) by `.` and the home directory by `~` in `text`."""
    contest = house.parent
    for root in sorted({str(contest.resolve()), str(contest.absolute())}, key=len, reverse=True):
        text = text.replace(root, ".")
    return text.replace(str(Path.home()), "~")


def record(house: Path, row: Row) -> Path:
    """Append one row to the register.

    Args:
        house: The contest's `.house/` directory.
        row: The attempt. It is not changed; the stored copy has a relative `log` and a `message` with the contest
            root as `.` and the home directory as `~`, redacted and cut to `MAX_MESSAGE` characters (redacted first,
            so a secret split by the cut is still masked).

    Returns:
        Path of `attempts.jsonl`.
    """
    message = guard.redact(_no_absolute_paths(house, row.message))[:MAX_MESSAGE]
    row = replace(row, log=_relative(house, row.log), message=message)
    path = house / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(asdict(row), ensure_ascii=False) + "\n"
    with path.open("a+b") as f:
        if f.tell() > 0:
            f.seek(-1, 2)
            if f.read(1) != b"\n":
                line = "\n" + line  # an interrupted write left an unterminated line: do not glue onto it
        f.write(line.encode("utf-8"))
    return path


def lock_path(contest: Path) -> Path:
    """The register lock file: `<git common dir>/house-register.lock`, shared by the checkout and its worktrees.

    Outside a git repo, a file in the temp directory named after the contest path stands in.
    """
    r = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=contest, capture_output=True, text=True,
                       check=False)
    if r.returncode == 0 and r.stdout.strip():
        return (contest / r.stdout.strip()).resolve() / LOCK_FILE
    digest = hashlib.sha256(str(contest.resolve()).encode()).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"house-register-{digest}.lock"


@contextlib.contextmanager
def lock(contest: Path, timeout: float | None = None) -> Iterator[None]:
    """Hold the contest's register lock (`fcntl.flock`, exclusive) for the duration of the block.

    Every writer of the register (number allocation, append, commit, restore from HEAD) runs under it. A second
    holder, in this or another process, waits.

    Args:
        contest: Contest root.
        timeout: Seconds to wait; default `LOCK_TIMEOUT`.

    Raises:
        TimeoutError: The lock was not free within the timeout.
    """
    deadline = time.monotonic() + (LOCK_TIMEOUT if timeout is None else timeout)
    with open(lock_path(contest), "a+") as f:
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"attempt register lock {lock_path(contest)} is busy") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
