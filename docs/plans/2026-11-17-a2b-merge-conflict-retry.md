# A2b follow-up: a retry after a merge conflict brings `main` into the task branch — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a night attempt ends `merge-refused` because `git merge` into `main` conflicted, the next attempt starts
with `main` merged into the task's worktree (clean, or with conflict markers left for the worker) and a brief that says
exactly which files to resolve and that the driver commits the merge.

**Architecture:** `houselib/night.py`: before attempt N > 1 of a task whose previous attempt was a conflict
`merge-refused`, the driver runs `git merge --no-ff --no-commit main` in the worktree (as the night already does other git
work there). Clean → the merge stays staged (the driver's usual commit at the end of the attempt records it; the worker only
re-runs the check). Conflict → markers stay; `next_brief` gets a "merge in progress" fact with the conflicted paths (from
`git diff --name-only --diff-filter=U`) and the Next action "resolve the conflict markers in <paths> keeping both sides'
behaviour, run the check; do not commit, the driver commits the merge". The driver's end-of-attempt commit must handle an
in-progress merge (`MERGE_HEAD` present): it commits only when no unmerged paths remain, else the attempt ends `no-commits`
with the reason `unresolved merge conflict` and the merge is aborted (`git merge --abort`) so the next attempt starts clean.

**Tech Stack:** Python 3.11+ stdlib, `unittest`.

**Accepted:** Wojtek, 2026-11-17 (Signal, Q12: „Oki zrób rekomendację”). Evidence: K4 after night,
`docs/research/k4/2026-11-17-after.md` (T012: three `merge-refused` attempts, 41 % of the night's cost).

## Global Constraints

- Stdlib only; tests on synthetic git repos (temp contests) with a scripted fake worker; full suite green before commit.
- Only a conflict `merge-refused` triggers this (the merge gate's message for a conflict; other refusals — review failed,
  checks red after merge, tampered — keep today's behaviour). Detect the conflict from the attempt record, not by re-parsing
  free text if a structured field can be added to `Attempt`/the register row (preferred: a `reason: "conflict"` field).
- The task branch only ever receives `main`; never the reverse outside `merge.merge_task`.
- The worker still never commits (night rule); the brief wording stays consistent with that.
- Protected files: if a conflicted path is protected for the task, the attempt ends `blocked-by-worker` with the reason in
  the report instead of asking the worker to edit it (a human decides).

## Review Focus

- `main` moved again during attempt N (another task merged) → attempt N's merge may still conflict at the gate; the next
  attempt merges `main` again (idempotent).
- The worker leaves conflict markers in a file → the check is RED; the driver must not commit markers (unmerged paths
  block the commit; a file with markers but marked resolved is caught by the check, not by the driver).
- A merge that was clean but breaks the task's own criteria → normal RED → next attempt as today.
- The register and the night report show `merge-refused (conflict)` and, for the next attempt, `main merged: clean |
  conflict in N files`.

---

### Task 1: conflict-aware retry in the night driver

**Files:** `houselib/night.py` (detect conflict refusals, pre-attempt merge of `main`, end-of-attempt commit with
`MERGE_HEAD`), `houselib/attempts.py` (optional `reason` field), `houselib/merge.py` (expose that the refusal was a
conflict), tests in `tests/test_night.py` (or a new `tests/test_night_conflict.py`).
- [ ] Tests: conflict refusal recorded with reason; next attempt starts with main merged (clean case: worker only runs
  check → merged); conflict case: markers present, brief lists the paths, a fake worker resolves → driver commits a merge
  commit → gate merges; unresolved conflict → `no-commits` + merge aborted; protected conflicted path → `blocked-by-worker`;
  non-conflict refusals unchanged.
- [ ] Commit `fix(night): a retry after a merge conflict brings main into the task branch (A2b)`.

### Task 2: brief and docs

**Files:** `houselib/night.py` `next_brief` (the "merge in progress" fact and Next action), `docs/modules/night.md`
(Polish, a short paragraph), tests.
- [ ] Tests: brief for the conflict case names the paths and says the driver commits; the clean case says "main merged
  cleanly; re-run the check"; other outcomes unchanged.
- [ ] Commit `fix(night): the brief says how to finish a merge of main (A2b)`.
