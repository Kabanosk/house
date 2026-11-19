# A1: attempt register and `house evidence` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every attempt at a task (night worker or interactive `house merge`) leaves a content-free row in `.house/attempts.jsonl` with one attempt number across nights, and `house evidence` computes, on demand and from facts only, what that attempt did; the night report shows a ≤5-line short form per attempt.

**Architecture:** `houselib/attempts.py` owns the register (append, next number, read). `houselib/evidence.py` computes facts: first from state (QUESTIONS diff, `git diff --stat`, last check, guard violations), then from the stream log through `transcript` (A0) and its privacy view; it renders a full and a short form. `night.run_night` records every finished attempt and names logs with the register's number; `cli.cmd_merge` records interactive gate runs. `house evidence` prints to stdout only; nothing of the relation is stored.

**Tech Stack:** Python 3.11+ stdlib, `unittest`; A0 `houselib/transcript.py`.

**Accepted:** Wojtek, 2026-11-17 (Signal). Amended 2026-11-17 after the A0 review (guard refusal text, Codex `file_change`).

**Spec:** `docs/specs/2026-11-16-agno-adoptions-design.md` §7 (A1), §3 (git policy: `attempts.jsonl` tracked, relation not stored); order §5 step 3 (A1, then A2b).

## Global Constraints

- Python 3.11+, stdlib only; full suite green before each commit; English docstrings; synthetic logs only.
- The gate (`house check` / `merge.merge_task`) stays the only source of the result; evidence only explains. Missing data → the fact says "unknown", never "pass". `is_error = None` never counts as success.
- `.house/attempts.jsonl` row (tracked in git): `task`, `n`, `source` (`night` | `merge`), `agent`, `tool` (`claude` | `codex`), `session` (Claude id; Codex `thread_id` from the log when known; `""` for `merge`), `log` (path relative to the contest, `""` for `merge`), `start`, `end` (ISO), `base`, `head` (commits), `outcome`, `message` (≤300 chars, `guard.redact`ed), `report` (`""`; A2b fills it). No absolute paths, no session content.
- Worker must not write `attempts.jsonl`: add it to what `guard.check_edit` refuses and to what `guard.diff_violations` reports (like `.house/checks/`).
- Everything printed from the log passes `transcript.view`; Bash commands ≤300 chars, redacted; error tails ≤5 lines, each ≤200 chars, redacted.
- `MAX_ATTEMPTS` keeps counting within one night (behaviour of `blocked` unchanged); the displayed number, log name and register use `n` from the register.
- Guard refusals: Claude only; for Codex the fact reads "not applicable", not "0".

## Review Focus

- First night on a contest that already has merged tasks but no `attempts.jsonl` → first row has `n = 1`; file created; committed with the night's own files (not `git add -A .house` from night).
- A task attempted by `house merge` interactively, then by the night → numbers continue (2, 3…), sources differ.
- Log file missing or empty (worker failed to start, `could not start`) → evidence prints the state facts and "log: not available", exit 0.
- `house check` run through a wrapper (`cd wt && /path/bin/house check c T001 --worktree .`, `python3 bin/house check …`) → still recognised via `guard._segments` (file name `house`, sub-command `check`).
- Worker report claims "pass" under the old template heading (`## Result vs acceptance`) or the new one, in any case → claim detected; report without that section → no "unbacked claim" line.

---

### Task 1: `houselib/attempts.py` — the register

**Files:** Create `houselib/attempts.py`; Test `tests/test_attempts.py`

**Interfaces:**
- Produces:
  - `@dataclass Row` with the fields listed in Global Constraints (all `str` except `n: int`).
  - `next_n(house: Path, task: str) -> int` — 1 + highest `n` for the task (broken lines skipped).
  - `record(house: Path, row: Row) -> Path` — appends one JSON line (`message` cut to 300 and redacted; `log` must be relative — an absolute path is made relative to `house.parent` or replaced by its file name).
  - `rows(house: Path, task: str | None = None) -> list[Row]`; `find(house, task, n) -> Row | None`.
- [ ] **Step 1: failing tests:** `test_next_n_empty_then_increments`; `test_numbers_continue_across_sources` (merge row n=1, night row n=2); `test_paths_relative_and_message_redacted` (absolute log under the contest → relative; message with `sk-…` → `[REDACTED]`, length ≤300); `test_broken_line_skipped`.
- [ ] **Step 2–4:** FAIL → implement → green. **Step 5:** `git commit -m "feat(attempts): attempt register .house/attempts.jsonl (A1)"`

### Task 2: guard protects the register

**Files:** Modify `houselib/guard.py` (`check_edit`, `diff_violations`); Test `tests/test_guard.py`

- [ ] **Step 1: failing tests:** `test_edit_attempts_register_refused` (`check_edit(".house/attempts.jsonl", c)` → reason mentions "written only by house"); `test_branch_changing_register_is_violation`.
- [ ] **Step 2–4:** FAIL → implement → green. **Step 5:** `git commit -m "feat(guard): attempts.jsonl is written only by house (A1)"`

### Task 3: facts from state

**Files:** Create `houselib/evidence.py`; Test `tests/test_evidence.py` (fixture contest from `tests/helpers.py`, real git)

**Interfaces:**
- Consumes: `attempts.Row`; `checks` results in `.house/checks/T###.json`; `guard.diff_violations`; `questions.parse`.
- Produces: `@dataclass Facts` with `questions_added: list[str]` (Q-### ids new between `base` and `head` of `QUESTIONS.md`, or vs the working copy when `head` is empty), `diffstat: str` (`git diff --stat base..head`, last line only when long), `last_check: str` (`GREEN` | `RED (AC-1, …)` | `unknown`), `violations: list[str]`, plus the log facts of Task 4 (defaults: unknown); `state_facts(contest: Path, row: Row) -> Facts`.
- [ ] **Step 1: failing tests:** `test_state_facts_new_question` (worker adds Q-002 between base and head → `["Q-002"]`); `test_state_facts_diffstat_and_last_check`; `test_state_facts_missing_checks_file_is_unknown`.
- [ ] **Step 2–4:** FAIL → implement → green. **Step 5:** `git commit -m "feat(evidence): facts from contest state (A1)"`

### Task 4: facts from the log

**Files:** Modify `houselib/evidence.py`; Test `tests/test_evidence.py` (synthetic Claude stream and Codex logs)

**Interfaces:**
- Consumes: `transcript.claude_stream`, `transcript.codex_stream`, `transcript.view` (A0); `guard._segments`.
- Produces (on `Facts`): `check_after_last_edit: str` (`yes, GREEN` | `yes, RED` | `yes, unknown` | `no` | `unknown`; last successful `Edit`/`Write`/`MultiEdit`/`NotebookEdit` vs last `house check` Bash call, result from the tool result's `is_error`: False → GREEN, True → RED, None → unknown); `unbacked_claim: bool` (report section `Result vs acceptance` matches `\b(pass(ed|es)?|green|ok)\b` case-insensitively and `check_after_last_edit` is not `yes, GREEN`); `test_edits: list[str]` (successful edits of `tests/…`, `*_test.*`, `test_*.*` and of the task's `protected` files; Claude: `Edit`/`Write`/`MultiEdit`/`NotebookEdit` with `is_error` False; Codex: `item.completed` of type `file_change` — today counted as unknown by `codex_stream`, so this task maps it to an event first, with a test on a synthetic Codex log); `guard_refusals: int | None` (error tool results whose text — `transcript.result_text` — contains `house guard:`; on 2.1.326 the text starts with `PreToolUse:` and the reason appears later, so not `startswith`; None for Codex → printed "not applicable"); `repeats: list[tuple[str, int]]` (same redacted command failed ≥3 times); `dead_end_repeats: list[str]` (for `n ≥ 2`: commands that failed identically in an earlier attempt's log of the same task, from the register); `calls: list[str]` (viewed tool calls in order, max 20 + "and K more"); `error_tails: list[str]`; `model`, `duration`, `n_calls`; `log_facts(contest: Path, row: Row, facts: Facts) -> Facts`.
- [ ] **Step 1: failing tests:** `test_no_check_after_last_edit` ("no" stated); `test_check_after_edit_green_and_red`; `test_house_check_via_wrapper_detected`; `test_unbacked_claim_line` (report "Result vs acceptance: pass", no GREEN in log → True); `test_codex_guard_not_applicable`; `test_guard_refusal_text_prefixed` (refusal text `PreToolUse:… house guard: …` counted); `test_codex_file_change_test_edit`; `test_repeated_failing_command`; `test_dead_end_repeat_across_attempts` (attempt 1 and 2 logs with the same failing `pytest …` → listed for attempt 2); `test_is_error_none_not_success`; **`test_canaries`** (A0 canary log → full and short evidence contain no canary, token shows `[REDACTED]`).
- [ ] **Step 2–4:** FAIL → implement → green. **Step 5:** `git commit -m "feat(evidence): facts from the stream log (A1)"`

### Task 5: rendering and `house evidence`

**Files:** Modify `houselib/evidence.py`, `houselib/cli.py` (new `cmd_evidence`, parser), `README.md`; Test `tests/test_evidence.py`, `tests/test_check_cli.py`-style CLI test

**Interfaces:**
- Produces: `render_full(facts, row) -> str`; `render_short(facts, row) -> list[str]` (≤5 lines: check after last edit + result; unbacked claim line `claim without backing in the log` when true; guard refusals; repeats; test edits — only non-empty lines, at least the first); `house evidence <contest> T### [--attempt N]` (default: last attempt) and `house evidence <contest> --session <id>` (interactive session found with `transcript.contest_sessions`; stdout only). Exit 0 with output; 1 when the task/attempt/session is unknown.
- [ ] **Step 1: failing tests:** `test_short_form_at_most_five_lines`; `test_cli_evidence_last_attempt`; `test_cli_evidence_unknown_attempt_exit_1`; `test_session_mode_writes_nothing` (no new files under the contest after the call).
- [ ] **Step 2–4:** FAIL → implement → green. **Step 5:** `git commit -m "feat(evidence): house evidence command (A1)"`

### Task 6: wire the register into `night` and `house merge`; short form in the night report

**Files:** Modify `houselib/night.py` (`run_night`: `n_reg = attempts.next_n(...)` at start, used in the log name and `Attempt.n` shown in the report; internal per-night counter kept for `MAX_ATTEMPTS`; after every finished attempt — merged, failed, `could not start`, interrupted — `attempts.record` with `base` = branch merge-base at start, `head` = branch head at end; `_write_report` adds per attempt the short form and `house evidence <contest> T### --attempt N`; `attempts.jsonl` committed with the night's own files), `houselib/cli.py` (`cmd_merge` records a `merge` row after `merge.merge_task`, ok or refused); Test `tests/test_night.py`, `tests/test_merge.py`

- [ ] **Step 1: failing tests:** `test_register_rows_from_night` (fake launcher, one merged and one failing task → rows with `source == "night"`, relative `.jsonl` logs, numbers 1..k); `test_numbers_continue_over_two_nights` (two `run_night` calls on the same contest → the failing task's numbers 1,2 then 3; `blocked` still after 3 failures within a night); `test_night_report_has_short_form_and_command`; `test_cli_merge_records_row` (refused merge → row `source == "merge"`, `session == ""`, `log == ""`); `test_canaries_in_night_report` (worker log with canaries → report clean).
- [ ] **Step 2–4:** FAIL → implement → green. **Step 5:** `git commit -m "feat(night,merge): attempt register rows and short evidence in the night report (A1)"`

### Task 7: `/house-retro` and judge use `house evidence`

**Files:** Modify `adapters/claude-code/skills/house-retro/SKILL.md` (the "what the agent did" part: `house evidence <contest> T### --attempt N` per attempt from `.house/attempts.jsonl`, `--session <id>` for interactive sessions; conversation content still read from the session logs as today), `core/agents/judge.md` (one line: may use `house evidence`); Test `tests/test_core_content.py` (both mention `house evidence`)

- [ ] **Step 1–4:** failing content test → edit → green. **Step 5:** `git commit -m "docs(retro,judge): use house evidence for what agents did (A1)"`
