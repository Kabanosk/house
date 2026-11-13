"""`house merge`: the only way a task reaches the contest's main line.

check the task branch → merge → re-check every merged task on the result → revert on regression.
"""
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import checks
from .fm import set_fields

FINISHED = ("merged", "done")


@dataclass
class MergeOutcome:
    ok: bool
    status: str
    message: str
    reverted: bool = False


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def _set_status(task: checks.Task, status: str) -> None:
    task.path.write_text(set_fields(task.path.read_text(), {"status": status}))


def _commit_house(contest: Path, message: str) -> None:
    _git(contest, "add", "-A", "--", ".house")
    if _git(contest, "diff", "--cached", "--quiet", "--", ".house").returncode != 0:
        _git(contest, "commit", "-q", "-m", message, "--", ".house")


def _dirty_outside_house(contest: Path) -> list[str]:
    out = _git(contest, "status", "--porcelain", "--untracked-files=no").stdout
    return [line[3:] for line in out.splitlines() if line[3:] and not line[3:].startswith(".house/")]


def _failures(rep: checks.CheckReport) -> str:
    bad = [f"{r.id} ({'timeout' if r.exit is None else f'exit {r.exit}'})"
           for r in rep.results if rep.kinds[r.id] == "run" and not r.passes]
    if not rep.init_ok:
        bad.append("init.sh")
    if rep.mismatch:
        bad.append("criteria changed since freeze")
    return ", ".join(bad)


def _finished_tasks(house: Path) -> list[str]:
    out = []
    for path in sorted((house / "tasks").glob("T*-*.md")):
        tid = path.name.split("-", 1)[0]
        if checks.load_task(house, tid).meta.get("status") in FINISHED:
            out.append(tid)
    return out


def merge_task(contest: Path, task_id: str, worktree: Path | None = None) -> MergeOutcome:
    house = contest / ".house"
    task = checks.load_task(house, task_id)
    status = str(task.meta.get("status"))
    dirty = _dirty_outside_house(contest)
    if dirty:
        return MergeOutcome(False, status, "refused: uncommitted changes in the contest checkout: " + ", ".join(dirty))
    branch = task.meta.get("branch")
    if not branch:
        return MergeOutcome(False, status, f"{task_id} has no `branch` in its task file")
    wt = worktree or contest / ".worktrees" / str(branch).removeprefix("task/")
    if not wt.is_dir():
        return MergeOutcome(False, status, f"no worktree for {task_id} at {wt} (pass --worktree)")
    _commit_house(contest, f"chore(house): state before {task_id}")

    rep = checks.run_checks(house, task_id, wt)
    if not rep.green:  # check results stay as uncommitted .house state; the next merge commits them
        return MergeOutcome(False, status, f"{task_id} red on its branch: {_failures(rep)}")

    r = _git(contest, "merge", "--no-ff", "--no-edit", "-m", f"merge {task_id} ({branch})", str(branch))
    if r.returncode != 0:
        _git(contest, "merge", "--abort")
        return MergeOutcome(False, status, f"merge conflict merging {branch}; aborted, main unchanged:\n"
                            + (r.stdout + r.stderr).strip())

    regressions = []
    for tid in [task_id, *[t for t in _finished_tasks(house) if t != task_id]]:
        after = checks.run_checks(house, tid, contest)
        if not after.green:
            regressions.append(f"{tid}: {_failures(after)}")
    if regressions:
        _git(contest, "revert", "-m", "1", "--no-edit", "HEAD")
        _set_status(task, "blocked")
        with (house / "PROGRESS.md").open("a") as f:
            f.write(f"- {datetime.now():%Y-%m-%d %H:%M} {task_id} cofnięte po scaleniu: regresja w "
                    + "; ".join(regressions) + "\n")
        _commit_house(contest, f"chore(house): {task_id} reverted (regression)")
        return MergeOutcome(False, "blocked", f"{task_id} merged but broke checks on main, reverted: "
                            + "; ".join(regressions), reverted=True)

    missing = checks.seal_protected(house, task_id, contest)
    final = "done" if checks.run_checks(house, task_id, contest).complete else "merged"
    _set_status(checks.load_task(house, task_id), final)
    _commit_house(contest, f"chore(house): {task_id} {final}")
    notes = [f"protected file not found after merge: {m}" for m in missing]
    if wt.resolve().is_relative_to((contest / ".worktrees").resolve()):
        rm = _git(contest, "worktree", "remove", str(wt))
        if rm.returncode != 0:
            notes.append(f"worktree kept ({rm.stderr.strip()})")
    br = _git(contest, "branch", "-d", str(branch))
    if br.returncode != 0:
        notes.append(f"branch kept ({br.stderr.strip()})")
    return MergeOutcome(True, final, f"{task_id} {final}" + ("".join(f"\n  note: {n}" for n in notes)))
