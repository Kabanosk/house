# A2b: brief for the next attempt in `night` and the report template — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A night worker's second or third attempt starts from a hand-off instead of 600 characters of failure text: facts computed by code (A1), the worker's own Goal/Next/Constraints/Decisions from its last report, every dead end from all earlier attempts, the done-when condition and the task file — for Claude and Codex workers, capped at 8 000 characters.

**Architecture:** The report template moves to the eight hand-off sections (A2 template, `handoff.SECTIONS`) plus `Result vs acceptance`; `handoff.report_sections` reads old and new reports. Workers write a dead end into the report the moment they abandon an approach. `night` copies the report after every attempt to `reports/T###-attempt-N.md` and records it in the attempt register (A1). `night.next_brief` assembles the brief; Claude gets it in the prompt, Codex through `bin/codex-task --brief-file`.

**Tech Stack:** Python 3.11+ stdlib, `unittest`; consumes A1 (`attempts`, `evidence`) and A2a (`handoff`).

**Accepted:** Wojtek, 2026-11-17 (Signal), including `no report from attempt N` in English.

**Spec:** `docs/specs/2026-11-16-agno-adoptions-design.md` §8 (template, instruction rules, A2b, acceptance); order §5 step 3 (after A1).

## Global Constraints

- Python 3.11+, stdlib only; full suite green before each commit; English docstrings; synthetic data in tests.
- Section order and English headers: `Goal`; `Next` (ends with `Next action:` and `Done when:`); `Critical context` with `Observed` (each entry with its source) and `Unverified`; `Constraints and human decisions`; `Dead ends`; `Decisions and facts`; `Errors and fixes`; `Done` (each changed file, verified or not). Each section filled or `none`.
- Brief ceiling: **8 000 characters, enforced by code**, cut from the end; `Goal` and the next step always survive.
- Code-made parts come from A1 facts (state first, then the log) through `transcript.view`; nothing from the session content enters the brief except via the worker's own report.
- Dead ends: from **all** earlier attempts' report copies listed in `.house/attempts.jsonl`, each entry suffixed `(worker report, attempt N)`; duplicates removed only when identical after whitespace normalisation.
- Done-when written by code: Claude — `house check <contest> T### --worktree <path>` prints GREEN, then `status: review`; Codex — acceptance criteria pass in the working directory and the report is written (Codex does not commit nor set the status).
- Old reports (`What was done`, `Tested and did not work`, `Evidence`) still parse: `What was done` → `Done`, `Tested and did not work` → `Dead ends`; `Next` and `Goal` unchanged.
- Report copies `reports/T###-attempt-N.md` are tracked in git (spec §3); briefs live in `.house/tmp/night/<date>/T###-<time>-N.brief.md` (ignored).
- Missing report placeholder: `no report from attempt N` (English like all briefs, D21; accepted by Wojtek 2026-11-17).

## Review Focus

- Attempt 2 after a **timeout** of attempt 1 (report written mid-work, never finished) → its dead ends are in the brief.
- A report that never changed during an attempt (worker crashed before writing) → no copy, register `report` empty, brief says `no report from attempt N` but still has the code facts.
- A brief over 8 000 characters because of many long dead ends → cut from the end; `Goal`, `Next action:` and `Done when:` present; a final line says what was cut and where the full copies are.
- A Codex task falling back to a Claude worker (Codex not logged in) → gets the Claude done-when, not the Codex one.
- Report written in the old template by an existing contest → `Done` and `Dead ends` filled from the old headings.

---

### Task 1: report template and the section parser

**Files:** Modify `core/templates/house/reports/TEMPLATE.md`, `houselib/handoff.py` (new `report_sections`); Test `tests/test_handoff.py`, `tests/test_core_content.py`

**Interfaces:** Produces `report_sections(text: str) -> dict[str, str]` — English section name (from `SECTIONS`, plus `"Result vs acceptance"`) → body text, accepting the new headings, the Polish aliases and the old headings mapped as in Global Constraints; missing sections absent from the dict.

- [ ] Tests: template has the eight sections in order, `Observed` before `Unverified` under `Critical context`, then `Result vs acceptance`, and an evidence line naming `house evidence <contest> T### --attempt N`; no `## Evidence`; `report_sections` on a new-template report and on an old-template report (`What was done` → `Done`, `Tested and did not work` → `Dead ends`); unknown headings ignored.
- [ ] FAIL → implement → green → `git commit -m "feat(handoff): eight-section report template and report parser (A2b)"`

### Task 2: workers write dead ends as they go

**Files:** Modify `core/agents/coder.md` (steps 3–5), `core/agents/coder-codex.md`, `bin/codex-task` (the appended instruction); Test `tests/test_core_content.py`

- [ ] Tests: `coder.md`, `coder-codex.md` and the `codex-task` prompt text (via `--dry-run`) say: write the report in the template's sections; add a `Dead ends` entry **at the moment** you abandon an approach (reason, tries, "do not repeat"), not only at the end; keep `Observed` (with source) apart from `Unverified`.
- [ ] FAIL → edit → green → `git commit -m "docs(agents): report in hand-off sections, dead ends written when abandoned (A2b)"`

### Task 3: report copy per attempt

**Files:** Modify `houselib/night.py` (after every attempt, incl. timeout and interrupted: if `reports/T###.md` changed during the attempt — content hash at start vs end — copy it to `reports/T###-attempt-N.md`, N from the register; put the relative path in the register row's `report`; commit the copy with the night's own files); Test `tests/test_night.py`

**Interfaces:** Consumes `attempts.record` / row field `report` (A1). Produces the copies that Task 4 reads.

- [ ] Tests: worker writes a report then times out → copy exists and is in the row; worker does not touch the report → no copy, `report == ""`; two attempts → two copies with their own numbers; copies committed.
- [ ] FAIL → implement → green → `git commit -m "feat(night): keep a copy of the worker report per attempt (A2b)"`

### Task 4: `night.next_brief`

**Files:** Modify `houselib/night.py` (new `next_brief`); Test `tests/test_night.py`

**Interfaces:**
- Consumes: `attempts.rows`, `evidence.state_facts`, `evidence.log_facts` (A1), `handoff.report_sections`, `handoff.SECTIONS`.
- Produces: `next_brief(contest: Path, task_id: str, worktree: Path, n: int, tool: str) -> str` (`tool`: `claude` | `codex`) — Markdown with the eight sections: `Goal` and `Next`/`Constraints and human decisions`/`Decisions and facts` from the last attempt that has a report (else `no report from attempt N` in their place); `Critical context`/`Observed`, `Errors and fixes` (error tails), `Done` (changed files + `verified` when `check after last change` is GREEN) from A1 facts of the previous attempt; `Dead ends` from all earlier copies with `(worker report, attempt N)`; the task file path and the done-when line for `tool`; capped at 8 000 characters as in Global Constraints.
- [ ] Tests (spec A2b acceptance): third attempt contains dead ends from attempts 1 and 2 (2 ended by timeout, report written mid-work) with their numbers, a verbatim error message and the done-when; no report → code sections + `no report from attempt N`; over 8 000 → cut, Goal and next step kept; Codex done-when differs; old-template report parsed; canary from a session log never in the brief.
- [ ] FAIL → implement → green → `git commit -m "feat(night): hand-off brief for the next attempt (A2b)"`

### Task 5: wire the brief into workers

**Files:** Modify `houselib/night.py` (`run_night`: from attempt 2 on write `next_brief` to `.house/tmp/night/<date>/T###-<time>-N.brief.md`; Claude: the prompt from `brief()` gets the file's content instead of `previous[:600]`; Codex: `worker_command` passes `--brief-file <path>`), `bin/codex-task` (`--brief-file FILE`: its content is appended to the prompt under a heading), `README.md` (night section: what attempt 2+ receives); Test `tests/test_night.py`, codex-task dry-run test

- [ ] Tests: `worker_command` for `coder-codex` passes `--brief-file`; attempt 2 of a Claude task gets the brief text, attempt 1 does not; `codex-task --dry-run --brief-file f` prompt contains the brief; existing `test_retry_brief_carries_previous_failure` updated to the new behaviour.
- [ ] Carry-overs from the step-1 final review: README sentence about restored Q-### answers (only hand-edited ones since A2a fix F2); `handoff.compact_instructions`: "keep the done-when condition in Next" → "in your own words".
- [ ] FAIL → implement → green → `git commit -m "feat(night): attempt 2+ gets the hand-off brief, Codex via --brief-file (A2b)"`
