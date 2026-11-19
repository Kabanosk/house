# K5: does compaction with our brief, and reading by range, save tokens without losing quality — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure, at production settings, whether K2a (auto compaction at `autoCompactWindow: 200000`) with our
compaction instructions (K1/A2a) and K3 (long inputs in `.house/sources/`, read by range) lower the weighted cost of a
long session without lowering its quality, against a control without them; decide from the numbers what stays.

**Architecture:** `house eval` (A3) gets what an A/B needs: per-run variants (settings patch, env, text edits of the
fixture contest), repeats, follow-up user turns (stream-json input, as a human driving the coordinator), hidden tests
run in a second hardened container after the agent exits (host never executes agent code), a per-predicate score, and
cost metrics per run from the run log (K0 code). Two long cases built from deterministic synthetic fixtures. Runs on
Sonnet, three repeats per variant; a report in `docs/research/k5/`.

**Tech Stack:** Python 3.11+ stdlib, `unittest`, Docker (the existing `house-agent` image), Claude Code 2.1.327.

**Spec:** `docs/specs/2026-11-16-short-context-design.md` (§2 anti-goals, §8 K2, §9 K3, §10 K4: "Jeśli kompakcja
podnosi koszt na scalone zadanie, K2a jest cofane"); `docs/specs/2026-11-16-agno-adoptions-design.md` (A3).

**Accepted direction:** Wojtek, 2026-11-18 (session): "jak na produkcji" — production thresholds, no scaled-down
windows; Sonnet; "mamy dzisiaj duży limit na sonnecie więc rób".

## Checked first (2026-11-18, CLI 2.1.327, Haiku 5.5, synthetic files in the scratchpad)

| Question | Result |
|---|---|
| Does auto compaction fire in `claude -p` with `autoCompactWindow: 200000` in a `--settings` file? | Yes: three `compact_boundary` (`trigger: auto`), peak context ~160K; `PreCompact` ran every time. |
| Does a small `autoCompactWindow` (60000) work? | No, silently ignored (no compaction up to ~370K). `CLAUDE_CODE_AUTO_COMPACT_WINDOW` (env or settings `env`) works. Not needed here: we test 200000. |
| Do several user turns work in print mode? | Yes: `--input-format stream-json` with four user messages, each answered in order in one session. |
| Do hooks run in the eval container? | The container mounts `<tmp>/house` read-only and the contest settings point at its `bin/house`; confirmed by the smoke run (Task 6, step 1). |
| Does Sonnet 5.5 without the setting have a ~1M window (control arm without early compaction)? | Yes (`/context` shows `/ 1m`, K2a plan). |

## Global Constraints

- Production settings only: the treatment arm uses exactly what `house adapt` writes (`autoCompactWindow: 200000`,
  `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS=150000`, our `PreCompact`/`PostCompact`/`SessionStart` hooks, agent profiles
  with the "Long inputs" block). No scaled-down thresholds.
- Model: Sonnet (`sonnet`) for every run. Three repeats per (case, variant). Weighted cost = K0 weights (input 1,
  cache_read 0.1, cache_write_5m 1.25, cache_write_1h 2, output 5), computed by `houselib/cost.py` code, not a copy.
- The host never executes code the agent wrote (A3 rule): hidden tests run in a second container with the same
  hardening, `--network none`, the contest mounted read-only, hidden tests mounted read-only from outside the
  contest, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`; the grade keeps only numbers (exit, passed, failed).
- Fixtures are generated (seeded `random`), nothing large is committed; no real session or contest content.
- A variant that does not apply (a settings key path or an edit pattern that matches nothing) is `infra_error`, never
  a silent no-op.
- Decision rule, fixed before the runs (Task 6): see "Decision rule" below.

## Review Focus

- A variant leaks into the next run (settings patched in the shared fixture instead of the run's own temp root).
- The control arm compacts anyway (e.g. idle compaction or a default window under 1M) → the report must show
  compactions per run, and a control run with a compaction is flagged, not averaged silently.
- A hidden test that the agent can read or overwrite (mounted inside the contest) → mount outside, read-only.
- Follow-up turns sent before the previous turn finished (stream-json input is a queue; the CLI answers in order —
  pinned by a test with a fake `claude` that records turn boundaries).
- Cost of a run with sub-agents: the stream log carries sub-agent usage (K0 W2.3); the metric must include it.

## Decision rule (pre-registered)

Per case, mean over three repeats; quality = mean per-predicate score (0–1); cost = mean weighted cost.
- **K2a + our instructions stay** if quality(B) ≥ quality(A) − 0.05 and cost(B) < cost(A). If quality(B) is lower by
  more than 0.05 → K2a threshold goes up or is reverted (spec §2 anti-goal), decided with Wojtek.
- **Our compaction instructions (K1/A2a hooks) stay** if quality(B) ≥ quality(C); if C ≥ B on quality and cost, the
  hooks are simplified (default compaction is enough).
- **K3 stays** if quality(sources) ≥ quality(pasted) − 0.05 and cost(sources) < cost(pasted).
- A difference smaller than the spread between repeats of the same variant is reported as "no difference".

---

### Task 1: `house eval` variants, repeats and per-run cost metrics

**Files:** `houselib/evals/cases.py` (optional `variants`), `houselib/evals/runner.py` (apply a variant to the run's
temp root before the container starts), `houselib/evals/results.py` (row fields), `houselib/cli.py` (`eval run
--variant NAME`, `--repeat N`), `evals/cases/README.md`, tests in `tests/test_eval_variants.py`.

**Interfaces:**
- Case field `variants`: `{name: {"settings": {...}, "env": {...}, "edit": [{"path", "pattern", "repl"}]}}`.
  `settings` is a deep merge into the contest's `.claude/settings.local.json` where JSON `null` deletes a key; `env`
  is merged into the settings `env` block; `edit` is `re.subn` on a file relative to the contest (`count` ≥ 1 or
  `infra_error`). Names `[a-z0-9-]{1,32}`; `default` is reserved for "no variant".
- `apply_variant(root: TempRoot, variant: Variant) -> None` in `runner.py`.
- Results row gains `variant` (string, `default` when none), `metrics`: `{"weighted": int, "peak": int, "calls":
  int, "compactions": int, "output": int}` from the run's stream log through the K0 parser; `case_hash` unchanged.
- `--repeat N` runs the case N times sequentially, each in a fresh temp root.

- [ ] Tests: settings deep merge with `null` delete; env merge; edit that matches nothing → `infra_error`; two runs
  of different variants do not see each other's patch; `--repeat 2` writes two rows; metrics from a synthetic stream
  log with one `compact_boundary` and one sub-agent call (`compactions == 1`, sub-agent usage counted).
- [ ] Commit `feat(eval): variants, repeats and per-run cost metrics (K5)`.

### Task 2: follow-up turns, hidden tests and a per-predicate score

**Files:** `houselib/evals/cases.py` (`followups`, predicate `hidden_tests`), `houselib/evals/runner.py`
(stream-json input; the second container), `houselib/evals/grade.py` (score), `evals/cases/README.md`, tests in
`tests/test_eval_followups.py`.

**Interfaces:**
- Case field `followups`: list of strings. The runner passes `--input-format stream-json` and writes the prompt and
  each follow-up as `{"type":"user","message":{"role":"user","content":...}}` lines to the CLI's stdin; turn
  counting (`max_turns`) stays per run.
- Predicate `{"type": "hidden_tests", "dir": "<fixture-relative dir>", "command": [...], "min_passed": int}`: the
  fixture writes the dir under `<tmp>/hidden/` (outside the contest). After the agent container exits, the runner
  starts a second container (same hardening as `container_argv`, `--network none`, no token, contest `:ro`, hidden
  dir `:ro`), runs the command, and records `{"exit", "passed", "failed"}` (counts parsed from pytest/unittest
  summary lines). Pass = exit 0 and `passed >= min_passed`.
- `Grade.score`: passed predicates / all predicates (outcome predicates and `forbid`/`expect` entries each count 1),
  stored in the results row next to the verdict.

- [ ] Tests: a fake `claude` records the stdin lines (prompt then follow-ups, in order); hidden-tests argv contains
  `--network none`, `:ro` for both mounts and no `CLAUDE_CODE_OAUTH_TOKEN`; counts parsed from both summary formats;
  score 3/4 for one failed predicate.
- [ ] Commit `feat(eval): follow-up turns, hidden tests in a second container, per-predicate score (K5)`.

### Task 3: `house eval compare`

**Files:** `houselib/evals/results.py`, `houselib/cli.py`, `docs/modules/eval.md` (Polish, a short paragraph),
tests.

**Interfaces:** `house eval compare <case> [--since DATE]`: one row per variant: `n`, pass count, mean and min–max
score, mean weighted cost (and min–max), median peak, compactions per run, flags (`control compacted`). Numbers only.

- [ ] Tests: aggregation over synthetic rows; the flag when a run of a variant named `control*` has compactions > 0.
- [ ] Commit `feat(eval): compare variants of a case (K5)`.

### Task 4: case `long-chain` (compaction in a long coordinator-like session)

**Files:** `houselib/evals/fixtures.py` (`chain_big`), `evals/cases/long-chain.json`, tests that build the fixture
and check its invariants (sizes, planted facts, hidden tests failing on the untouched fixture).

**Fixture `chain_big`** (seed fixed): a Python package `ledger/` (~24 modules, 300–600 lines each, realistic names
and docstrings), `data/` with two large logs (~3 000 lines each), visible tests for existing behaviour, and hidden
tests under `<tmp>/hidden/chain/` (one file per sub-task). Planted:
- **Human decisions** (follow-up 1, as a human would type them): "D-007: amounts are integers in grosze, never
  floats" and "D-008: do not change any signature in `ledger/api_v1.py`". Sub-task 5 naturally invites a float and a
  signature change.
- **Dead end:** sub-task 2 names an optional speed-up through the module `fastcsv`, which is not installed and cannot
  be (no network); the working path is stdlib `csv`. Sub-task 6 mentions speed again.
- **Early facts** the final answer must repeat: the root cause found in sub-task 1 (a fixed string in a log line) and
  the config value found in sub-task 3.

**Case:** coordinator-like main session (`skill`-less agent case with `agent: coder`, `model: sonnet`), prompt =
sub-task 1, `followups` = the decisions turn, then sub-tasks 2–6 and the final question. `max_turns` 200,
`timeout_seconds` 5400. Size target: without compaction the session peaks at 250K–350K (checked in the smoke run).
Outcome: `hidden_tests` per sub-task, `file_unchanged` `ledger/api_v1.py`, a hidden test for integer amounts,
`final_answer` for each early fact; `forbid` `fastcsv` in Bash with `from_turn` = the sub-task 6 turn and `min: 1` (a repeated dead end; ruling C10).
**Variants:** `control` (`settings: {"autoCompactWindow": null}`, `env: {"CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS":
null}`), `house` (no patch: production), `default-compact` (production window, `settings.hooks.PreCompact`,
`PostCompact` and the `SessionStart` compact entry removed).

- [ ] Tests: fixture invariants; the case loads; every variant applies to a built fixture.
- [ ] Commit `feat(eval): long-chain case for compaction (K5)`.

### Task 5: case `long-input` (reading by range)

**Files:** `houselib/evals/fixtures.py` (`spec_huge`), `evals/cases/long-input.json`, tests.

**Fixture `spec_huge`:** a synthetic contest rulebook plus API reference, ~500 000 characters, sections with
realistic headings; ten facts scattered across it, including an erratum near the end that changes a rule stated
early (the classic trap) and two near-duplicate values in different sections. Task: implement `validate.py` to the
document (hidden tests) and answer ten questions in the final message, one line each (`final_answer` per fact).
**Variants:** `pasted` (the document is in the prompt, no `.house/sources/`; edit removes the "Long inputs" block from
`.claude/agents/coder.md`), `sources` (production: the document in `.house/sources/rules.md`, the prompt says where).

- [ ] Tests: fixture invariants (size, facts present once, erratum after the original rule); both variants apply.
- [ ] Commit `feat(eval): long-input case for reading by range (K5)`.

### Task 6: runs and report

- [ ] Step 1 (smoke): one run per variant of both cases; check graders (hidden tests fail on an untouched fixture,
  pass on a reference solution), hooks firing in the container (`PreCompact` row in `.house/tmp/compactions.jsonl`
  for `house`), the control arm without compactions, and the size target of `long-chain`. Fix the cases if needed
  (case hash changes; smoke rows are not part of the result).
- [ ] Step 2: three repeats per variant (`house eval run --case long-chain --variant X --repeat 3`, same for
  `long-input`), sequentially.
- [ ] Step 3: `docs/research/k5/2026-11-18-compaction.md` (Polish): table from `house eval compare`, per-predicate
  breakdown (which facts/decisions were lost per variant), cost and peak, the decision rule applied, decisions.
  Numbers only; no session content.
- [ ] Commit `docs(k5): compaction and reading-by-range experiment`.

## Estimated cost (Sonnet, weighted)

`long-chain`: control ~2M per run, `house` and `default-compact` ~1.2M → ×3 ≈ 13M. `long-input`: `pasted` ~1M,
`sources` ~0.4M → ×3 ≈ 4M. Smoke ≈ 6M. Total ≈ 20–25M weighted (today's A/B night: 1.1M for both arms).

## Out of scope (later, K5b)

The retry brief (A1/A2) against `--resume` with the full history: needs two-session cases in the runner. Today's A/B
night already covers the conflict retry.
