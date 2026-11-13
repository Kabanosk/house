"""`house night`: work through ready tasks unattended, one fresh worker session per task.

preflight → (pick ready tasks → fresh session in the task worktree → `house merge` gate)* → morning report.
The dispatcher never writes code; workers never wait for a human (questions go to Signal, the task is blocked).

Used by: `house night <contest> [--hours H] [--max-tasks N] [--dry-run]` (see `cli.cmd_night`), run by the owner
or a scheduler. Workers are `claude -p --agent coder` sessions (or `bin/codex-task` for `coder-codex` tasks).

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
- Only the merge gate (`merge.merge_task`) puts work on the main line; night never merges by itself.
- Night commits only files it wrote itself (task file, PROGRESS.md, report), never all of `.house/`.
"""
import json
import os
import shutil
import signal as sig_mod
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import HOUSE_ROOT, checks, guard, merge
from . import spec as specmod
from .fm import parse_frontmatter, set_fields

MIN_FREE_GB, WARN_FREE_GB = 5, 10
MAX_ATTEMPTS, MAX_CONSECUTIVE_FAILURES = 3, 5
DEFAULT_TASK_MINUTES = 45
POLL_SECONDS = 5


@dataclass
class Attempt:
    """One worker run on one task and how it ended.

    Attributes:
        task: Task id, e.g. "T001".
        n: Attempt number for this task within the night run, starting at 1.
        outcome: One of "merged", "merge-refused", "reverted", "no-commits", "timeout", "blocked-by-worker",
            "tampered", "interrupted", "error".
        message: Human-readable details (the merge message, the tampering list, the error text).
        log: Path of the worker's log, relative to the contest when possible; "" if no worker was started.
    """
    task: str
    n: int
    outcome: str  # merged | merge-refused | reverted | no-commits | timeout | blocked-by-worker | error
    message: str
    log: str


@dataclass
class NightReport:
    """Result of `run_night`.

    Attributes:
        attempts: Every attempt in order, merged and failed.
        stopped_because: Why the night ended, e.g. "STOP file", "time limit", "nothing ready", "interrupted".
        report_path: The written `.house/reports/night-<date>.md`, or None before it is written.
    """
    attempts: list[Attempt] = field(default_factory=list)
    stopped_because: str = ""
    report_path: Path | None = None


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


def _commit_house(contest: Path, message: str) -> None:
    """Commit all pending changes under `.house/` with the given message, if there are any."""
    _git(contest, "add", "-A", "--", ".house")
    if _git(contest, "diff", "--cached", "--quiet", "--", ".house").returncode != 0:
        _git(contest, "commit", "-q", "-m", message, "--", ".house")


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


def brief(contest: Path, task_id: str, worktree: Path, previous: str | None = None) -> str:
    """Build the prompt for a worker session.

    Args:
        contest: Path of the contest checkout.
        task_id: Task id, e.g. "T001".
        worktree: The task worktree the worker runs in.
        previous: Outcome text of the last failed attempt, if any; the first 600 characters are added so the worker
            fixes that first.

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
    if previous:
        text += f" Previous attempt failed: {previous[:600]} . Fix that first."
    return text


def worker_command(contest: Path, task: checks.Task, worktree: Path, tools: dict,
                   previous: str | None = None) -> list[str]:
    """Build the command line of a worker session.

    Args:
        contest: Path of the contest checkout (its `spec.yaml` supplies `night.model`, default "sonnet").
        task: The loaded task (`checks.load_task`).
        worktree: The task worktree; passed to `bin/codex-task` or named in the brief.
        tools: Contents of `.house/tools.json`; a truthy `codex` entry enables Codex workers.
        previous: Outcome text of the last failed attempt, forwarded to `brief`.

    Returns:
        `[bin/codex-task, <task file>, <worktree>]` for a `coder-codex` task when Codex is available, otherwise
        `claude -p --agent coder --model <model> ... <brief>` with the contest's local settings, auto permission mode
        and no permission prompts.
    """
    if task.meta.get("agent") == "coder-codex" and tools.get("codex"):
        return [str(HOUSE_ROOT / "bin/codex-task"), str(task.path), str(worktree)]
    model = specmod.get(_spec(contest), "night.model") or "sonnet"
    return ["claude", "-p", "--agent", "coder", "--model", str(model),
            "--settings", str(contest / ".claude/settings.local.json"),
            "--permission-mode", "auto", "--permission-prompts", "none",
            brief(contest, task.id, worktree, previous)]


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


def _write_report(contest: Path, rep: NightReport, started: datetime, tampering: list[str]) -> Path:
    """Write the morning report `.house/reports/night-<date>.md` and append a line to `.house/PROGRESS.md`.

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
        details = a.message.replace("\n", " ").replace("|", "/")[:300]
        lines.append(f"| {a.task} | {a.n} | {a.outcome} | {details} | `{a.log}` |")
    questions = _open_questions(house)
    lines += ["", "## Open questions", *([f"- {q}" for q in questions] or ["- none"])]
    if tampering:
        lines += ["", "## Tampering found (left uncommitted; house merge refuses it)", *[f"- {t}" for t in tampering]]
    path.write_text("\n".join(lines) + "\n")
    with (house / "PROGRESS.md").open("a") as f:
        f.write(f"- {datetime.now():%Y-%m-%d %H:%M} · house · noc: {len(merged)} zadań scalonych, "
                f"{len(rep.attempts) - len(merged)} nieudanych prób, koniec: {rep.stopped_because} · "
                f"{path.relative_to(contest)}\n")
    return path


def _after_failure(contest: Path, tid: str, n: int, outcome: str) -> None:
    """Status after a failed attempt: 3rd failure → blocked; doing/review → todo; blocked (by the worker or a
    reverted merge) stays blocked."""
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
    _commit_paths(contest, [task.path, progress], f"chore(house): night {tid} attempt {n} {outcome}")


def run_night(contest: Path, hours: float = 8, max_tasks: int | None = None,
              launcher: Callable | None = None, now: Callable = time.monotonic,
              sleep: Callable = time.sleep) -> NightReport:
    """Run the unattended loop: start workers, gate their results through `house merge`, write the morning report.

    Args:
        contest: Path of the contest checkout.
        hours: Time limit of the whole run in hours (default 8). No new task is started after it.
        max_tasks: Stop after starting this many attempts; None means no limit.
        launcher: Callable `(cmd, cwd, log) -> worker` that starts a worker; defaults to `_launch` (a real process).
            The worker needs `poll()` and `kill()`. Replace in tests.
        now: Monotonic clock in seconds; replace in tests.
        sleep: Sleep function used between polls (POLL_SECONDS); replace in tests.

    Returns:
        A `NightReport`. Parallelism is `limits.max_parallel_agents` (default 3), the per-task limit is
        `night.task_minutes` (default 45). After MAX_ATTEMPTS failures a task becomes `blocked`; the run stops after
        MAX_CONSECUTIVE_FAILURES failed attempts in a row (a worker that blocks its task does not count). On exit,
        surviving workers are killed, their tasks go back to `todo`, the report is written and committed, and a
        Signal message is sent (errors there are ignored). Ctrl+C ends the run cleanly with "interrupted".
    """
    launcher = launcher or _launch
    house = contest / ".house"
    spec = _spec(contest)
    limit = int(specmod.get(spec, "limits.max_parallel_agents") or 3)
    task_seconds = float(specmod.get(spec, "night.task_minutes", DEFAULT_TASK_MINUTES)) * 60
    tools = _tools(contest)
    started_at, t0 = datetime.now(), now()
    rep = NightReport()
    running: dict[str, tuple] = {}  # task → (proc, start, worktree, log, attempt n, branch)
    attempts: dict[str, int] = {}
    last_failure: dict[str, str] = {}
    consecutive, started_count = 0, 0

    def failed(tid: str, n: int, attempt: Attempt) -> None:
        nonlocal consecutive
        rep.attempts.append(attempt)
        attempts[tid] = n
        last_failure[tid] = f"{attempt.outcome}: {attempt.message}"
        if attempt.outcome != "blocked-by-worker":
            consecutive += 1
        _after_failure(contest, tid, n, attempt.outcome)

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
                    started_count += 1
                    log = house / "tmp/night" / f"{started_at:%Y-%m-%d}" / f"{tid}-{started_at:%H%M%S}-{n}.log"
                    try:
                        wt = prepare_worktree(contest, tid)
                        task = checks.load_task(house, tid)
                        cmd = worker_command(contest, task, wt, tools, last_failure.get(tid))
                        running[tid] = (launcher(cmd, wt, log), now(), wt, log, n, str(task.meta.get("branch")))
                    except Exception as e:  # noqa: BLE001 — one broken task must not end the night
                        failed(tid, n, Attempt(tid, n, "error", f"could not start: {e}", ""))
            if not running and not rep.stopped_because:
                rep.stopped_because = stopping or (
                    f"{MAX_CONSECUTIVE_FAILURES} failed attempts in a row"
                    if consecutive >= MAX_CONSECUTIVE_FAILURES else "nothing ready")
                if not stopping and consecutive < MAX_CONSECUTIVE_FAILURES and pick_ready(house, set(), 1):
                    rep.stopped_because = ""
                    continue
                break
            for tid in list(running):
                proc, start, wt, log, n, branch = running[tid]
                code = proc.poll()
                timed_out = code is None and now() - start >= task_seconds
                if code is None and not timed_out:
                    continue
                if timed_out:
                    proc.kill()
                del running[tid]
                try:
                    attempt = _finish(contest, tid, n, branch, wt, log, timed_out)
                except Exception as e:  # noqa: BLE001
                    attempt = Attempt(tid, n, "error", f"evaluating the attempt failed: {e!r}", str(log))
                if attempt.outcome == "merged":
                    rep.attempts.append(attempt)
                    attempts[tid] = n
                    consecutive = 0
                else:
                    failed(tid, n, attempt)
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
        for tid, (proc, _start, _wt, log, n, _branch) in list(running.items()):
            proc.kill()
            rep.attempts.append(Attempt(tid, n, "interrupted", "session killed when the night ended", str(log)))
            task = checks.load_task(house, tid)
            if task.meta.get("status") in ("doing", "review"):
                _set(task, status="todo")
            _commit_paths(contest, [task.path], f"chore(house): night interrupted {tid}")
        rep.stopped_because = rep.stopped_because or "ended"
        tampering = guard.frozen_tampering(contest)
        rep.report_path = _write_report(contest, rep, started_at, tampering)
        _commit_paths(contest, [rep.report_path, house / "PROGRESS.md"], "chore(house): night report")
        try:
            from . import notify
            merged = sum(a.outcome == "merged" for a in rep.attempts)
            notify.send(contest, f"🌙 noc: {merged} scalonych, {len(rep.attempts) - merged} nieudanych prób; "
                        f"koniec: {rep.stopped_because}. Raport: {rep.report_path.name}")
        except Exception:  # noqa: BLE001, S110
            pass
    return rep


def _restore_tampering(contest: Path, found: list[str]) -> None:
    """Restore what only house may write from HEAD: frozen criteria and hand-set merged/done statuses.

    Args:
        contest: Path of the contest checkout.
        found: Messages from `guard.frozen_tampering`; task files named in "T### set to ..." messages are restored.
    """
    _git(contest, "checkout", "HEAD", "--", ".house/checks")
    for path in sorted((contest / ".house/tasks").glob("T*-*.md")):
        tid = path.name.split("-", 1)[0]
        if any(line.startswith(f"{tid} set to") for line in found):
            _git(contest, "checkout", "HEAD", "--", str(path.relative_to(contest)))


def _commit_leftovers(wt: Path, tid: str, contest: Path) -> list[str]:
    """Commit what the worker left uncommitted, except secrets, protected files and `.house/` (they stay uncommitted,
    so the gate refuses the branch).

    Args:
        wt: The task worktree.
        tid: Task id, used in the commit message.
        contest: Path of the contest checkout (source of the protected-paths list).

    Returns:
        The paths that were left out.
    """
    protected = guard.protected_paths(contest)
    keep, skipped = [], []
    for _, rel in guard._status(wt):
        risky = rel.startswith(".house/") or rel in protected or guard._secret_file(wt / rel)
        (skipped if risky else keep).append(rel)
    if keep:
        _git(wt, "add", "-A", "--", *keep)
        _git(wt, "commit", "-q", "-m", f"{tid}: uncommitted work at the end of the night session")
    return skipped


def _finish(contest: Path, tid: str, n: int, branch: str, wt: Path, log: Path, timed_out: bool) -> Attempt:
    """Evaluate a finished (or timed-out) worker and run the merge gate.

    Args:
        contest: Path of the contest checkout.
        tid: Task id.
        n: Attempt number.
        branch: Task branch name.
        wt: The task worktree.
        log: The worker's log file.
        timed_out: True when the worker was killed for exceeding the time limit.

    Returns:
        An `Attempt`: "tampered" (restored from HEAD), "timeout", "blocked-by-worker", "no-commits", "error",
        "reverted" or "merge-refused" (from `merge.merge_task`), or "merged". Otherwise the task is set to `review`
        and merged through `merge.merge_task`.
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
    _commit_leftovers(wt, tid, contest)
    if _ahead(contest, branch) == 0:
        return Attempt(tid, n, "no-commits", "worker finished without committing anything", rel_log)
    _set(task, status="review")
    _commit_paths(contest, [task.path], f"chore(house): {tid} ready for review (night)")
    if str(task.meta.get("review")) == "always":
        from . import review
        res = review.review_task(contest, tid)
        if not res.ok:
            return Attempt(tid, n, "merge-refused", f"review failed: {res.why}", rel_log)
    try:
        out = merge.merge_task(contest, tid, wt)
    except Exception as e:  # noqa: BLE001
        return Attempt(tid, n, "error", f"merge crashed: {e!r}", rel_log)
    if out.ok:
        return Attempt(tid, n, "merged", out.message, rel_log)
    return Attempt(tid, n, "reverted" if out.reverted else "merge-refused", out.message, rel_log)
