# Cross-model review (P5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `house review` gets an independent review of a task branch or of everything since the last milestone from a
model family other than the author's (Codex for Claude-written work, Claude for Codex-written work), within a daily
Codex budget; tasks marked `review: always` cannot pass `house merge` without a review of their current head.

**Architecture:** `houselib/review.py` builds a blind brief, picks the reviewer, runs it headlessly in a read-only
mode with a time limit, verifies the output is a review, parses findings and writes `reports/review-<id>.md`.
`merge.py` enforces `review: always`. Vendored `second-opinion` skill (MIT) for interactive use.

**Tech Stack:** Python 3.11+ stdlib; `codex exec --sandbox read-only --skip-git-repo-check -C <repo> -o <out> -`;
`claude -p --allowedTools "Read,Grep,Glob,Bash(git diff:*),Bash(git log:*),Bash(git show:*)" --permission-prompts none`.

**Spec:** `docs/superpowers/specs/2026-11-12-system-hardening-design.md` §9 (P5).

## Global Constraints

- Brief is blind (goal + acceptance + the exact diff command; never the author's report) and contains none of the
  trigger phrases "second opinion", "counterpart", "blind review"; it says the reader is the only reviewer and must
  not run any agent, CLI or skill. Scope rule: review only `git diff <base>...<head>`; open other files only to
  verify a specific finding.
- Output format asked for: one finding per line `BLOCKER|MAJOR|MINOR|NIT <file>:<line> — <reason>`, then
  `VERDICT: <one line>`; "if nothing significant, say so plainly".
- Reviewer choice: author = `coder-codex` → Claude (`--model opus`); otherwise Codex while
  `limits.codex_reviews_per_day` (default 4, counted per local date in `state.json` `codex_reviews`) allows and
  Codex is available; else Claude in a fresh session, and the report says "same model family (weaker independence)".
- Liveness: before a Codex review, a nonce check (`cat <file>` inside the read-only sandbox, 120 s); failing → fall
  back to Claude and say why. Review time limit `review.minutes` (default 20); process group killed on timeout.
- Output that is empty, lacks `VERDICT:`, or reads like an apology/delegation → "review failed", never "no findings".
- `review: always` (task field; planner sets it for the local scorer, the submission pipeline and contracts between
  tasks): `house merge` refuses unless `reports/review-<task>.md` records the branch head and has no BLOCKER, or
  `--accept-review "<reason>"` is given (logged as D-###). `house night` runs the review before merging such tasks.
- Milestone review: `house review <contest> --since <ref|last>` reviews `<ref>...HEAD` on main; `state.json`
  `last_review_ref` updated after a successful run.

## Review Focus

- Codex not installed / not logged in / budget used → Claude reviews, marked as weaker independence.
- Reviewer hangs → killed at the limit, "review failed", merge of a `review: always` task still refused.
- Reviewer output is an apology ("couldn't complete…") → failed, not "clean".
- Task head moves after the review (new commits) → old review does not satisfy the gate.
- BLOCKER found, coordinator disagrees → `--accept-review` with a reason, D-### written.

### Task 1: `houselib/review.py`
`brief(contest, base, head, goal, acceptance) -> str`; `pick_reviewer(contest, author, tools, today) -> (kind, note)`;
`run_review(contest, kind, brief_text, repo, launcher=None) -> (ok, text, why)`; `parse(text) -> Findings(blockers,
majors, minors, nits, verdict)`; `review_task(contest, task_id, launcher=None) -> ReviewResult` and
`review_since(contest, ref, launcher=None)` writing reports. Tests with fake launchers: reviewer choice incl. budget
and author codex, brief has no trigger phrases and the diff command, apology/empty → failed, parse counts, report
records head sha, budget increments only for Codex runs.

### Task 2: merge gate + CLI + night
`merge.merge_task(..., accept_review: str | None = None)` enforcement; CLI `house review <contest> T###|--since
REF|last`, `house merge … --accept-review "<reason>"`; night runs `review_task` before merging `review: always` tasks.
Tests: refused without review, refused with stale head, refused with BLOCKER, accepted with reason (D-### written),
passes with clean review.

### Task 3: content + real run
`core/agents/reviewer.md` (neutral profile for the review role; the CLI is the mechanism), `house-plan` (mark
`review: always`), `house-run`/`house-judge` (milestone `house review --since last`), protocol note, README, vendored
skill listed in README. One real Codex review of a small diff in a scratch copy (uses 1 of the daily budget).
