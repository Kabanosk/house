"""`house merge`: the only way a task reaches the contest's main line.

refuse tampering → check the task branch → merge → re-check every merged task on the result → roll back on a
regression this merge caused.

Used by: `house merge <contest> T### [--worktree DIR]` (the coordinator, after a coder set `status: review`)
and by `house night` as its gate. Coders never merge; nobody edits `merged`/`done` statuses by hand.

What `house merge` refuses (nothing changes on main; exit code 1):
- the task is not frozen, or its status is not `review` (status flow: todo → doing → review → merged → done,
  or blocked);
- uncommitted changes in the contest checkout outside `.house/`, or in the task worktree;
- tampering with frozen criteria or hand-set `merged`/`done` statuses, and guard violations in the branch diff
  (changed protected files, secrets in any commit, `.house/` files carried by the branch);
- a task without `branch`, or without a worktree;
- a task that is red on its own branch, or a merge conflict.
After a successful merge every merged/done task is re-checked on main; a regression caused by this merge is
reverted, the task becomes `blocked` and its branch is rebased onto the new main on the next attempt.
The task ends as `done` when all its criteria pass (manual ones included), otherwise `merged`.
"""
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import checks, guard, review
from .fm import parse_frontmatter, set_fields

FINISHED = ("merged", "done")


@dataclass
class MergeOutcome:
    """Result of `merge_task`.

    Attributes:
        ok: True when the task was merged.
        status: The task's status afterwards ("done", "merged", "blocked"), or the unchanged one when refused
            ("?" when the task could not even be loaded).
        message: What happened, including `note:` lines for non-fatal problems; shown by `house merge`.
        reverted: True when the merge was made and then cleanly undone (regression or crash in the re-checks).
    """
    ok: bool
    status: str
    message: str
    reverted: bool = False


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Run `git <args>` in cwd and return the CompletedProcess (text mode, never raises on a non-zero exit)."""
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def _set_status(task: checks.Task, status: str) -> None:
    """Set the `status` field of the task file."""
    task.path.write_text(set_fields(task.path.read_text(), {"status": status}))


def _commit_house(contest: Path, message: str) -> None:
    """Commit all pending changes under `.house/` with the given message, if there are any."""
    _git(contest, "add", "-A", "--", ".house")
    if _git(contest, "diff", "--cached", "--quiet", "--", ".house").returncode != 0:
        _git(contest, "commit", "-q", "-m", message, "--", ".house")


def _porcelain_paths(cwd: Path, *extra: str) -> list[str]:
    """List paths reported by `git status --porcelain` in cwd (extra args are passed to git status)."""
    out = _git(cwd, "-c", "core.quotePath=false", "status", "--porcelain", "-z", *extra).stdout
    return [entry[3:] for entry in out.split("\0") if len(entry) > 3]


def _dirty_outside_house(contest: Path) -> list[str]:
    """List tracked files with uncommitted changes outside `.house/` in the contest checkout."""
    return [p for p in _porcelain_paths(contest, "--untracked-files=no") if not p.startswith(".house/")]


def _failures(rep: checks.CheckReport) -> str:
    """Describe what is red in a `CheckReport` as a comma-separated string."""
    bad = [f"{r.id} ({'timeout' if r.exit is None else f'exit {r.exit}'})"
           for r in rep.results if rep.kinds[r.id] == "run" and not r.passes]
    if not rep.init_ok:
        bad.append("init.sh")
    if rep.mismatch:
        bad.append("criteria changed since freeze")
    return ", ".join(bad)


def _finished_frozen(house: Path) -> tuple[list[str], list[str]]:
    """Split merged/done tasks by whether they have frozen criteria.

    Args:
        house: Path of the contest's `.house/` directory.

    Returns:
        `(tasks with frozen criteria, tasks from before freezing existed)`.
    """
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


def _review_gate(contest: Path, task_id: str, branch: str, accept_review: str | None) -> str | None:
    """Refusal message for a `review: always` task without a usable review of its current head, else None.

    A review with BLOCKER or MAJOR findings passes only with `accept_review` (a reason, logged as D-###).
    """
    rep = review.report_for(contest, task_id)
    head = _git(contest, "rev-parse", branch).stdout.strip()
    if not rep:
        return f"refused: {task_id} needs a review (review: always); run: house review <contest> {task_id}"
    if rep.get("head") != head:
        return (f"refused: the review of {task_id} is for head {rep.get('head', '?')[:8]}, the branch is at "
                f"{head[:8]}; run house review again")
    if not rep.get("status", "").startswith("ok"):
        return f"refused: the last review of {task_id} failed ({rep.get('status')}); run house review again"
    # review: always marks the scorer, the submission pipeline and contracts: a MAJOR there is a wrong score
    blockers = int(rep.get("blockers", "0") or 0) + int(rep.get("majors", "0") or 0)
    if blockers and not accept_review:
        return (f"refused: the review of {task_id} found {rep.get('blockers', '0')} BLOCKER and "
                f"{rep.get('majors', '0')} MAJOR finding(s) (review: always task); fix them, or merge with "
                "--accept-review \"<why the findings are wrong>\"")
    if blockers:
        decisions = contest / ".house/DECISIONS.md"
        text = decisions.read_text() if decisions.exists() else "# Dziennik decyzji\n"
        did = checks._next_decision_id(text)
        decisions.write_text(text.rstrip("\n") + f"\n\n## {did} Akceptacja recenzji {task_id} mimo BLOCKER\n\n"
                             f"- **Decyzja:** scalić {task_id} mimo {blockers} uwag BLOCKER/MAJOR w recenzji\n"
                             f"- **Dlaczego:** {accept_review}\n- **Kto:** coordinator\n"
                             f"- **Data:** {datetime.now():%Y-%m-%d %H:%M}\n")
    return None


def merge_task(contest: Path, task_id: str, worktree: Path | None = None,
               accept_review: str | None = None) -> MergeOutcome:
    """Gate a reviewed task branch into main, or refuse.

    Args:
        contest: Path of the contest checkout (the directory that holds `.house/`); its current branch is the target.
        task_id: Task id, e.g. "T001"; the task must be frozen and have `status: review`.
        worktree: The task's worktree; defaults to `<contest>/.worktrees/<branch without task/>`. Its changes must
            be committed. After a successful merge the worktree and branch are removed when possible.

    Returns:
        A `MergeOutcome`; never raises for refusals. A KeyboardInterrupt during the re-checks undoes the merge
        and is re-raised. Steps: refuse (see module docstring) → commit pending `.house/` state → rebase a branch
        that was reverted before → run the task's checks on its branch → `git merge --no-ff` → re-run checks of the
        task and every merged/done task on main (tasks already red before the merge are only reported) → on a
        regression revert and block the task → otherwise seal protected files, set `done` or `merged`, commit.
    """
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
    tampered = guard.frozen_tampering(contest)
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
    violations = guard.diff_violations(contest, task_id, wt)
    if violations:
        return MergeOutcome(False, status, "refused by guard: " + "; ".join(violations))
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

    if str(task.meta.get("review")) == "always":
        problem = _review_gate(contest, task_id, str(branch), accept_review)
        if problem:
            return MergeOutcome(False, status, problem)

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
    except KeyboardInterrupt:  # Ctrl+C during the re-checks: undo the merge, then stop
        _rollback(contest, pre_head)
        raise
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
