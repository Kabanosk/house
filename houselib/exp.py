"""Experiment loop for optimize contests (`house exp`).

Every experiment is a small change of a parent (the best `keep`, any earlier experiment, or a commit), run with
a fixed time budget, scored by `spec.scoring.local_scorer` and decided by code: better than the parent → `keep`,
otherwise `revert`. Each attempt is one row of `.house/experiments.csv`; `parent` makes the rows a tree, so an
agent can improve the best or debug a crash. Stagnation is computed from that file, not from memory.

Used by: coders and the coordinator in optimize contests (`playbooks/optimize.md`), `house exp new|run|status|
prune`, and the session-start hook (one summary line).

Rules an agent should know:
- Workflow: `house exp new <c> --from best` prints `E-### <worktree>`; make ONE change there; then
  `house exp run <c> E-### --budget 5m -- <command>`. The command trains/predicts inside the worktree, then the
  scorer runs there. `house exp run <c> --from best -- <command>` does both when the change is the command line.
- The scorer must print `score: <number>` (or a bare number as its last line). Direction comes from
  `scoring.direction` (`max`/`min`), else from the words max/min in `scoring.metric`.
- An experiment that changes the scorer or a frozen acceptance test is a `crash` ("tampered"). Scores are
  always computed with the contest checkout's current scorer; rows scored by an older scorer are not compared.
- A row reproduces with its `commit` plus its `command` (a sweep changes only the command).
- Statuses: `new` (created), `keep`, `revert`, `crash`, `timeout`. Ties are `revert`. Every scored commit stays
  reachable as `refs/exp/E-###`; `keep` worktrees stay (their outputs may be submitted), the others are removed.
- Data outside git: list it in `exp.shared` in the spec and it is symlinked into every experiment worktree.
  `HOUSE_EXP` (id) and `HOUSE_CONTEST` (checkout path) are set for the command and the scorer.
"""
import contextlib
import csv
import fcntl
import hashlib
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import checks, gitutil, guard, outlog
from .outlog import LOG_CAP, cap_tail
from . import spec as specmod

COLUMNS = ["id", "parent", "commit", "idea_id", "budget", "duration", "score", "status", "scorer", "time", "note",
           "command"]
SCORED = ("keep", "revert")
_BUDGET_PART = re.compile(r"(\d+)([smh])")
_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_SCORE_RE = re.compile(rf"\bscore\s*[:=]\s*({_NUM})", re.IGNORECASE)
_DIRECTION_RE = re.compile(r"\b(min|minimi[sz]e|max|maximi[sz]e)\b", re.IGNORECASE)
DEFAULT_BUDGET = "5m"
DEFAULT_SCORER_MINUTES = 10
MAX_UNTRACKED_BYTES = 20 * 1024 * 1024  # bigger new files (weights, predictions) must be gitignored, not committed
_NOT_SHARED = {".git", ".house", ".worktrees"}


@dataclass
class Result:
    """Outcome of one `house exp run`.

    Attributes:
        id: Experiment id (`E-###`).
        status: `keep`, `revert`, `crash` or `timeout`.
        score: Score from the local scorer, or None (crash/timeout).
        parent_score: The parent's comparable score, or None (raw commit, crash, other scorer).
        note: Why (exit code, tampered files, missing score) plus the caller's note.
        log: Path of `.house/experiments/E-###.log` (command and scorer output, capped at 1 MB).
    """

    id: str
    status: str
    score: float | None
    parent_score: float | None
    note: str
    log: Path


def parse_budget(text: str) -> int:
    """Turn a time budget into seconds.

    Args:
        text: Plain seconds (`"45"`) or units `s`/`m`/`h`, combinable (`"90s"`, `"5m"`, `"1h30m"`).

    Returns:
        The budget in seconds, at least 1.

    Raises:
        ValueError: Empty, malformed or zero budget.
    """
    t = str(text).strip().lower()
    if t.isdigit():
        seconds = int(t)
    elif t and re.fullmatch(r"(?:\d+[smh])+", t):
        seconds = sum(int(n) * {"s": 1, "m": 60, "h": 3600}[u] for n, u in _BUDGET_PART.findall(t))
    else:
        raise ValueError(f"invalid budget {text!r}; use e.g. 90s, 5m, 1h30m")
    if seconds <= 0:
        raise ValueError(f"budget must be positive, got {text!r}")
    return seconds


def direction(spec: dict) -> str | None:
    """Tell whether the contest metric is maximized or minimized.

    Args:
        spec: Parsed `spec.yaml` (`spec.load_spec`).

    Returns:
        `"max"` or `"min"` from `scoring.direction`; otherwise from the first word max/min/maximize/minimize in
        `scoring.metric`; None when neither says it (then `house exp run` refuses).
    """
    explicit = specmod.get(spec, "scoring.direction")
    if isinstance(explicit, str) and explicit.strip().lower() in ("max", "min"):
        return explicit.strip().lower()
    metric = specmod.get(spec, "scoring.metric")
    m = _DIRECTION_RE.search(metric) if isinstance(metric, str) else None
    return m.group(1).lower()[:3] if m else None


def parse_score(output: str) -> float | None:
    """Read the score from the scorer's output.

    Args:
        output: Everything the scorer printed (stdout and stderr together).

    Returns:
        The number in the last `score: X` / `score=X` (case-insensitive); otherwise the last non-empty line if it
        is a single number; otherwise None.
    """
    found = _SCORE_RE.findall(output or "")
    if found:
        return float(found[-1])
    lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
    if lines and re.fullmatch(_NUM, lines[-1]):
        return float(lines[-1])
    return None


def _csv_path(contest: Path) -> Path:
    """Return the path of `.house/experiments.csv`."""
    return contest / ".house" / "experiments.csv"


def _state_dir(contest: Path) -> Path:
    """Return `.house/experiments/` (lock and logs; never committed), creating it."""
    d = contest / ".house" / "experiments"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load(contest: Path) -> list[dict]:
    """Read every experiment row.

    Args:
        contest: Contest checkout root.

    Returns:
        Rows in file order as dicts with every key of `COLUMNS` (missing cells are ""); [] without the file.
    """
    path = _csv_path(contest)
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return [{k: (r.get(k) or "") for k in COLUMNS} for r in csv.DictReader(f)]


def _write(contest: Path, rows: list[dict]) -> None:
    """Rewrite experiments.csv atomically (call under `_locked`); newlines in cells become spaces."""
    path = _csv_path(contest)
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({k: " ".join(str(r.get(k, "") or "").splitlines()) for k in COLUMNS})
    os.replace(tmp, path)


@contextlib.contextmanager
def _locked(contest: Path):
    """Hold the exclusive experiments lock (`.house/experiments/lock`) for id allocation and csv updates."""
    with (_state_dir(contest) / "lock").open("w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def next_id(rows: list[dict], taken: set[str] | frozenset = frozenset()) -> str:
    """Return the next free experiment id (`E-001`, `E-002`, …) after the highest one in rows and in `taken`.

    Args:
        rows: Experiment rows.
        taken: Ids used elsewhere (existing `refs/exp/*` and `.worktrees/exp-*`), so an id is never reused even
            when csv rows were lost.
    """
    ids = [r.get("id", "") for r in rows] + list(taken)
    nums = [int(m.group(1)) for i in ids if (m := re.fullmatch(r"E-(\d+)", i))]
    return f"E-{max(nums, default=0) + 1:03d}"


def _score(r: dict) -> float | None:
    """Return the row's score as a float, or None when it has none."""
    try:
        return float(r.get("score") or "")
    except ValueError:
        return None


def _better(a: float, b: float, sense: str) -> bool:
    """Tell whether score a is strictly better than b for the direction ("max"/"min")."""
    return a > b if sense == "max" else a < b


def _comparable(rows: list[dict], scorer: str) -> list[dict]:
    """Rows scored (keep/revert with a number) by the given scorer hash, in file order."""
    return [r for r in rows if r.get("status") in SCORED and r.get("scorer") == scorer and _score(r) is not None]


def best(rows: list[dict], scorer: str, sense: str) -> dict | None:
    """Pick the best `keep` scored by the current scorer.

    Args:
        rows: Experiment rows (`load`).
        scorer: Current scorer hash; rows scored by another scorer are ignored.
        sense: `"max"` or `"min"`.

    Returns:
        The best row (the earliest one on a tie), or None when there is no comparable keep.
    """
    keeps = [r for r in _comparable(rows, scorer) if r["status"] == "keep"]
    if not keeps:
        return None
    pick = max if sense == "max" else min
    return pick(keeps, key=_score)


def stagnation(rows: list[dict], scorer: str, sense: str) -> int:
    """Count scored experiments since the last new best.

    Only `keep`/`revert` rows of the current scorer count; crashes and timeouts do not (a typo must not force a
    pivot). Compare the result with `limits.stagnation_n`.

    Args:
        rows: Experiment rows (`load`).
        scorer: Current scorer hash.
        sense: `"max"` or `"min"`.

    Returns:
        Number of comparable experiments after the row that last set a new best (all of them if none did).
    """
    count, current = 0, None
    for r in _comparable(rows, scorer):
        s = _score(r)
        if r["status"] == "keep" and (current is None or _better(s, current, sense)):
            current, count = s, 0
        else:
            count += 1
    return count


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Run git without raising; callers check the return code."""
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def _spec(contest: Path) -> dict:
    """Load the contest's spec.yaml ({} when missing)."""
    return specmod.load_spec(contest / ".house")


def _requirements(contest: Path) -> tuple[str, str]:
    """Return `(direction, scorer command)` or raise ValueError telling what to set in spec.yaml."""
    spec = _spec(contest)
    sense = direction(spec)
    if sense is None:
        raise ValueError("set scoring.direction (max or min) in .house/spec.yaml")
    scorer = specmod.get(spec, "scoring.local_scorer")
    if not isinstance(scorer, str) or not scorer.strip():
        raise ValueError("set scoring.local_scorer (the local scorer command) in .house/spec.yaml")
    return sense, scorer


def _snapshot(contest: Path) -> dict[str, bytes]:
    """Read the scorer files (`guard.scorer_files`: tracked files only) from the contest checkout."""
    return {rel: (contest / rel).read_bytes() for rel in guard.scorer_files(contest) if (contest / rel).is_file()}


def _fingerprint(command: str, snap: dict[str, bytes]) -> str:
    """12 hex characters of a sha256 over the scorer command and the snapshot's files."""
    h = hashlib.sha256(command.encode())
    for rel in sorted(snap):
        h.update(b"\0" + rel.encode() + b"\0" + snap[rel])
    return h.hexdigest()[:12]


def scorer_hash(contest: Path) -> str:
    """Fingerprint the current local scorer: its command and its files (`guard.scorer_files`).

    Rows with another fingerprint were scored differently and are not compared (`best`, `stagnation`). Data and
    outputs named in the command are not part of it (only files tracked by git are).

    Args:
        contest: Contest checkout root.

    Returns:
        12 hex characters.
    """
    return _fingerprint(str(specmod.get(_spec(contest), "scoring.local_scorer") or ""), _snapshot(contest))


def _worktree(contest: Path, exp_id: str) -> Path:
    """Return the experiment's worktree path `.worktrees/exp-E-###`."""
    return contest / ".worktrees" / f"exp-{exp_id}"


def _commit_of(rows: list[dict], parent: str, contest: Path) -> str:
    """Resolve an experiment id or any git revision to a commit hash (ValueError when impossible)."""
    if re.fullmatch(r"E-\d+", parent):
        r = next((r for r in rows if r["id"] == parent), None)
        if r is None:
            raise ValueError(f"unknown experiment {parent}")
        if not r["commit"]:
            raise ValueError(f"{parent} has not been run yet; run it or pick another parent")
        return r["commit"]
    out = _git(contest, "rev-parse", "--verify", "--quiet", f"{parent}^{{commit}}")
    if out.returncode != 0:
        raise ValueError(f"unknown parent {parent!r}: use best, an experiment id (E-###) or a commit")
    return out.stdout.strip()


def previous_best(rows: list[dict], scorer: str, sense: str) -> dict | None:
    """The best keep of the most recent earlier scorer, used after a scorer change until a new keep exists.

    Args:
        rows: Experiment rows.
        scorer: Current scorer hash.
        sense: `"max"` or `"min"`.

    Returns:
        That row, or None when no earlier scorer has a keep.
    """
    older = [r["scorer"] for r in rows if r["status"] in SCORED and r["scorer"] and r["scorer"] != scorer]
    return best(rows, older[-1], sense) if older else None


def _resolve(contest: Path, rows: list[dict], parent: str) -> tuple[str, str]:
    """Turn `--from` into `(parent label for the csv, commit)`. `best` is the best comparable keep; after a scorer
    change, the previous scorer's best (its child becomes the first keep of the new scorer); else HEAD."""
    if parent == "best":
        sense = direction(_spec(contest))
        if sense is None:
            raise ValueError("set scoring.direction (max or min) in .house/spec.yaml")
        b = best(rows, scorer_hash(contest), sense) or previous_best(rows, scorer_hash(contest), sense)
        if b:
            return b["id"], b["commit"]
        parent = "HEAD"
    commit = _commit_of(rows, parent, contest)
    return (parent if re.fullmatch(r"E-\d+", parent) else commit), commit


def _link_shared(contest: Path, wt: Path) -> None:
    """Symlink every `exp.shared` path that exists in the contest but not in the worktree, and git-exclude it."""
    shared = specmod.get(_spec(contest), "exp.shared") or []
    for rel in shared if isinstance(shared, list) else []:
        rel = str(rel).strip().strip("/")
        if not rel or Path(rel).parts[0] in _NOT_SHARED or ".." in Path(rel).parts:
            continue
        src, dst = contest / rel, wt / rel
        if src.exists() and not dst.exists() and not dst.is_symlink():
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.symlink_to(src.resolve(), target_is_directory=src.is_dir())
            gitutil.exclude(contest, "/" + rel)


def _now() -> str:
    """Local time, ISO format, seconds precision."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new(contest: Path, parent: str = "best", idea: str = "", note: str = "") -> tuple[str, Path]:
    """Create an experiment: a detached worktree at the parent commit and a row with status `new`.

    Make one change in the worktree, then call `run` (`house exp run <c> E-### -- <command>`).

    Args:
        contest: Contest checkout root.
        parent: `best` (the best comparable keep, or HEAD when there is none), an experiment id (`E-###`, also a
            crash or revert, e.g. to debug it) or any git revision.
        idea: Idea id from `.house/ideas.md` (optional).
        note: One-line description of the change (optional).

    Returns:
        `(experiment id, worktree path)`.

    Raises:
        ValueError: Unknown parent, a parent that has not run, or `best` without a known direction.
        RuntimeError: git could not create the worktree.
    """
    gitutil.exclude(contest, ".worktrees/")
    gitutil.exclude(contest, ".house/experiments/")
    with _locked(contest):
        rows = load(contest)
        label, commit = _resolve(contest, rows, parent)
        refs = _git(contest, "for-each-ref", "--format=%(refname:strip=2)", "refs/exp/").stdout.split()
        dirs = [p.name.removeprefix("exp-") for p in (contest / ".worktrees").glob("exp-E-*")]
        eid = next_id(rows, set(refs) | set(dirs))
        wt = _worktree(contest, eid)
        out = _git(contest, "worktree", "add", "--detach", "-q", str(wt), commit)
        if out.returncode != 0:
            raise RuntimeError(f"git worktree add failed: {out.stderr.strip()}")
        try:
            _link_shared(contest, wt)
        except BaseException:
            _remove_worktree(contest, wt)
            raise
        rows.append({**dict.fromkeys(COLUMNS, ""), "id": eid, "parent": label, "idea_id": idea, "status": "new",
                     "time": _now(), "note": note})
        _write(contest, rows)
    return eid, wt


def _remove_worktree(contest: Path, wt: Path) -> None:
    """Delete an experiment worktree; shared symlinks are unlinked first so their targets are never touched."""
    shared = specmod.get(_spec(contest), "exp.shared") or []
    for rel in shared if isinstance(shared, list) else []:
        link = wt / str(rel).strip().strip("/")
        if link.is_symlink():
            link.unlink()
    _git(contest, "worktree", "remove", "--force", str(wt))
    _git(contest, "worktree", "prune")


def _large_untracked(wt: Path) -> list[str]:
    """List untracked, not ignored files in the worktree above MAX_UNTRACKED_BYTES (they would bloat git)."""
    out = _git(wt, "ls-files", "--others", "--exclude-standard", "-z").stdout
    return [rel for rel in out.split("\0") if rel and (wt / rel).is_file() and not (wt / rel).is_symlink()
            and (wt / rel).stat().st_size > MAX_UNTRACKED_BYTES]


def _write_log(path: Path, parts: list[str]) -> None:
    """Write the experiment log, keeping only its last LOG_CAP characters (shared with `house out`)."""
    path.write_text(cap_tail("\n".join(parts), LOG_CAP))


def _command_text(command: list[str] | str) -> str | list[str]:
    """A single string (or one-element list) runs through the shell; a longer list runs as is."""
    if isinstance(command, list) and len(command) == 1:
        return command[0]
    return command


_Interrupted = outlog.Interrupted  # SIGTERM/SIGHUP during `run`, raised like Ctrl+C (shared with `house out`)
_signals_interrupt = outlog.signals_interrupt


def _alive(pid: int) -> bool:
    """Tell whether a process with this pid exists."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OverflowError):
        return True
    return True


def _pidfile(contest: Path, exp_id: str) -> Path:
    """`.house/experiments/E-###.pid`: the process running the experiment."""
    return _state_dir(contest) / f"{exp_id}.pid"


def _claim(contest: Path, exp_id: str) -> dict:
    """Mark a `new` experiment (or a `running` one whose process is gone) as `running` by this process."""
    with _locked(contest):
        rows = load(contest)
        r = next((r for r in rows if r["id"] == exp_id), None)
        if r is None:
            raise ValueError(f"unknown experiment {exp_id}")
        if r["status"] == "running":
            try:
                pid = int(_pidfile(contest, exp_id).read_text().strip())
            except (OSError, ValueError):
                pid = None
            if pid and _alive(pid):
                raise ValueError(f"{exp_id} is running (pid {pid}); wait for it or start a new one")
        elif r["status"] != "new":
            raise ValueError(f"{exp_id} already ran ({r['status']}); start a new one with --from {exp_id}")
        if not _worktree(contest, exp_id).is_dir():
            raise ValueError(f"worktree of {exp_id} is missing ({_worktree(contest, exp_id)})")
        r["status"] = "running"
        _write(contest, rows)
        _pidfile(contest, exp_id).write_text(str(os.getpid()))
        return dict(r)


def _record(contest: Path, exp_id: str, commit_csv: bool = True, **fields) -> None:
    """Update one row under the lock and (by default) commit experiments.csv on the contest checkout."""
    with _locked(contest):
        rows = load(contest)
        for row in rows:
            if row["id"] == exp_id:
                row.update(fields)
        _write(contest, rows)
        if commit_csv:
            score = fields.get("score")
            checks._git_commit(contest / ".house", [_csv_path(contest)],
                               f"chore(exp): {exp_id} {fields.get('status', '')}" + (f" {score}" if score else ""))


def run(contest: Path, exp_id: str, command: list[str] | str, budget: int | None = None) -> Result:
    """Run a `new` experiment: commit its change, run the command with the budget, score it, keep or revert.

    Steps: the row is claimed as `running` (a second run of the same id is refused while this process lives);
    everything in the worktree is committed (`refs/exp/E-###` pins it); a change to a protected file (scorer,
    its imported helpers, frozen acceptance test; renames included) against the parent makes it a `crash`
    ("tampered") without running; the scorer files are snapshotted from the contest checkout; the command runs in
    the worktree in its own process group, killed at the budget (`timeout`); a non-zero exit is a `crash`; the
    scorer then runs in the worktree with the snapshot (never writing through symlinks), and its score is compared
    with the parent's: strictly better (or no comparable parent score) → `keep`, else `revert`. If the contest's
    scorer changed during the run, it is a `crash`. The row is updated and `.house/experiments.csv` committed on
    the contest checkout; the worktree is kept only for `keep`. SIGINT/SIGTERM/SIGHUP kill the command and
    record `crash` "interrupted".

    Args:
        contest: Contest checkout root.
        exp_id: Id printed by `new`.
        command: Command to run in the worktree: one string (shell) or an argument list.
        budget: Seconds for the command; default `exp.budget` from the spec (else 5 minutes).

    Returns:
        The `Result`.

    Raises:
        ValueError: Unknown id, an id that already ran or is running, a missing worktree, large untracked files,
            or the spec lacks `scoring.direction` / `scoring.local_scorer`. The row stays runnable.
        RuntimeError: The experiment's change could not be committed (the command did not run).
        KeyboardInterrupt: Interrupted; the row was recorded as `crash`.
    """
    sense, scorer_cmd = _requirements(contest)
    spec = _spec(contest)
    if budget is None:
        budget = parse_budget(str(specmod.get(spec, "exp.budget") or DEFAULT_BUDGET))
    scorer_timeout = int(float(specmod.get(spec, "exp.scorer_minutes") or DEFAULT_SCORER_MINUTES) * 60)
    r = _claim(contest, exp_id)
    wt = _worktree(contest, exp_id)
    state = {"commit": ""}
    try:
        with _signals_interrupt():
            return _run_claimed(contest, r, wt, command, budget, sense, scorer_cmd, scorer_timeout, state)
    except KeyboardInterrupt:
        _record(contest, exp_id, status="crash", commit=state["commit"], budget=str(budget),
                note="; ".join(x for x in (r["note"], "interrupted") if x))
        _remove_worktree(contest, wt)
        raise
    except BaseException:
        _record(contest, exp_id, commit_csv=False, status="new")
        raise
    finally:
        _pidfile(contest, exp_id).unlink(missing_ok=True)


def _run_claimed(contest: Path, r: dict, wt: Path, command, budget: int, sense: str, scorer_cmd: str,
                 scorer_timeout: int, state: dict) -> Result:
    """Body of `run` once the row is claimed; `state["commit"]` is set as soon as the change is committed."""
    exp_id = r["id"]
    rows = load(contest)
    parent_commit = _commit_of(rows, r["parent"], contest)
    parent_row = next((p for p in rows if p["id"] == r["parent"]), None)
    big = _large_untracked(wt)
    if big:
        raise ValueError(f"{exp_id}: large untracked files would be committed: {', '.join(big)}; "
                         "add them to .gitignore or delete them, then run again")
    added = _git(wt, "add", "-A")
    if added.returncode != 0:
        raise RuntimeError(f"git add failed in {wt}: {added.stderr.strip()}")
    if _git(wt, "diff", "--cached", "--quiet").returncode != 0:
        msg = f"exp {exp_id}: {r['note'] or r['idea_id'] or 'change'}"
        done = _git(wt, "commit", "-q", "--no-verify", "-m", msg)
        if done.returncode != 0:
            raise RuntimeError(f"git commit of {exp_id} failed: {(done.stderr or done.stdout).strip()}")
    commit = _git(wt, "rev-parse", "HEAD").stdout.strip()
    _git(contest, "update-ref", f"refs/exp/{exp_id}", commit)
    state["commit"] = commit

    snap = _snapshot(contest)
    current = _fingerprint(scorer_cmd, snap)
    env = {"HOUSE_EXP": exp_id, "HOUSE_CONTEST": str(contest)}
    cmd = _command_text(command)
    shown = cmd if isinstance(cmd, str) else shlex.join(cmd)
    log = _state_dir(contest) / f"{exp_id}.log"
    log_parts = [f"$ {shown}", f"budget: {budget} s"]
    score = parent_score = None
    duration = 0.0
    out = _git(contest, "diff", "--name-only", "--no-renames", "-z", parent_commit, commit).stdout
    changed = set(out.split("\0")) - {""}
    tampered = sorted(changed & (set(guard.protected_paths(contest)) | set(snap)))
    if tampered:
        status, why = "crash", "tampered: " + ", ".join(tampered) + " (scorer/acceptance tests must not change)"
    else:
        t0 = time.monotonic()
        code, output, _ = checks._run_cmd(cmd, wt, budget, extra_env=env)
        duration = time.monotonic() - t0
        log_parts.append(output)
        if code is None:
            status, why = "timeout", f"timeout after {budget} s"
        elif code != 0:
            status, why = "crash", f"exit {code}"
        else:
            status, why, score = _score_in(wt, commit, scorer_cmd, snap, scorer_timeout, env, log_parts)
            if status is None and scorer_hash(contest) != current:
                status, why, score = "crash", "scorer changed during the run (contest checkout); not comparable", None
            if status is None:
                if (parent_row and parent_row["status"] in SCORED and parent_row["scorer"] == current
                        and _score(parent_row) is not None):
                    parent_score = _score(parent_row)
                better = parent_score is None or _better(score, parent_score, sense)
                status = "keep" if better else "revert"
                why = "" if better else f"not better than {r['parent']} ({parent_score})"
    if not snap:
        why = "; ".join(x for x in (why, "warning: no scorer files protected; list them in scoring.scorer_files")
                        if x)
    log_parts.append(f"status: {status} {why}".rstrip())
    _write_log(log, log_parts)
    note = "; ".join(x for x in (r["note"], why) if x)
    _record(contest, exp_id, commit=commit, budget=str(budget), duration=f"{duration:.1f}", status=status,
            score="" if score is None else repr(score), scorer=current, note=note, command=shown)
    if status != "keep":
        _remove_worktree(contest, wt)
    return Result(exp_id, status, score, parent_score, note, log)


def _inside(wt: Path, rel: str) -> bool:
    """Tell whether writing `wt/rel` stays inside the worktree (no symlinked file or directory on the way)."""
    root = os.path.realpath(wt)
    return os.path.realpath(wt / rel).startswith(root + os.sep)


def _score_in(wt: Path, commit: str, scorer_cmd: str, snap: dict[str, bytes], timeout: int, env: dict,
              log_parts: list[str]) -> tuple[str | None, str, float | None]:
    """Run the scorer in the worktree with the snapshot's files; returns (crash status or None, why, score)."""
    written = []
    try:
        for rel, data in snap.items():
            if _inside(wt, rel):
                (wt / rel).parent.mkdir(parents=True, exist_ok=True)
                (wt / rel).write_bytes(data)
                written.append(rel)
        code, output, _ = checks._run_cmd(scorer_cmd, wt, timeout, extra_env=env)
    finally:
        for rel in written:
            if _git(wt, "cat-file", "-e", f"{commit}:{rel}").returncode == 0:
                _git(wt, "checkout", commit, "--", rel)
            elif _inside(wt, rel):
                (wt / rel).unlink(missing_ok=True)
    log_parts += ["--- scorer ---", f"$ {scorer_cmd}", output]
    if code is None:
        return "crash", f"scorer timeout after {timeout} s", None
    if code != 0:
        return "crash", f"scorer failed (exit {code})", None
    score = parse_score(output)
    if score is None:
        return "crash", "scorer printed no score; it must print `score: <number>`", None
    return None, "", score


def run_new(contest: Path, parent: str, command: list[str] | str, budget: int | None = None, idea: str = "",
            note: str = "") -> Result:
    """Create and run an experiment in one go (the change is the command line, e.g. a parameter sweep).

    Args:
        contest: Contest checkout root.
        parent: As in `new` (`best`, `E-###` or a git revision).
        command: As in `run`.
        budget: As in `run`.
        idea: Idea id (optional).
        note: One-line description (optional).

    Returns:
        The `Result` of `run`.

    Raises:
        ValueError: As in `new` and `run`; the spec is checked before anything is created.
    """
    _requirements(contest)
    eid, _ = new(contest, parent, idea=idea, note=note)
    return run(contest, eid, command, budget)


def _stagnation_limit(spec: dict) -> int:
    """Return `limits.stagnation_n` (default 3)."""
    try:
        return int(specmod.get(spec, "limits.stagnation_n") or 3)
    except (TypeError, ValueError):
        return 3


def status(contest: Path, last: int = 10) -> str:
    """Describe the experiment tree for an agent or the owner (`house exp status`).

    Args:
        contest: Contest checkout root.
        last: How many of the latest rows to list.

    Returns:
        Lines: `best: E-### <score> (<commit7>)` or `best: none`; counts per status; `stagnation: s/N` with
        ` → protocols/stagnation.md` once s reaches `limits.stagnation_n`; a note when the scorer changed (older
        rows are not compared) or the direction is unknown; then the latest rows as
        `E-### ← <parent> <status> <score> <duration>s <idea> <note>`.
    """
    rows = load(contest)
    if not rows:
        return "experiments: none yet; start with `house exp new <contest> --from best`\n"
    spec = _spec(contest)
    sense, current = direction(spec), scorer_hash(contest)
    b = best(rows, current, sense) if sense else None
    lines = [f"best: {b['id']} {b['score']} ({b['commit'][:7]})" if b else "best: none"]
    prev = previous_best(rows, current, sense) if sense and not b else None
    if prev:
        lines.append(f"previous scorer's best: {prev['id']} {prev['score']} (--from best continues from it)")
    counts = {k: sum(r["status"] == k for r in rows)
              for k in ("keep", "revert", "crash", "timeout", "running", "new")}
    lines.append(f"experiments: {len(rows)} (" + ", ".join(f"{k} {n}" for k, n in counts.items()) + ")")
    if sense is None:
        lines.append("direction unknown: set scoring.direction (max or min) in .house/spec.yaml")
    else:
        s, n = stagnation(rows, current, sense), _stagnation_limit(spec)
        lines.append(f"stagnation: {s}/{n}" + (" → protocols/stagnation.md" if s >= n else ""))
    older = [r for r in rows if r["status"] in SCORED and r["scorer"] != current]
    if older:
        lines.append(f"scorer changed: {len(older)} earlier scored row(s), up to {older[-1]['id']}, are not compared")
    for r in rows[-last:]:
        parent = r["parent"] if r["parent"].startswith("E-") else r["parent"][:7]
        extra = " ".join(x for x in (r["idea_id"], r["note"][:60]) if x)
        lines.append(f"{r['id']} ← {parent} {r['status']} {r['score'] or '-'} {r['duration'] or '-'}s {extra}".rstrip())
    return "\n".join(lines) + "\n"


def summary_line(contest: Path) -> str | None:
    """One line for the session-start hook: best experiment and stagnation; None without experiments."""
    rows = load(contest)
    if not rows:
        return None
    spec = _spec(contest)
    sense, current = direction(spec), scorer_hash(contest)
    if sense is None:
        return f"experiments: {len(rows)}; direction unknown (set scoring.direction)"
    b = best(rows, current, sense)
    s, n = stagnation(rows, current, sense), _stagnation_limit(spec)
    head = f"experiments: best {b['id']} = {b['score']}" if b else f"experiments: {len(rows)}, no comparable keep yet"
    return f"{head} · stagnation {s}/{n}" + (" → protocols/stagnation.md" if s >= n else "")


def prune(contest: Path, keep: int = 3) -> list[str]:
    """Remove worktrees of `keep` experiments outside the top `keep` (by score, current scorer first).

    Refs (`refs/exp/E-###`) and csv rows stay, so any experiment can be recreated with `git worktree add`.

    Args:
        contest: Contest checkout root.
        keep: How many of the best keep worktrees to leave.

    Returns:
        Ids whose worktrees were removed.
    """
    rows = load(contest)
    sense, current = direction(_spec(contest)) or "max", scorer_hash(contest)
    alive = [r for r in rows if r["status"] == "keep" and _worktree(contest, r["id"]).is_dir()]
    comparable = [r for r in alive if r["scorer"] == current and _score(r) is not None]
    ranked = sorted(comparable, key=_score, reverse=(sense == "max"))
    ranked += [r for r in alive if r not in comparable]
    removed = []
    for r in ranked[max(keep, 0):]:
        _remove_worktree(contest, _worktree(contest, r["id"]))
        removed.append(r["id"])
    return removed
