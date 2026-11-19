# A0: session record reader (`houselib/transcript.py`) and stream logs for `night` / `codex-task` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One tolerant, streaming reader turns a Claude stream log, a `codex exec --json` log or an interactive Claude Code session (with sub-agents) into one tool-independent list of events, deduplicated and with a single privacy view for everything that leaves the process; `night` workers and `bin/codex-task` write such stream logs.

**Architecture:** `houselib/transcript.py` holds the event model, three source parsers (`claude_stream`, `codex_stream`, `claude_session`), global dedup (`load`) and the privacy view (`view`). `guard.redact` masks secrets in text. `night.worker_command` adds `--session-id <uuid> --output-format stream-json --verbose` and the worker log becomes `….jsonl`; `bin/codex-task` gets `--json-log FILE`. Nothing reads `~/.claude/projects` for sessions `house` started itself.

**Tech Stack:** Python 3.11+ stdlib (`json`, `dataclasses`, `uuid`, `pathlib`), `unittest`.

**Accepted:** Wojtek, 2026-11-17 (Signal).

**Spec:** `docs/specs/2026-11-16-agno-adoptions-design.md` §6 (A0) and §3 (git policy); order §5 step 2 ("narrow" = §6 as revised: three sources, no search layer, privacy at write — review §5).

## Checked first (2026-11-17, Claude Code 2.1.326, Codex 0.166.0, by running it on Haiku)

**Claude `claude -p --session-id U --output-format stream-json --verbose`** (one Bash call, one Agent sub-agent, one auto-compaction):
- `assistant` events carry `message.usage` **per model call**, with `cache_creation.ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens`. One call is split into several `assistant` events (one per content block) with the same `message.id` and identical `usage`; `stop_reason` is `null` in all of them. The request id key is `request_id` (session JSONL: `requestId`).
- Sub-agent events are **in the stream**: `parent_tool_use_id` (the parent's Agent `tool_use` id), `agent_id`, `subagent_type`.
- `result` lines can repeat (`result_index` 0, 1 when a background agent finishes); `modelUsage` (per model, 5m/1h split, `costUSD`) and `total_cost_usd` are **cumulative** → take the last.
- Σ deduplicated `usage` (by `message.id`) = `result.modelUsage` exactly (cache read 111 991 = 111 991) in a run without compaction.
- Compaction: `system`/`compact_boundary` with `compact_metadata` **snake_case** (`trigger`, `pre_tokens`, `post_tokens`, `duration_ms`); the summarising call has **no** `assistant` event (only in `result`). Session JSONL uses `compactMetadata.preTokens/postTokens`.
- Content-bearing `system` subtypes to skip: `hook_response` (hook stdout), `task_started` (`prompt`), `task_notification` (`summary`); ignore silently: `init`, `status`, `thinking_tokens`, `hook_started`, `task_progress`, `task_updated`, `background_tasks_changed`; type `rate_limit_event`.
- **Decision (spec §6 "Najpierw sprawdź" pt. 3):** the stream is the **only** source for sessions without a human; no fallback to `~/.claude/projects/*/<uuid>.jsonl`.

**Codex `codex exec --json`**: needs stdin closed (else it waits "Reading additional input from stdin"). `thread.started.thread_id`; `item.started`/`item.completed` with `item.type` `command_execution` (`command`, `aggregated_output`, `exit_code` null while running then int, `status` `completed`|`failed`) and `agent_message`; `turn.completed.usage` **per turn**: `input_tokens` (includes cache), `cached_input_tokens`, `cache_write_input_tokens`, `output_tokens`, `reasoning_output_tokens`. Per-turn sums are enough for v1 (spec).

**Interactive session JSONL** (`~/.claude/projects/<encoded cwd>/<session>.jsonl`; encoding = every character outside `[A-Za-z0-9]` → `-`, checked on the probe dir): `assistant` records with `message.usage`, `requestId`, `isSidechain`; `system`/`compact_boundary` with `compactMetadata`; `cost-state` with `modelUsage` per model and `totalCostUSD` (probe: cost-state cache read 527 460 vs Σ usage 378 110 → side calls/compaction outside the record, as the spec expects).

## Global Constraints

- Python 3.11+, stdlib only; `python3 -m unittest discover -s tests` green before every commit; docstrings/comments in English like `houselib`; tests on **synthetic** records only (no real sessions in the repo).
- Reading is always streaming, line by line. Unknown record: counted and skipped; broken line: skipped; never raise on input data. Format recognised by record shape; CLI version kept only as information.
- Skipped by default (content): `api-request-blob`, `file-history-snapshot`, `file-history-delta`, `<session>/tool-results/`; known metadata records (`ai-title`, `mode`, `bridge-session`, …) skipped silently; `<synthetic>` model dropped.
- Everything leaving the process (stdout of `house` commands, files in `.house/`, `evals/`) goes through `transcript.view`. Whitelist per tool (spec §6): `Bash` → `command` cut to 300 chars and `redact`ed; `Edit`/`Write`/`MultiEdit`/`NotebookEdit`/`Read` → `file_path`; `Grep`/`Glob` → `pattern`, `path`; `Agent`/`Task` → `subagent_type`, `description`; `Skill` → skill name; any other tool → name only; tool result → `is_error`, size (error tail: A1).
- Logs live in `.house/tmp/night/<date>/` (git-ignored). Worker stdin stays `/dev/null`.
- Codex tokens normalised: `input = input_tokens − cached_input_tokens − cache_write_input_tokens`, `cache_read = cached_input_tokens`, `cache_write = cache_write_input_tokens`, `output = output_tokens`.

## Review Focus

- A stream log cut mid-line by `night` killing a worker at timeout (last line truncated JSON) → earlier events kept, no exception.
- Worker log with non-JSON lines mixed in (stderr merged into stdout: warnings, a Python traceback from `codex-task`) → counted as `broken`, events still parsed.
- A Claude session copied into a fork or resumed (`--resume`) so the same `message.id`+request id appears in two session files → counted once.
- A sub-agent JSONL whose `.meta.json` is missing or malformed → events kept with `agent_id` from the file name and empty `tool_use_id`.
- A Bash command containing a token in an env assignment (`XAI_API_KEY=xai-…`) or a `curl -H 'Authorization: Bearer sk-…'` → `[REDACTED]` in every output; a canary in a `Write` content, an Agent prompt or a tool result never appears in any `view`.

---

### Task 1: `guard.redact`

**Files:** Modify `houselib/guard.py` (next to `has_secret`-style helpers ~337); Test `tests/test_guard.py`

**Interfaces:** Produces `redact(text: str) -> str` — every match of any `export.SECRET_RES` pattern replaced by `[REDACTED]`; text without secrets returned unchanged.

- [ ] **Step 1:** tests `test_redact_masks_each_secret_kind` (one sample per `SECRET_RES` pattern embedded in a sentence → pattern gone, `[REDACTED]` present, surrounding words kept), `test_redact_keeps_plain_text` (`"pytest tests/x.py -k y"` unchanged), `test_redact_two_secrets_in_one_command`.
- [ ] **Step 2:** `python3 -m unittest tests.test_guard -v` → FAIL. **Step 3:** implement. **Step 4:** full suite green.
- [ ] **Step 5:** `git commit -m "feat(guard): redact secrets in text"`

### Task 2: event model and the Claude stream parser

**Files:** Create `houselib/transcript.py`; Create `tests/test_transcript.py` (with small builders that write synthetic JSONL lines into a temp dir)

**Interfaces:**
- Produces:
  - `@dataclass Event: kind: str` (`tool_call` | `tool_result` | `message` | `usage` | `compaction` | `session_total`), `ts: str | None`, `session_id: str`, `source: str` (`stream` | `jsonl` | `codex`), `model: str | None = None`, `agent_id: str = ""`, `parent_tool_use_id: str = ""`, `sidechain: bool = False`, `cwd: str | None = None`, `git_branch: str | None = None`, `cli_version: str | None = None`, `data: dict` (kind-specific, below), `raw: dict | None` (in memory only; never serialised).
  - `data` per kind: `tool_call` {`id`, `name`, `input`}; `tool_result` {`id`, `is_error`: True/False/None, `size`: int}; `message` {`role`, `length`}; `usage` {`key`: (message id, request id), `input`, `cache_read`, `cache_write_5m`, `cache_write_1h`, `output`, `complete`: bool}; `compaction` {`trigger`, `pre_tokens`, `post_tokens`, `duration_ms`}; `session_total` {`models`: {model: {`input`, `cache_read`, `cache_write`, `output`, `cost_usd`}}, `cost_usd`}.
  - `@dataclass Parsed: events: list[Event]`, `unknown: dict[str, int]` (record type → count), `broken: int`.
  - `claude_stream(path: Path) -> Parsed` — `assistant` → one `usage` per (`message.id`, `request_id`) (first occurrence kept; content blocks → `tool_call` per `tool_use`, `message` per `text`); `user` tool results → `tool_result` (`is_error` from the block, `size` = length of the result content as text); `system/compact_boundary` → `compaction` from `compact_metadata`; last `result` → one `session_total` from `modelUsage`/`total_cost_usd`; skipped/ignored subtypes as listed in "Checked first".
- [ ] **Step 1: failing tests:** `test_stream_usage_once_per_call` (three `assistant` lines with one `message.id`, blocks thinking/tool_use/text → 1 `usage`, 1 `tool_call`, 1 `message`; buckets incl. 5m/1h split); `test_stream_subagent_fields` (`parent_tool_use_id`, `agent_id` carried); `test_stream_last_result_is_total` (two `result` lines with growing `modelUsage` → one `session_total` equal to the second); `test_stream_compaction` (`compact_metadata` → `pre_tokens == 75726`, `trigger == "auto"`); `test_stream_tolerant` (truncated last line, a non-JSON stderr line, an unknown `type: "x-new"` → `broken == 2`, `unknown == {"x-new": 1}`, other events intact); `test_stream_tool_result_error_states` (`is_error` true / false / absent → True / False / None); `test_empty_file` (no events, no error).
- [ ] **Step 2:** FAIL. **Step 3:** implement (module docstring: sources, privacy rule, "never raises on input"). **Step 4:** full suite green.
- [ ] **Step 5:** `git commit -m "feat(transcript): event model and Claude stream reader (A0)"`

### Task 3: Codex stream parser

**Files:** Modify `houselib/transcript.py`; Test `tests/test_transcript.py`

**Interfaces:** Produces `codex_stream(path: Path) -> Parsed` — `session_id` = `thread.started.thread_id`; `command_execution` completed → `tool_call` {`name`: `"Bash"`, `input`: {`command`}} + `tool_result` {`is_error`: `exit_code != 0` when int, else None}; `agent_message` → `message`; each `turn.completed.usage` → one `usage` (normalised, `cache_write_1h = 0`, `complete = True`, key = (thread id, turn index)); `session_total` = sum of turns, model from nothing (None) unless present.
- [ ] **Step 1: failing tests:** `test_codex_normalised_tokens` (input 28659, cached 26368, write 0 → `input == 2291`, `cache_read == 26368`; assert `cached <= input_tokens` invariant on the fixture); `test_codex_exit_codes` (0 → False, 1 → True, missing → None); `test_codex_two_turns_total`.
- [ ] **Step 2–4:** FAIL → implement → green. **Step 5:** `git commit -m "feat(transcript): codex exec --json reader with normalised tokens (A0)"`

### Task 4: interactive Claude Code sessions with sub-agents, and contest assignment

**Files:** Modify `houselib/transcript.py`; Test `tests/test_transcript.py`

**Interfaces:**
- Produces:
  - `claude_session(path: Path) -> Parsed` — the main `<session>.jsonl` plus `<session>/subagents/agent-<id>.jsonl`; from `agent-<id>.meta.json`: `toolUseId` → `parent_tool_use_id`, `agentType`; `agent_id` from the file name. `assistant` → `usage` keyed (`message.id`, `requestId`), `complete = stop_reason is not None`; `isSidechain` → `sidechain`; `system/compact_boundary` → `compaction` from `compactMetadata` (`preTokens`, `postTokens`, `trigger`, `durationMs`); last `cost-state` → `session_total`; content records skipped (list in Global Constraints).
  - `project_dirs(contest: Path, projects: Path = Path("~/.claude/projects").expanduser()) -> list[Path]` — dirs whose name starts with the encoded path of the contest **or** of its `realpath` (encoding: `re.sub(r"[^A-Za-z0-9]", "-", str(path))`); `.worktrees/…` dirs match by prefix.
  - `contest_sessions(contest: Path, projects: Path = …) -> list[Path]` — main session files in those dirs whose first record with a `cwd` resolves under `contest.resolve()`.
- [ ] **Step 1: failing tests:** `test_session_with_subagent` (main file + `subagents/agent-a1.jsonl` + meta → sub-agent events have `agent_id == "a1"` and `parent_tool_use_id` from meta; totals include them); `test_meta_missing` (no `.meta.json` → `agent_id` from name, empty parent); `test_cost_state_total` (two `cost-state` records → last wins, extra Haiku model present); `test_compaction_camel_case`; `test_content_records_skipped_silently` (`file-history-snapshot`, `api-request-blob`, `ai-title` → not in `unknown`); `test_contest_assignment_symlink_and_worktree` (tmp `projects/` with dirs for the real path and for `<real>/.worktrees/T001-x`, contest given as a symlink → both sessions found; a dir for a sibling project with the same prefix but different `cwd` (e.g. `<real>-old`) → not found).
- [ ] **Step 2–4:** FAIL → implement → green. **Step 5:** `git commit -m "feat(transcript): interactive sessions with sub-agents, contest assignment (A0)"`

### Task 5: global dedup and the privacy view

**Files:** Modify `houselib/transcript.py`; Test `tests/test_transcript.py`

**Interfaces:**
- Produces:
  - `load(sources: list[tuple[str, Path]]) -> Parsed` — `sources` items are (`"stream"` | `"codex"` | `"session"`, path); runs the parsers, then dedups `usage` across everything by `key`: complete beats incomplete, then the later one, ties → higher `output`; a `sidechain` copy loses to a non-sidechain one; a `session` source whose session id equals a `stream` source's is dropped entirely (spec: counted once).
  - `view(event: Event) -> dict` — the only serialisable form: `kind`, `ts`, `session_id`, `source`, `model`, `agent_id`, `parent_tool_use_id`, and `data` reduced by the whitelist (Global Constraints); `usage`/`compaction`/`session_total` numbers pass; `message` → role and length only; never `raw`.
- [ ] **Step 1: failing tests:** `test_dedup_same_call_two_sessions`; `test_dedup_sidechain_copy_loses`; `test_dedup_incomplete_then_complete` (complete record's `output` wins); `test_synthetic_dropped`; `test_stream_and_session_same_id_counted_once`; **`test_canaries_never_in_view`**: synthetic session with `CANARY-WRITE` in a `Write` content, `CANARY-PROMPT` in an Agent prompt, `CANARY-RESULT` in a tool result, `CANARY-TEXT` in assistant text, and a Bash command `curl -H "Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz123456" x` → `json.dumps([view(e) for e in events])` contains none of the canaries and no `sk-abc…`, but contains `[REDACTED]` and `curl`.
- [ ] **Step 2–4:** FAIL → implement → green. **Step 5:** `git commit -m "feat(transcript): global dedup and privacy view (A0)"`

### Task 6: `night` and `bin/codex-task` write stream logs

**Files:**
- Modify: `houselib/night.py` (`Attempt` gets `session: str = ""`; `worker_command(contest, task, worktree, tools, previous=None, session_id: str | None = None, json_log: Path | None = None)`; `run_night` creates `session_id = str(uuid.uuid4())`, the log name `T###-<time>-N.jsonl`, passes both, keeps the id in the `running` tuple and in the `Attempt`; module docstring)
- Modify: `bin/codex-task` (`--json-log FILE`: adds `--json` to `codex exec`, stdin `/dev/null`, Codex stdout → FILE, prints one line `codex-task: exit <code>, log <FILE>`; docstring)
- Test: `tests/test_night.py`, a new small test for `codex-task --dry-run --json-log`

**Interfaces:**
- Consumes: nothing from Tasks 1–5 (A1 consumes the logs).
- Produces: Claude worker command = previous command + `--session-id <id> --output-format stream-json --verbose`; Codex worker command = `[bin/codex-task, --json-log, <log>, <task file>, <worktree>]`, and its own wrapper output goes to `<log without .jsonl>.out`; `Attempt.log` is the `.jsonl` path relative to the contest; `Attempt.session` is the Claude session id (Codex: empty here; A1 reads `thread_id` from the log).
- [ ] **Step 1: failing tests:** extend `test_commands_and_brief`: Claude cmd contains `--session-id` followed by the given id, `--output-format`, `stream-json`, `--verbose`; Codex cmd contains `--json-log` and the log path; `test_night_logs_are_jsonl_with_session` (run with the fake launcher → every `Attempt.log` ends with `.jsonl`, Claude attempts have a UUID `session`, two attempts of one task have different session ids); `test_codex_task_dry_run_json` (`bin/codex-task --dry-run --json-log /tmp/x.jsonl <task> <wd>` prints a command containing `--json`).
- [ ] **Step 2–4:** FAIL → implement → green.
- [ ] **Step 5: manual check (cheap):** `bin/codex-task --json-log <scratch>/c.jsonl <prompt file> <scratch dir>` with a one-line prompt; then `python3 -c "from houselib import transcript; …codex_stream(...)"` shows a `session_total`. No log committed.
- [ ] **Step 6:** `git commit -m "feat(night): stream-json worker logs with session ids; codex-task --json-log (A0)"`

### Task 7: manual consistency check (spec §6 acceptance, nothing committed)

- [ ] On this machine: one interactive session without compaction from `~/.claude/projects` (read locally, streaming): Σ `usage` of the main model from `claude_session` equals the `cost-state` value for that model (input, cache read, output). Write the CLI version and "equal / differs by X" into this plan (numbers only). A difference means the format changed.
- [ ] `git commit -m "docs(plans): A0 consistency check result"`

## Manual checks

2026-11-17, Claude Code 2.1.326, Codex 0.166.0 (numbers only, nothing committed from real sessions):
- Task 6: a real `bin/codex-task --json-log <file>` run (one `ls`) → `codex_stream`: message, tool_call, tool_result
  (`is_error` False), usage, session_total; `unknown` {}, `broken` 0. Codex still prints "Reading additional input from
  stdin..." with stdin closed, then proceeds (exit 0).
- Task 7: Σ `usage` from `claude_session` equals `cost-state` exactly for three sessions/models without compaction. For
  sessions resumed many times (165 and 68 process starts) `cost-state` covers **only the last process run**, since its
  `startTime`: Σ usage since then is 115.4M cache read / 333.7K output vs `cost-state` 114.6M / 332.6K (the rest are side
  calls, i.e. the K0 "outside the record" column). Follow-up: `session_total` carries `since_ms` (commit a99b590), so K0
  compares it with usage events at or after that time.
