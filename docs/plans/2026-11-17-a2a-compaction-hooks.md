# A2a: compaction in contest sessions (PreCompact, SessionStart `compact`, PostCompact) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When Claude Code compacts a contest session, the summary follows our eight-section hand-off (a brief for further work, not a history), human decisions and answers come back verbatim from code right after compaction, and every compaction leaves a content-free row in `.house/tmp/compactions.jsonl`.

**Architecture:** `houselib/decisions.py` parses `DECISIONS.md` (only inside `## D-###` blocks, `Zastępuje:` honoured). `houselib/handoff.py` owns the hand-off template: the section list, the English compaction instructions, and section detection for the counter. `houselib/hook.py` gets `pre_compact`, `post_compact` and a `compact` branch of `session_start`; `bin/house-hook` routes the new modes; `adapters.claude_settings` registers `PreCompact` and `PostCompact`. `house_settings` (house repo) picks hooks by name, so it does not get them.

**Tech Stack:** Python 3.11+ stdlib, `unittest`, Claude Code hooks.

**Spec:** `docs/specs/2026-11-16-agno-adoptions-design.md` §8 (template, instruction rules, A2a); rationale in `docs/specs/2026-11-16-short-context-design.md` §6. Order: Agno §5 step 1.

## Checked first (2026-11-17, Claude Code 2.1.326, by running it)

Probe with Haiku in a scratch repo, hooks logging the event JSON (keys and short values only):

- `PreCompact` event: `session_id`, `transcript_path`, `cwd`, `prompt_id`, `trigger` (`manual` | `auto`), `custom_instructions` (null for plain `/compact`). Its **stdout became the summary instructions**: the model's summary began with the marker line we asked for.
- Order of events: `PreCompact` → `SessionStart` with `source: "compact"` (has `model`) → `PostCompact`.
- `SessionStart(compact)` stdout **reaches the model** after compaction (it quoted `SessionStart:compact hook success: …`).
- `PostCompact` event: `trigger`, `compact_summary` (str; 3–7K characters in the probe) which starts with an `<analysis>…` block before the summary proper. Section detection must look at the part after `<analysis>` (after `</analysis>` or inside `<summary>…</summary>` when present).
- Auto-compact fires mid-turn too and runs the same three hooks (`trigger: auto`).
- Bonus for K2b (not used here): `SessionStart` with `source: resume` carries `context_tokens`, `seconds_since_last_response`, `prompt_cache_likely_expired`, `estimated_cache_write_usd`.
- "`# Compact instructions` in CLAUDE.md": not used (spec: stay with the hook).

## Global Constraints

- Python 3.11+, stdlib only; `python3 -m unittest discover -s tests` green before every commit. Docstrings/comments in English like `houselib`. Tests on synthetic files only.
- Hooks never break a session: internal error → exit 0 and one `house-hook:` line on stderr (as today).
- `PreCompact` stdout ≤ **10 000 characters** (Claude Code cuts longer). `SessionStart` in `compact` mode ≤ **10 000 characters**, no Signal receive, no briefing line. Any other `source`: output exactly as today (≤ 25 lines).
- `.house/tmp/compactions.jsonl` is git-ignored (`.house/tmp/`); its rows contain **no summary text**: time, session id, trigger, length, sections present, sections marked empty.
- Template sections, in this order, English headers (spec §8): `Goal`; `Next` (ends with two required lines: next command or edit; done-when condition); `Critical context` with `Observed` (each entry with its source: command or path:line) and `Unverified`; `Constraints and human decisions`; `Dead ends` (reason, number of tries, "do not repeat"); `Decisions and facts` (D-### refs); `Errors and fixes`; `Done` (each changed file, verified or not). Each section filled or explicitly `none`. Ends with a pointer to the full record (session id, `transcript_path`).
- Instruction rules (spec §8): "Stop working. Write only the hand-off; do not run commands or tools."; the reader gets only instructions, task and this hand-off; what survives the reset (repo, `.house/`, task file, reports, `house out` logs) is pointed to by path, not copied; truncated output → a narrow command to recover it; "Do not invent observations. Do not restate the task."; as short as possible, aim ~2–3K tokens, hard hint ≤ ~8K tokens.
- A2a does not touch `night` (A2b) and does not reference `house evidence` (A1 adds it).

## Review Focus

- `DECISIONS.md` straight from the template: the header line `- **Kto:** człowiek | judge | coordinator` sits outside any `## D-###` block → not an entry, no human decision printed.
- Human decision later superseded (`- **Zastępuje:** D-003` in a later entry, also `D-3`/lower-case/trailing text) → the old one is not printed; the new one is.
- `Kto` written by `questions.answer` as `człowiek (+48…, Signal)` or `człowiek (Wojtek)` → counts as human (prefix match, case-insensitive), not only the bare word.
- Many/long human decisions exceeding 10 000 characters → whole entries kept in order until the budget, then one line pointing to `.house/DECISIONS.md`; one single entry longer than the budget is cut with `…`, never dropped silently.
- `PostCompact` with an empty or missing `compact_summary`, or a summary with no `<analysis>` block, or Polish headers (`Cel`, `Ślepe uliczki`) → a row is still written; sections counted with English and Polish names.

---

### Task 1: `houselib/decisions.py` — parse `DECISIONS.md`

**Files:**
- Create: `houselib/decisions.py`
- Test: `tests/test_decisions.py`

**Interfaces:**
- Produces:
  - `@dataclass Decision: id: str` ("D-007"), `title: str`, `fields: dict[str, str]` (field name as written, e.g. `"Kto"` → value), `by: str` (`"human"` | `"agent"` | `"unknown"`; `Kto` starting with `człowiek` → human, `judge`/`coordinator` → agent, missing → unknown), `supersedes: list[str]` (normalised `D-###` ids from `Zastępuje`), `text: str` (the whole block, header included, trailing blank lines stripped), `line: int` (1-based line of the header).
  - `parse(text: str) -> list[Decision]` — entries only from `^## D-(\d+)\b` headers; fields only from `- **Name:** value` lines inside a block; id normalised to three digits (`D-7` → `D-007`).
  - `active_human(decisions: list[Decision]) -> list[Decision]` — `by == "human"` and not superseded by any entry in the list, in file order.
  - Reused later by A5 (`house decisions`).

- [ ] **Step 1: Write the failing tests** `tests/test_decisions.py`:
  - `test_template_header_is_not_an_entry`: text of `core/templates/house/DECISIONS.md` → `parse(...) == []`.
  - `test_fields_and_by`: three entries with `Kto: człowiek`, `Kto: człowiek (Wojtek, Signal)`, `Kto: judge`, one without `Kto` → `by == ["human", "human", "agent", "unknown"]`; `fields["Decyzja"]` read; `line` correct.
  - `test_supersedes_hides_old_human_entry`: D-001 human, D-004 human with `- **Zastępuje:** D-1 (zmiana planu)` → `active_human` ids `["D-004"]`; `supersedes == ["D-001"]`.
  - `test_text_is_whole_block`: `text` starts with `## D-002` and contains every field line of that entry and nothing of the next.
  - `test_merge_gate_entry_is_agent`: entry in the exact format `merge._review_gate` writes (`Kto: coordinator`) → `by == "agent"`.
- [ ] **Step 2:** `python3 -m unittest tests.test_decisions -v` → FAIL (module missing).
- [ ] **Step 3:** Implement with `re` over lines; module docstring states the rules an agent should know (format, `Zastępuje`, who counts as human) like other `houselib` modules.
- [ ] **Step 4:** `python3 -m unittest discover -s tests` → green.
- [ ] **Step 5:** `git add houselib/decisions.py tests/test_decisions.py && git commit -m "feat(decisions): parse DECISIONS.md entries, human and superseded (A2a)"`

### Task 2: `houselib/handoff.py` — template, compaction instructions, section detection

**Files:**
- Create: `houselib/handoff.py`
- Test: `tests/test_handoff.py`

**Interfaces:**
- Produces:
  - `SECTIONS: list[tuple[str, tuple[str, ...]]]` — `(english header, accepted aliases)` in template order: `("Goal", ("Cel",))`, `("Next", ("W toku", "Następne kroki"))`, `("Critical context", ("Krytyczny kontekst",))`, `("Constraints and human decisions", ("Ograniczenia",))`, `("Dead ends", ("Ślepe uliczki",))`, `("Decisions and facts", ("Decyzje",))`, `("Errors and fixes", ("Błędy",))`, `("Done", ("Zrobione",))`. A2b reuses it for the report template and the brief.
  - `compact_instructions(session_id: str, transcript_path: str) -> str` — the full English instruction text (exact wording is the implementer's within the rules in Global Constraints), ≤ 10 000 chars, naming all eight headers in order with `Observed`/`Unverified` under `Critical context` and the two required closing lines of `Next` (`Next action:` and `Done when:`), and ending with the pointer block (`Full record: session <id>, transcript <path>`).
  - `summary_body(text: str) -> str` — the part after `</analysis>` (or the content of `<summary>…</summary>` when present), else the whole text.
  - `section_status(text: str) -> tuple[list[str], list[str]]` — `(present, marked_empty)` as English header names, on `summary_body(text)`; a section is present when a Markdown heading or bold line (`#… Goal`, `**Goal**`, `1. Goal`) matches the header or an alias, case-insensitive; marked empty when its body (until the next section) is only `none`/`brak`/`-`/`n/a`.
- [ ] **Step 1: Failing tests** `tests/test_handoff.py`:
  - `test_instructions_have_sections_in_order`: indexes of the eight headers in `compact_instructions("s1", "/tmp/t.jsonl")` strictly increase; `Observed` and `Unverified` appear after `Critical context` and before `Constraints and human decisions`; contains `Next action:`, `Done when:`, `s1`, `/tmp/t.jsonl`, `Stop working`, `Do not invent observations`.
  - `test_instructions_fit_budget`: `len(...) <= 10_000` with a 300-char transcript path.
  - `test_section_status_english_and_polish`: synthetic summary `<analysis>Goal: ignore me</analysis>` + `## Goal\nShip X\n## Dead ends\nnone\n## Zrobione\n- a.py (verified)` → present `["Goal", "Dead ends", "Done"]`, marked empty `["Dead ends"]` (the `Goal` inside `<analysis>` does not count twice nor alone).
  - `test_section_status_empty`: `section_status("") == ([], [])`.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** full suite green.
- [ ] **Step 5:** `git commit -m "feat(handoff): eight-section hand-off template and compaction instructions (A2a)"`

### Task 3: hooks — `pre-compact`, `post-compact`, `session-start` in `compact` mode

**Files:**
- Modify: `houselib/hook.py` (new `pre_compact`, `post_compact`, `compact_state`; `session_start` branches on `event.get("source") == "compact"`; module docstring: new modes and the 10 000-character contract)
- Modify: `bin/house-hook` (modes `pre-compact` → stdout, `post-compact` → no output; docstring)
- Test: `tests/test_hook.py`

**Interfaces:**
- Consumes: `decisions.parse`, `decisions.active_human` (Task 1); `handoff.compact_instructions`, `handoff.section_status`, `handoff.summary_body` (Task 2); existing `questions.parse` (fields `id`, `status`, `title`, `answer`, `blocks`), `hook._tasks`, `exp.summary_line`, `.house/checks/*.json` results as `session_start` reads them today.
- Produces:
  - `pre_compact(event: dict) -> str` — in a contest: `handoff.compact_instructions(event["session_id"], event["transcript_path"])` (missing keys → `"?"`); outside a contest: `""`.
  - `compact_state(contest: Path, budget: int = 10_000) -> str` — items in this order, each whole or not at all: (1) `## Human decisions (verbatim)` with `d.text` of every `active_human` decision, then every answered Q-### as `Q-### <title> — answer: <answer>`; (2) open Q-### (`id`, title, `Blokuje`); (3) tasks with status `doing`/`review`/`blocked` with their last check result (GREEN/RED + failing ids, from `.house/checks/T###.json`); (4) red checks and `exp.summary_line`; (5) when anything was left out: `More: .house/DECISIONS.md, .house/QUESTIONS.md, .house/tasks/`. First line: `house: state restored after compaction (contest <name>)`. A single item longer than the remaining budget is cut with `…` only if it is the first of group (1); otherwise dropped and counted in (5).
  - `session_start(event)` — `source == "compact"` → `compact_state(contest)` (no `_signal_lines`, no briefing line); outside a contest with `compact` → `""`; every other source unchanged.
  - `post_compact(event: dict) -> None` — in a contest: append to `<contest>/.house/tmp/compactions.jsonl` one JSON line `{"time": ISO local, "session_id", "trigger", "chars": len(compact_summary), "summary_chars": len(summary_body), "present": [...], "empty": [...]}`; creates `.house/tmp/`; outside a contest: nothing.
- [ ] **Step 1: Failing tests** in `tests/test_hook.py` (fixture contest from `tests/helpers.py` as the existing hook tests use):
  - `test_pre_compact_prints_instructions`: `pre_compact({"cwd": contest, "session_id": "abc", "transcript_path": "/x/abc.jsonl", "trigger": "auto"})` ≤ 10 000 chars, contains `Dead ends`, `abc`, `/x/abc.jsonl`; outside a contest → `""`.
  - `test_compact_mode_restores_human_decisions_first`: DECISIONS.md = template header + D-001 human (superseded by D-003), D-002 judge, D-003 human with `Zastępuje: D-001`; QUESTIONS.md with Q-001 answered and Q-002 open → output starts with the restored-state line, then D-003's full text; D-001 absent; D-002 absent; the template's `Kto: człowiek | judge | coordinator` line absent; Q-001 answer present; Q-002 present after it; `len <= 10_000`.
  - `test_compact_mode_budget_keeps_order`: 40 human decisions of ~400 chars each → output ≤ 10 000, ends with the `More:` line, decisions kept are the first ones in file order.
  - `test_compact_mode_never_calls_signal`: patch `hook._signal_lines` to raise → `session_start({"cwd": contest, "source": "compact"})` works and the mock was not called; with `source: "startup"` output equals today's (`session_start` without `source`) and stays ≤ 25 lines.
  - `test_post_compact_row_without_content`: summary with a canary string `CANARY-7Q` in its `## Goal` body → one row in `.house/tmp/compactions.jsonl` with `trigger`, `chars`, `present` contains `"Goal"`, and the file does **not** contain `CANARY-7Q`; missing `compact_summary` → row with `chars == 0`.
  - `test_house_hook_routes_new_modes`: run `bin/house-hook pre-compact` and `post-compact` as subprocesses with JSON on stdin (as existing entry-point tests do) → exit 0; `pre-compact` stdout non-empty in a contest; garbage stdin → exit 0 with a `house-hook:` stderr line.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** full suite green.
- [ ] **Step 5:** `git commit -m "feat(hook): PreCompact instructions, compact-mode SessionStart, PostCompact counter (A2a)"`

### Task 4: register the hooks in contests

**Files:**
- Modify: `houselib/adapters.py` (`claude_settings`: add `"PreCompact": [{"hooks": [{"type": "command", "command": f"{hook} pre-compact", "timeout": 30}]}]` and `"PostCompact": [{"hooks": [{"type": "command", "command": f"{hook} post-compact", "timeout": 30}]}]`; docstring)
- Test: `tests/test_adapt.py`

**Interfaces:**
- Consumes: `merge_settings` (unchanged hook replacement by `house-hook` in the command; K2a's key handling).
- [ ] **Step 1: Failing tests:** `test_contest_gets_compaction_hooks` (both events present, command ends with `pre-compact`/`post-compact`); `test_house_repo_has_no_compaction_hooks` (`house_settings(root)["hooks"]` has only `PreToolUse` and `SessionStart`); existing `test_user_settings_survive_without_duplicates` extended: re-adapt keeps one `PreCompact` entry.
- [ ] **Step 2–4:** FAIL → implement → full suite green.
- [ ] **Step 5:** `git commit -m "feat(adapt): register PreCompact and PostCompact hooks in contests (A2a)"`

### Task 5: manual trial (with K2a) and docs

**Files:**
- Modify: `README.md` (one short paragraph: what happens at compaction in a contest session, where the counter is)
- Modify: `docs/plans/2026-11-17-k2a-autocompact-window.md` and this plan ("Manual trial" sections)

- [ ] **Step 1:** README paragraph (Polish).
- [ ] **Step 2: Manual trial**, Haiku only, in the baseline contest copy after `house adapt` (spec Agno A2a acceptance + short-context §8): (a) `/compact` → `PreCompact` fired, summary has the sections and no file contents that are on disk; (b) auto-compact past the K2a threshold → `trigger: auto`; (c) `SessionStart(compact)` printed the human decisions (add one human D-### in the copy for the trial; revert after); (d) a row in `.house/tmp/compactions.jsonl`. Idle compaction: not triggered (needs ~1 h); note the known v2.1.105–2.1.114 bug is not our version. Record versions and numbers here, no session content.
- [ ] **Step 3:** `git commit -m "docs: compaction in contest sessions; A2a/K2a manual trial"`

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

Re-trial after the final-review fix wave (2026-11-17 12:37, Claude Code 2.1.326, Haiku 5.5, manual `/compact`):
- Goal kept the request and its source ("the human who asked in this chat") and the done-when condition survived.
- The file on disk was pointed to by path, Read offsets and `sed -n '189p' <path>`; no file line was copied (only the 5-character
  row label of the result).
- `compactions.jsonl` row: trigger `manual`, `chars` 3264, all 8 sections present, `Dead ends` marked empty.
