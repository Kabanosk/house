# K0: `house cost` — one cost table per contest — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `house cost <contest> [--since DATE]` prints one row per session of the contest (interactive, night worker, Signal job) with calls, context sizes, the four token buckets, compactions, the "outside the record" column and a weighted cost, so K4 and the K2b decision rest on numbers.

**Architecture:** `houselib/cost.py` collects the contest's sources (interactive sessions via `transcript.contest_sessions`, night worker stream logs from `.house/attempts.jsonl`, Signal job logs when present), parses them with A0 (`transcript.load` for global dedup), groups events into session trees (parent + sub-agents), computes the row numbers and renders a table; `cli.cmd_cost` wires it. Numbers only — the A0 privacy view is never bypassed.

**Tech Stack:** Python 3.11+ stdlib, `unittest`; A0 `houselib/transcript.py`.

**Accepted:** Wojtek, 2026-11-17 (Signal, question 9).

**Spec:** `docs/specs/2026-11-16-short-context-design.md` §7 (K0) and §4 (weights); A0 in `docs/specs/2026-11-16-agno-adoptions-design.md` §6; order §5 step 5.

## Facts that shape it (verified 2026-11-17)

- An interactive session's `cost-state` covers **only the last process run**, from its `startTime` (A0 manual check, `session_total.since_ms`). "Outside the record" = `session_total − Σ usage` over usage events with `ts ≥ since_ms`; for stream logs (`since_ms` None) over the whole log.
- Codex `usage` is per turn, not per call: its rows show token sums and calls, but context first/median/peak are shown as `—` for Codex.
- Night worker logs are `.jsonl` stream logs named in the register rows; their session ids equal the session files in `~/.claude/projects` (`contest_sessions` finds those too) — `load()` drops the session file when the stream is present.

## Global Constraints

- Stdlib only; tests on synthetic sessions/streams; `python3 -m unittest discover -s tests` green before commit; English docstrings.
- Weights (spec §4, configurable in `spec.yaml` `cost.weights`, defaults): input 1, cache read 0.1, cache write 5m 1.25, cache write 1h 2, output 5.
- Footer always: `estimate of API cost (relative prices), not a bill and not subscription usage`.
- A row with no per-call usage (Signal job without usage, session without cost-state) shows what it has, marked `total only` or `no data`; never crash.
- Output to stdout only. No paths of private projects outside the contest.

## Review Focus

- Contest adopted under a symlink and sessions in `.worktrees/…` → assigned to the contest (A0 `contest_sessions`).
- Night worker session present both as a stream log and as a session file → one row.
- A resumed session (many process runs) → "outside the record" not negative (uses `since_ms`).
- `--since` excludes older sessions by their first event time; empty result → a clear "no sessions" line, exit 0.
- Sub-agent usage summed into the parent's row; a sub-agent-only file without its parent is its own row.

---

### Task 1: session trees and row numbers

**Files:** `houselib/cost.py` (`Row` dataclass: kind, date, main model, calls, context first/median/peak, input, cache_read, cache_write_5m, cache_write_1h, output, compactions, max pre_tokens, outside (dict model→tokens or None), weighted, note; `rows(contest, since=None, projects=…) -> list[Row]`; `weighted(row, weights)`), `tests/test_cost.py`.
- [ ] Tests (spec acceptance): a repeated record counted once; a session with a compaction (count + max `pre_tokens`); a session with a sub-agent summed in one tree; a night session counted once despite stream + session file; "outside" from a `cost-state` with an extra Haiku model; resumed session uses `since_ms`; contest under a symlink and a `.worktrees/` session assigned; no usage → `no data`; Codex row context `—`; fixture numbers equal hand-computed values.
- [ ] Commit `feat(cost): session rows from records (K0)`.

### Task 2: `house cost` CLI and table

**Files:** `houselib/cost.py` (`render(rows, weights) -> str` fixed-width table + footer), `houselib/cli.py` (`cmd_cost`, `house cost <contest> [--since YYYY-MM-DD]`), `docs/modules/` (a short section in `night.md` or a new `cost.md`, Polish) + README table row; tests.
- [ ] Tests: table columns and footer; `--since`; unknown contest → exit 1; weights from `spec.yaml` override defaults; no session content in output (canary).
- [ ] Commit `feat(cost): house cost command (K0)`.

### Task 3: manual check (numbers only)

- [ ] Run `house cost grok-bot-baseline` and `house cost grok-bot-wro-2026` locally; compare the night rows with the K4 baseline snapshot (`docs/research/k4/2026-11-17-baseline-snapshot.json`) — totals should agree within the dedup differences; record agreement (numbers only) in this plan.

## Manual check (2026-11-17, numbers only)

- `house cost grok-bot-baseline`: the three Sonnet night-worker sessions of the baseline night match the K4 snapshot exactly
  (calls 7 / 9 / 7, weighted 85 526 / 132 271 / 81 255). They show as `interactive`: the baseline night ran before the attempt
  register (A1), so house finds them only as session files. The night's Codex worker (snapshot: 9 turns, 82 531) is missing for
  the same reason (Codex sessions are found only through the register's `log`). The two Haiku rows are the K2a manual
  compaction trials (1 compaction each, peaks 95K and 170K), as expected.
- `house cost grok-bot-wro-2026` (the owner's checkout): the two Opus sessions of 2026-11-05 match the snapshot exactly (calls
  212 / 321, weighted 4 265 431 / 4 783 872). The interactive Codex sessions of that contest (24 and 6 turns in the snapshot) are
  not found — interactive Codex sessions are outside K0's sources (spec §7 lists interactive Claude, night workers, Signal jobs).
- "Outside the record" for those two sessions: 2 779K and 3 362K tokens (about 13 % of their recorded tokens), i.e. tokens the
  `cost-state` counts that no usage record carries. Not explained further here; K4 reports it as its own column.
