# P7 `house exp`: experiment loop for optimize contests — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every optimize experiment is a small change of a chosen parent, run with a fixed time budget, scored by
the contest's local scorer, and kept or reverted automatically, with every attempt recorded in
`.house/experiments.csv` (a tree via `parent`) and stagnation computed from that file, not from agent memory.

**Architecture:** New module `houselib/exp.py` (ids, csv, worktrees, run, score, decision, status, prune), with CLI
`house exp new|run|status|prune`. Experiments live in detached worktrees `.worktrees/exp-E-###`; every scored commit
is pinned by a hidden ref `refs/exp/E-###`. The session-start hook shows one experiments line. Content: optimize
playbook, stagnation and submissions protocols, spec template, README.

**Tech Stack:** Python 3.11 stdlib, git, unittest.

**Spec:** `docs/superpowers/specs/2026-11-12-system-hardening-design.md` §11 (P7). The spec is a sketch ("details
at the first optimize contest"); decisions it leaves open are made below as **[ruling]**.

## Global Constraints

- Stdlib only; Google-style docstrings on every public function (what it is for agents, arguments, returns).
- Columns of `experiments.csv` from the spec: `id, parent, commit, idea_id, budget, duration, score, status`;
  extra columns are appended after them: `scorer, time, note`.
- `best` = the best `keep`; better than the parent = `keep`, otherwise `revert` (spec §11).
- Submissions still go through `/house-submit` and `submissions.csv`; the local score comes from the experiment.
- The scorer and the frozen acceptance tests (`guard.protected_paths`) must not be changed by an experiment.
- Tests never touch real Signal (`tests.helpers` import).

## Decisions [ruling]

1. **Two steps plus a one-shot.** `house exp new <c> --from best|E-###|<commit>` creates the worktree and prints
   `E-### <path>`. The agent edits code there. `house exp run <c> E-### -- <cmd>` commits the change and runs it.
   `house exp run <c> --from … -- <cmd>` without an id does both in one go (parameter sweeps where the change is
   the command line). This keeps the spec's signature and adds the edit step it needs.
2. **Hidden refs instead of `exp/<n>` branches.** A refs/exp/E-### ref per experiment keeps every attempt
   reproducible without hundreds of branches in `git branch` and without pushing them to a team remote.
3. **Status values:** `new` (created, not run), `keep`, `revert`, `crash` (non-zero exit, scorer failure,
   tampering), `timeout`.
4. **Score:** the scorer's output is read for the last `score: <number>` / `score=<number>` (case-insensitive);
   if there is none, the last non-empty line must be a single number; else `crash` with a note telling what to
   print.
5. **Direction:** `scoring.direction` (`max`|`min`); if absent, a word `min`/`max` (also `minimize`, `maximize`)
   in `scoring.metric`; if still unknown, `house exp run` refuses with "set scoring.direction".
6. **Scorer integrity and comparability.** Before scoring: if the experiment changed any protected file against
   its parent → `crash` "tampered". The protected files are then overwritten in the worktree with the contest
   checkout's versions for scoring (and restored afterwards), so every score uses the current scorer. The row
   stores `scorer` = short sha256 of those files; `best`, parent comparison and stagnation use only rows with the
   current scorer hash. A parent scored with another scorer counts as unscored.
7. **Parent without a comparable score** (a raw commit, a crash being debugged, an old scorer): a scored run is
   `keep` (it becomes a root or a repaired node).
8. **Ties are `revert`.**
9. **Worktrees:** removed after `revert`/`crash`/`timeout`, kept after `keep` (outputs such as predictions or
   weights live there and may be submitted). `house exp prune <c> --keep 3` removes keep worktrees outside the
   top 3; refs stay.
10. **Data outside git:** `exp.shared` (list of contest-relative paths, default `[]`) are symlinked into each new
    worktree when they exist in the contest and not in the worktree; `HOUSE_CONTEST` and `HOUSE_EXP` are set for
    the command and the scorer.
11. **Stagnation** = scored rows (`keep`/`revert`, current scorer) after the row that last set a new best;
    crashes and timeouts do not count (a typo must not force a pivot). Flagged at `limits.stagnation_n`.
12. **Budget:** `--budget` like `90s`, `5m`, `1h30m` or plain seconds; default `exp.budget` (`5m`). Applies to the
    command; the scorer has `exp.scorer_minutes` (default 10). Timeout kills the process group.
13. **Bookkeeping:** id allocation and csv writes under an exclusive lock `.house/experiments/lock`; logs in
    `.house/experiments/E-###.log` (capped 1 MB). `.house/experiments/` and `.worktrees/` are added to
    `.git/info/exclude`. Only `.house/experiments.csv` is committed on the contest checkout (pathspec commit).
14. **Exit codes:** `run` 0 keep, 1 revert/crash/timeout/refusal, 2 usage; `new`, `status`, `prune` 0/2.

## Review Focus

1. An experiment that edits `score.py` (or a frozen test) to inflate its score → `crash` "tampered", never `keep`.
2. The scorer changed on the contest checkout after earlier experiments → old scores not compared with new ones.
3. A command that spawns a background child and exceeds the budget → killed, `timeout`, worktree removed.
4. A scorer printing other numbers after the score (`score: 0.8`, then `n=1000`) → 0.8, not 1000.
5. Two agents creating experiments at once → distinct ids, no lost csv rows.

---

### Task 1: core module — ids, csv, budget, direction, score parsing

**Files:** Create `houselib/exp.py`, `tests/test_exp.py`; modify `houselib/gitutil.py` (add `exclude`),
`houselib/adapters.py` (use it).

**Interfaces (Produces):**
- `COLUMNS = ["id","parent","commit","idea_id","budget","duration","score","status","scorer","time","note"]`
- `parse_budget(text: str) -> int` (seconds; `ValueError` on junk or 0)
- `direction(spec: dict) -> str | None` (`"max"`/`"min"`)
- `parse_score(output: str) -> float | None`
- `load(contest: Path) -> list[dict]` (missing file → `[]`), `_write(contest, rows)`, `_locked(contest)` context
  manager, `next_id(rows) -> str` (`E-001`…)
- `best(rows, scorer: str, sense: str) -> dict | None`; `stagnation(rows, scorer, sense) -> int`
- `gitutil.exclude(repo: Path, pattern: str) -> None` (idempotent append to `info/exclude`, works in worktrees)

- [ ] Tests: `parse_budget("90s")==90`, `"5m"==300`, `"1h30m"==5400`, `"45"==45`, `"x"`/`"0"` raise;
  `direction({"scoring":{"direction":"min"}})=="min"`, metric `"RMSE (min)"`→min, `"F1, maximize"`→max, nothing →
  None; `parse_score("score: 0.8\nn=1000\n")==0.8`, `"SCORE=1.5e-3"`, `"loss\n0.25\n"==0.25`, `"done\n"` → None;
  `best`/`stagnation` on a hand-made row list (keep 0.5, revert 0.4, keep 0.7 [new best], revert 0.6, crash,
  revert 0.65 → best 0.7, stagnation 2; with `sense="min"` best is the lowest keep, 0.5);
  rows with another scorer hash ignored; `next_id` after `E-009` → `E-010`; `exclude` twice → one line.
- [ ] Run RED, implement, GREEN. Move `adapters.write_local_settings`' exclude code onto `gitutil.exclude`; its
  tests stay green.
- [ ] Commit `feat(exp): experiment records, budget, direction and score parsing`.

### Task 2: worktrees, run, decision

**Files:** `houselib/exp.py`, `tests/test_exp.py`.

**Interfaces (Produces):**
- `new(contest, parent="best", idea="", note="") -> tuple[str, Path]` — resolves `best` (falls back to `HEAD`
  when there is no comparable keep), `E-###`, raw commit or ref; detached worktree `.worktrees/exp-E-###`; links
  `exp.shared`; row with status `new`; `ValueError` on an unknown parent.
- `run(contest, exp_id, command: list[str] | str, budget: int | None = None) -> Result` where
  `Result(id, status, score, parent_score, note, log: Path)`; `ValueError` if the id is not `new` or direction is
  unknown.
- `run_new(contest, parent, command, budget=None, idea="", note="") -> Result`.

- [ ] Tests (temp contest with `score.py` reading `out.txt`, `scoring.local_scorer: python3 score.py`,
  `scoring.direction: max`, frozen scorer via `guard`):
  `test_first_run_from_head_is_keep_and_pinned` (ref `refs/exp/E-001` == row commit, worktree kept, csv
  committed on the contest checkout), `test_better_child_keep_worse_revert` (child of best writes a higher /
  lower `out.txt`; revert removes the worktree, ref stays), `test_tie_is_revert`, `test_crash_and_timeout`
  (`exit 3` → crash; `sleep 30 & sleep 30` with budget 1 → timeout under 10 s), `test_tampered_scorer_is_crash`
  (experiment edits `score.py` to print 99), `test_scorer_change_makes_old_scores_incomparable` (change
  score.py on the checkout and commit; next child of E-001 is `keep` even with a lower number; `best` = it),
  `test_missing_score_is_crash_with_hint`, `test_unknown_direction_refuses`, `test_shared_data_is_linked`
  (`data/` untracked in contest, visible in the worktree, command reads it), `test_env_vars`
  (`HOUSE_EXP`, `HOUSE_CONTEST`), `test_concurrent_new_distinct_ids` (two threads → two ids, two rows).
- [ ] RED, implement, GREEN. Commit `feat(exp): experiment worktrees, budgeted run, scoring and keep/revert`.

### Task 3: status, prune, CLI, hook line

**Files:** `houselib/exp.py`, `houselib/cli.py`, `houselib/hook.py`, `tests/test_exp.py`, `tests/test_cli.py` or
`test_exp.py`.

**Interfaces (Produces):**
- `status(contest, last=10) -> str` — lines: `best E-### score (commit7)` or `best: none`; `experiments: N
  (keep k, revert r, crash c, timeout t, new n)`; `stagnation: s/N` + ` → protocols/stagnation.md` when s ≥ N;
  `scorer changed: rows before E-### are not compared` when hashes differ; then the last rows
  `E-### ← parent status score duration idea note`.
- `prune(contest, keep=3) -> list[str]` (removed ids).
- `summary_line(contest) -> str | None` for the hook: `experiments: best E-### = 0.873 · stagnation 2/3`
  (+ ` → protocols/stagnation.md`), None without rows.
- CLI: `house exp new <c> [--from REF] [--idea I] [--note T]`, `house exp run <c> [ID] [--from REF] [--budget B]
  [--idea I] [--note T] -- CMD...`, `house exp status <c> [-n N]`, `house exp prune <c> [--keep N]`.

- [ ] Tests: status text after a keep/revert/crash sequence; stagnation arrow; prune keeps top-N worktrees;
  CLI round trip through `cli.main` (`exp run --from HEAD -- python3 -c "…"` exit 0, then a worse one exit 1,
  `exp run` with neither id nor `--from` → 2); `hook.session_start` contains the experiments line.
- [ ] RED, implement, GREEN. Commit `feat(exp): house exp CLI, status, prune and the session-start line`.

### Task 4: content and docs

**Files:** `core/playbooks/optimize.md`, `core/protocols/stagnation.md`, `core/protocols/submissions.md`,
`core/templates/house/spec.yaml`, `core/templates/house/experiments.csv`, `README.md`,
`tests/test_core_content.py`.

- [ ] Tests: template has `experiments.csv` with the `COLUMNS` header; spec template has `scoring.direction`,
  `exp.budget`, `exp.scorer_minutes`, `exp.shared`; optimize playbook mentions `house exp new`, `house exp run`,
  `house exp status`; stagnation protocol says detection comes from `house exp status`.
- [ ] Playbook section "Experiments" (loop: new from best → small change → run with budget → keep/revert; debug a
  crash by `--from E-###`; one change per experiment; ideas get `I-###` ids from `ideas.md` and go to `--idea`);
  submissions protocol: submit a `keep` commit, `local_score` from its row, note `E-###`.
- [ ] README section `house exp`. Commit `docs(exp): playbook, protocols, templates and README`.
