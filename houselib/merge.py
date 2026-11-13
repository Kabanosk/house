"""`house merge`: the only way a task reaches the contest's main line.

refuse tampering → check the task branch → merge → re-check every merged task on the result → roll back on a
regression this merge caused.
"""
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import checks
from .fm import parse_frontmatter, set_fields

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


def _porcelain_paths(cwd: Path, *extra: str) -> list[str]:
    out = _git(cwd, "-c", "core.quotePath=false", "status", "--porcelain", "-z", *extra).stdout
    return [entry[3:] for entry in out.split("\0") if len(entry) > 3]


def _dirty_outside_house(contest: Path) -> list[str]:
    return [p for p in _porcelain_paths(contest, "--untracked-files=no") if not p.startswith(".house/")]


def _at_head(contest: Path, rel: str) -> str | None:
    r = _git(contest, "show", f"HEAD:{rel}")
    return r.stdout if r.returncode == 0 else None


def _tampering(contest: Path) -> list[str]:
    """Changes only `house` may make, found uncommitted (house commits them the moment it makes them)."""
    house = contest / ".house"
    found = []
    for path in sorted((house / "checks").glob("*.json")):
        rel = str(path.relative_to(contest))
        head = _at_head(contest, rel)
        try:
            now = json.loads(path.read_text())["frozen"]
            before = json.loads(head)["frozen"] if head else None
        except (ValueError, KeyError, TypeError):
            found.append(f"{rel} is unreadable")
            continue
        if before != now:
            found.append(f"frozen criteria in {rel} changed outside `house check --freeze/--refreeze`")
    for path in sorted((house / "tasks").glob("T*-*.md")):
        rel = str(path.relative_to(contest))
        try:
            now = parse_frontmatter(path.read_text())[0].get("status")
            head = _at_head(contest, rel)
            before = parse_frontmatter(head)[0].get("status") if head else None
        except ValueError:
            continue
        if now in FINISHED and before != now:
            found.append(f"{path.name.split('-', 1)[0]} set to `{now}` by hand (only house merge sets it)")
    return found


def _failures(rep: checks.CheckReport) -> str:
    bad = [f"{r.id} ({'timeout' if r.exit is None else f'exit {r.exit}'})"
           for r in rep.results if rep.kinds[r.id] == "run" and not r.passes]
    if not rep.init_ok:
        bad.append("init.sh")
    if rep.mismatch:
        bad.append("criteria changed since freeze")
    return ", ".join(bad)


def _finished_frozen(house: Path) -> tuple[list[str], list[str]]:
    """(merged/done tasks with frozen criteria, merged/done tasks from before freezing existed)."""
    frozen, legacy = [], []
    for path in sorted((house / "tasks").glob("T*-*.md")):
        tid = path.name.split("-", 1)[0]
        meta = parse_frontmatter(path.read_text())[0]
        if meta.get("status") in FINISHED:
            (frozen if checks.is_frozen(house, tid) else legacy).append(tid)
    return frozen, legacy


def _rollback(contest: Path, pre_head: str) -> str | None:
    """Undo the merge: revert (keeps history), else reset --keep. Returns an error if both fail."""
    if _git(contest, "revert", "-m", "1", "--no-edit", "HEAD").returncode == 0:
        return None
    _git(contest, "revert", "--abort")
    r = _git(contest, "reset", "--keep", pre_head)
    if r.returncode == 0:
        return None
    return f"could not undo the merge (revert and reset --keep failed: {r.stderr.strip()}); fix main by hand"


def _red_before(contest: Path, house: Path, task_ids: list[str]) -> set[str]:
    """Which of task_ids were already red on main before this merge (checked in a throwaway worktree)."""
    if not task_ids:
        return set()
    tmp = contest / ".worktrees" / ".house-baseline"
    _git(contest, "worktree", "remove", "--force", str(tmp))
    if _git(contest, "worktree", "add", "--detach", str(tmp), "HEAD^1").returncode != 0:
        return set()
    try:
        return {t for t in task_ids if not checks.run_checks(house, t, tmp, record=False).green}
    finally:
        _git(contest, "worktree", "remove", "--force", str(tmp))


def merge_task(contest: Path, task_id: str, worktree: Path | None = None) -> MergeOutcome:
    house = contest / ".house"
    try:
        task = checks.load_task(house, task_id)
        if not checks.is_frozen(house, task_id):
            raise ValueError(f"{task_id} is not frozen; run: house check <contest> {task_id} --freeze")
        frozen_done, legacy = _finished_frozen(house)
    except (ValueError, FileNotFoundError) as e:
        return MergeOutcome(False, "?", f"refused: {e}")
    status = str(task.meta.get("status"))
    if status != "review":
        return MergeOutcome(False, status, f"refused: {task_id} is `{status}`; only `review` tasks are merged")
    dirty = _dirty_outside_house(contest)
    if dirty:
        return MergeOutcome(False, status, "refused: uncommitted changes in the contest checkout: " + ", ".join(dirty))
    tampered = _tampering(contest)
    if tampered:
        return MergeOutcome(False, status, "refused: " + "; ".join(tampered))
    branch = task.meta.get("branch")
    if not branch:
        return MergeOutcome(False, status, f"{task_id} has no `branch` in its task file")
    wt = worktree or contest / ".worktrees" / str(branch).removeprefix("task/")
    if not wt.is_dir():
        return MergeOutcome(False, status, f"no worktree for {task_id} at {wt} (pass --worktree)")
    if _porcelain_paths(wt):
        return MergeOutcome(False, status, f"refused: uncommitted changes in the worktree {wt}; commit them first")
    _commit_house(contest, f"chore(house): state before {task_id}")

    main_branch = _git(contest, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    data = json.loads((house / "checks" / f"{task_id}.json").read_text())
    if data.get("reverted"):  # replay every commit since the old fork point as new commits; otherwise git
        # treats the reverted ones as already merged and brings only the newer ones
        rb = _git(wt, "rebase", "--onto", main_branch, data["reverted"]["base"])
        if rb.returncode != 0:
            _git(wt, "rebase", "--abort")
            return MergeOutcome(False, status, f"{task_id} was reverted before and its branch does not rebase "
                                f"cleanly onto {main_branch}; rebase it by hand:\n" + rb.stderr.strip())

    rep = checks.run_checks(house, task_id, wt)
    if not rep.green:  # check results stay as uncommitted .house state; the next merge commits them
        return MergeOutcome(False, status, f"{task_id} red on its branch: {_failures(rep)}")

    pre_head = _git(contest, "rev-parse", "HEAD").stdout.strip()
    r = _git(contest, "merge", "--no-ff", "--no-edit", "-m", f"merge {task_id} ({branch})", str(branch))
    if r.returncode != 0:
        _git(contest, "merge", "--abort")
        return MergeOutcome(False, status, f"merge of {branch} failed (conflict?); aborted, main unchanged:\n"
                            + (r.stdout + r.stderr).strip())
    try:
        red_after = {}
        for tid in [task_id, *[t for t in frozen_done if t != task_id]]:
            after = checks.run_checks(house, tid, contest)
            if not after.green:
                red_after[tid] = _failures(after)
        already_red = _red_before(contest, house, [t for t in red_after if t != task_id])
        regressions = {t: why for t, why in red_after.items() if t not in already_red}
    except Exception as e:  # noqa: BLE001 — whatever broke, main must not keep an unchecked merge
        err = _rollback(contest, pre_head)
        return MergeOutcome(False, status, f"checks crashed after merging ({e!r}); merge undone"
                            + (f"\n{err}" if err else ""), reverted=err is None)

    if regressions:
        err = _rollback(contest, pre_head)
        _set_status(checks.load_task(house, task_id), "blocked")
        data = json.loads((house / "checks" / f"{task_id}.json").read_text())
        data["reverted"] = {"base": _git(contest, "merge-base", pre_head, str(branch)).stdout.strip()}
        (house / "checks" / f"{task_id}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        why = "; ".join(f"{t}: {w}" for t, w in regressions.items())
        with (house / "PROGRESS.md").open("a") as f:
            f.write(f"- {datetime.now():%Y-%m-%d %H:%M} {task_id} cofnięte po scaleniu: regresja w {why}\n")
        _commit_house(contest, f"chore(house): {task_id} reverted (regression)")
        return MergeOutcome(False, "blocked", f"{task_id} merged but broke checks on main, reverted: {why}"
                            + (f"\n{err}" if err else ""), reverted=err is None)

    missing = checks.seal_protected(house, task_id, contest)
    data = json.loads((house / "checks" / f"{task_id}.json").read_text())
    data.pop("reverted", None)
    (house / "checks" / f"{task_id}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    final = "done" if checks.run_checks(house, task_id, contest).complete else "merged"
    _set_status(checks.load_task(house, task_id), final)
    _commit_house(contest, f"chore(house): {task_id} {final}")
    notes = [f"protected file not found after merge: {m}" for m in missing]
    notes += [f"{t} was already red on main before this merge: {red_after[t]}" for t in sorted(already_red)]
    notes += [f"{t} is merged/done but has no frozen criteria (not re-checked)" for t in legacy]
    if wt.resolve().is_relative_to((contest / ".worktrees").resolve()):
        rm = _git(contest, "worktree", "remove", str(wt))
        if rm.returncode != 0:
            notes.append(f"worktree kept ({rm.stderr.strip()})")
    br = _git(contest, "branch", "-d", str(branch))
    if br.returncode != 0:
        notes.append(f"branch kept ({br.stderr.strip()})")
    return MergeOutcome(True, final, f"{task_id} {final}" + "".join(f"\n  note: {n}" for n in notes))
