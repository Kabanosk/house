# K2a: auto-compact threshold through `house adapt` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Contest sessions (interactive and `night` workers) compact automatically long before ~967K: `house adapt` writes `autoCompactWindow: 200000` and `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS=150000` into the contest's `.claude/settings.local.json`, and a value the user set there wins.

**Architecture:** `adapters.claude_settings()` gains two keys; `adapters.merge_settings()` learns "user value wins" for every key of ours other than `hooks` and `permissions` (scalars: set only when absent; `env`: dict merge, existing keys win). Nothing else changes: `night.worker_command` already passes `--settings <contest>/.claude/settings.local.json`.

**Tech Stack:** Python 3.11+ stdlib, `unittest`.

**Spec:** `docs/specs/2026-11-16-short-context-design.md` §8 (K2a); order: `docs/specs/2026-11-16-agno-adoptions-design.md` §5 step 1 (after the baseline night).

## Checked first (2026-11-17, Claude Code 2.1.326, by running it)

Probe: throwaway git repo in the scratchpad, `claude -p --model haiku --settings <file>`, `/context` and logging hooks.

| Question | Result |
|---|---|
| Is `autoCompactWindow` read from a `--settings` file? | Yes. `/context` shows `… / 200k` with 200000, `… / 120k` with 120000, `… / 1m` without it (Haiku 5.5 and Sonnet 5.5). |
| Where does auto-compact fire? | At **window − autocompact buffer**; the buffer is 33K in every run. Default: 1M − 33K ≈ **967K** (spec §4 confirmed). With 200000: ≈ **167K**, not 200K. With 100000 it fired at ≈67K. |
| Does auto-compact run our `PreCompact` instructions? | Yes: `PreCompact` with `trigger: "auto"`, also mid-turn (between tool calls); `PostCompact` with `trigger: "auto"` and `compact_summary`. |
| `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS` | Not triggerable cheaply (needs ~1 h idle). The binary has it: `Math.max(<min>, env.CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS ?? <default>)`, idle compaction only on 1-hour cache TTL, switch `"idleCompaction": false`. Name confirmed; the behaviour goes into the manual trial below. |

**Decision (Wojtek, 2026-11-17, Signal): A — keep 200000.** Background: with `autoCompactWindow: 200000` the safety net fires at ~167K, so the K2b reminder band "~120K–~200K" shrinks to ~120K–~167K. Options: **A)** keep 200000 as the spec says (net at ~167K; n* there ≈ 8 calls, still pays off; recommended — simplest, matches the spec text, K4 measures it); **B)** 233000 so it fires at ~200K. The plan uses the constant `AUTO_COMPACT_WINDOW`, so B is a one-line change.

## Global Constraints

- Python 3.11+, stdlib only; `python3 -m unittest discover -s tests` green before every commit.
- Docstrings and comments in English, in the style of `houselib`.
- Hooks and these keys live only in `.claude/settings.local.json` (excluded from git); nothing goes into a committed file.
- A value the user already has in `settings.local.json` wins (spec Agno §16, short-context §8). `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` is not used.
- No code lands before the baseline night has ended (Agno spec §5 step 0); the night runs on this checkout's code.

## Review Focus

- Existing `settings.local.json` with the user's own `autoCompactWindow` (e.g. 500000) → kept, not overwritten on re-adapt.
- Existing `env` with other user variables (e.g. `FOO=1`) → kept, ours added; user's own `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS` → kept.
- Existing `env` that is not a dict (hand-edited garbage, e.g. a list) → replaced by ours, no crash.
- House repo settings (`write_house_settings`, `house setup --signal-hook`) → do **not** get these keys (they would change every house session).
- Re-running `house adapt` twice → identical file (idempotent, no duplicates).

---

### Task 1: `claude_settings` and "user value wins" in `merge_settings`

**Files:**
- Modify: `houselib/adapters.py` (`claude_settings` ~283–304, `merge_settings` ~345–377; new module constants next to `ALLOW_COMMANDS`)
- Test: `tests/test_adapt.py` (class with `test_fresh_contest_gets_hooks_and_allowlist`, ~185)

**Interfaces:**
- Produces: `AUTO_COMPACT_WINDOW = 200000`, `IDLE_COMPACT_MIN_TOKENS = 150000` (ints, module level in `houselib/adapters.py`); `claude_settings(root)` returns additionally `"autoCompactWindow": AUTO_COMPACT_WINDOW` and `"env": {"CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS": str(IDLE_COMPACT_MIN_TOKENS)}`; `merge_settings(existing, ours)` keeps its signature. A2a (next plan) adds hooks to `claude_settings` and relies on `merge_settings` still replacing house hooks by command.

- [ ] **Step 1: Write the failing tests** (in the existing settings test class, using its fixture contest and `adapters.write_local_settings`):
  - `test_fresh_contest_gets_compact_settings`: after adapt, `s["autoCompactWindow"] == 200000` and `s["env"]["CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS"] == "150000"`.
  - `test_user_compact_values_win`: pre-write `{"autoCompactWindow": 500000, "env": {"FOO": "1", "CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS": "300000"}, "model": "opus"}`; after adapt: `500000`, `"300000"`, `FOO == "1"`, `model == "opus"`, house hooks present.
  - `test_env_not_a_dict_is_replaced`: pre-write `{"env": ["x"]}` → after adapt `env == {"CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS": "150000"}`.
  - `test_adapt_twice_is_identical`: two `write_local_settings` calls → byte-identical file.
  - `test_house_repo_settings_have_no_compact_keys`: `adapters.house_settings(root)` has neither `autoCompactWindow` nor `env`.

- [ ] **Step 2: Run to verify they fail**
  Run: `python3 -m unittest tests.test_adapt -v`
  Expected: the five new tests FAIL (KeyError / assertion), the rest pass.

- [ ] **Step 3: Implement** the constants, the two keys in `claude_settings`, and in `merge_settings` a loop over `ours` keys other than `hooks`/`permissions`: dict values merge as `{**ours[k], **existing[k]}` when `existing[k]` is a dict, else `ours[k]`; other values are set only when the key is absent from `existing`. Update both docstrings (what is written, who wins). `house_settings` stays as is (it builds its dict from hooks only) — the test pins that.

- [ ] **Step 4: Run the tests**
  Run: `python3 -m unittest discover -s tests`
  Expected: all green.

- [ ] **Step 5: Commit**
  ```bash
  git add houselib/adapters.py tests/test_adapt.py
  git commit -m "feat(adapt): auto-compact window 200K and idle compaction from 150K for contest sessions (K2a)"
  ```

### Task 2: README and the manual trial record

**Files:**
- Modify: `README.md` (section on `house adapt` / night: one paragraph on compaction thresholds and how to override them in `settings.local.json`)
- Modify: this plan (fill in "Manual trial" below)

- [ ] **Step 1:** README paragraph (Polish, like the rest of README): what `house adapt` sets, that auto-compact fires ~33K below the window (≈167K), idle compaction from 150K after ~1 h on a warm cache, user values in `settings.local.json` win, `"idleCompaction": false` turns idle compaction off.
- [ ] **Step 2: Manual trial** — done once together with A2a (one trial covers K2a and A2a acceptance; spec short-context §8 "Akceptacja K2a"), cheap model only: in the baseline contest copy, after `house adapt`, `claude -p --model haiku --settings .claude/settings.local.json` pushed past the threshold by ranged `Read`s of a generated file; record CLI version, the token count at which `PreCompact` fired (`trigger: auto`), the `compactions.jsonl` row and that `SessionStart` (`source: compact`) restored the human decisions. Idle compaction: record only that the setting reached the session (`/context` or `env`), not an idle trigger.
- [ ] **Step 3: Commit** `docs: K2a thresholds in README; manual trial result`.

## Manual trial

2026-11-17 12:23, Claude Code 2.1.326, Haiku 5.5, contest copy `grok-bot-baseline` after `house adapt` (joint K2a + A2a trial):
- `settings.local.json`: `autoCompactWindow` 200000, `env.CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS` "150000", hooks `PreCompact`/`PostCompact` present.
- Auto compaction fired after crossing ~167K: `compact_boundary` trigger `auto`, `pre_tokens` 170196, `post_tokens` 20189 (session cost 0.16 USD).
- `.house/tmp/compactions.jsonl` row: trigger `auto`, `chars` 2980, `summary_chars` 2441, all 8 sections present, none marked empty, no summary text.
- After compaction the model quoted a test human decision (D-901, `Kto: człowiek`) verbatim from the "house: state restored after compaction" block, not from the summary: the human decision is restored by code.
- Summary headings: the eight sections in order.
- Deviation: the summary quoted two partial lines of a file that is on disk (one inaccurately); the instruction does not forbid copying file contents strongly enough (open finding for the final review).
- Manual `/compact`: verified in the 2026-11-17 probe (`PreCompact` stdout used as the summary instructions, `trigger: manual`).
- Idle compaction: not triggered (needs ~1 h idle); only the setting was verified.
- The test decision was reverted after the trial.
- Auto compaction in print mode (2026-11-18, CLI 2.1.327, Haiku 5.5, synthetic files): `autoCompactWindow: 200000` in a `--settings` file fires in `claude -p` (three `compact_boundary` with `trigger: auto`, peak context ~160K, `PreCompact` ran each time), so night workers and evals get K2a. A value of 60000 in the same key was silently ignored (no compaction up to ~370K): small windows need `CLAUDE_CODE_AUTO_COMPACT_WINDOW` (env or the settings `env` block), which fired at both values.
