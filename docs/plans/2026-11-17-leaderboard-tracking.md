# Leaderboard tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A background timer stores the official leaderboard of every contest that configures one; `/lb` reads it
and answers with what changed since this chat's previous `/lb` (sentences quoting the previous answer, or a table).

**Architecture:** New module `houselib/leaderboard.py` (config, poll + storage, diff, rendering, Agorize fetcher).
`house lb poll|show|agorize` in `houselib/cli.py`; a `house-lb` systemd user timer via a shared helper in
`houselib/setup.py`; `/lb` in `houselib/signalagent.handle_command` calls `leaderboard.summary` and remembers what
each chat saw in the agent state.

**Tech Stack:** Python 3 stdlib only (urllib, json, subprocess), unittest.

**Spec:** `docs/specs/2026-11-17-leaderboard-tracking-design.md`

## Global Constraints

- Config in `.house/spec.yaml`: `leaderboard: {cmd, every (default 15min), team}`; `cmd` runs with cwd = contest root, timeout 120 s.
- Command contract: stdout = JSON list `[{"team": str, "score": number, ...}]`, list order = rank order. Non-zero exit or bad JSON = failed fetch.
- Storage: `.house/leaderboard.jsonl` (one `{"at": iso, "rows": [...]}` per line, appended only when rows differ from the last one), `.house/leaderboard.state.json` (`last_try`, `last_ok`, `error`).
- `/lb` never fetches. Table when: no previous `/lb` in this chat, previous snapshot not found, > 8 h since the previous `/lb`, or > 3 changes. Otherwise sentences, quoting our previous `/lb` answer.
- A change = team whose score changed or who is new, counted only within new top 10 ∪ our rank ±2. Pure rank shifts are not changes; our own line is always present.
- Table = `rank | team | score`, top 10, plus our row (after `…`) when we are outside.
- The background process never sends anything to Signal. Agorize bearer token is never written to disk or logs.
- User-facing texts in Polish, like the rest of S7.

## Review Focus

- Command prints nothing / HTML login page instead of JSON → poll records the error, keeps old snapshots; `/lb` adds the staleness line. (Task 1 test `test_poll_bad_output_records_error`.)
- Our team missing from the board (not submitted yet, renamed) → our line says „nas nie ma w rankingu”, no crash. (Task 2 `test_render_team_absent`.)
- Ties: two teams with equal score swap order between fetches → that is a rank shift, not a change. (Task 2 `test_tie_swap_is_not_a_change`.)
- Chat with two contests → one answer per contest, each with its own memory of the previous `/lb`. (Task 3 `test_lb_two_contests_separate_memory`.)
- Contest with `.house` but no `leaderboard` section → `/lb` says „brak konfiguracji rankingu (spec.yaml: leaderboard.cmd)” instead of silence; poll skips it. (Task 1 `test_config_missing`, Task 3 `test_lb_no_config`.)

---

### Task 1: Config, poll and storage

**Files:**
- Create: `houselib/leaderboard.py`
- Test: `tests/test_leaderboard.py`

**Interfaces:**
- Produces:
  - `config(contest: Path) -> dict | None` → `{"cmd": str, "every": timedelta, "team": str | None}`; None without `leaderboard.cmd`.
  - `parse_every(text: str | None) -> timedelta` — accepts `90s`, `15min`, `2h`; None/invalid → 15 min.
  - `parse_rows(stdout: str) -> list[dict]` — normalises to `{"team": str, "score": float, **extra}`; raises `ValueError`.
  - `poll(contest: Path, now: datetime, run=subprocess.run, force: bool = False) -> str | None` — log line, None when not due/not configured.
  - `snapshots(contest: Path) -> list[dict]`, `poll_state(contest: Path) -> dict`.

- [ ] **Step 1: Write failing tests** in `tests/test_leaderboard.py` (contest in a tmp dir with `.house/spec.yaml`; `run` fake returning `CompletedProcess`):
  - `test_config_reads_spec` → `{"cmd": "x", "every": timedelta(minutes=15), "team": "UwUr2"}`; `test_config_missing` → None for no section / no cmd.
  - `test_parse_every`: `"90s"`→90 s, `"15min"`→15 min, `"2h"`→2 h, `"bzdura"`→15 min.
  - `test_parse_rows`: score `"0.53"` → `0.53`; extra keys kept; `"[]"` → `[]`; `"{}"`, `"<html>"`, row without team → ValueError.
  - `test_poll_appends_only_on_change`: two polls with the same output (force=True) → 1 snapshot; different output → 2.
  - `test_poll_respects_every`: second poll 5 min after the first (every 15min) → returns None, `run` not called.
  - `test_poll_bad_output_records_error`: exit 1 → state `error` set, `last_ok` unchanged, no new snapshot; timeout (`run` raises `subprocess.TimeoutExpired`) likewise.
  - `run` is called with `cwd=contest`, `timeout=120`, `shell=True`.
- [ ] **Step 2:** `python3 -m pytest tests/test_leaderboard.py -q` → FAIL (module missing).
- [ ] **Step 3:** Implement in `houselib/leaderboard.py` (spec via `houselib.spec.load_spec(contest / ".house")`; writes via `signalstore.write_json`-style atomic write; jsonl append + fsync). Due = `now - last_try >= every` (or force).
- [ ] **Step 4:** tests PASS.
- [ ] **Step 5:** Commit `feat(lb): poll the official leaderboard into .house/leaderboard.jsonl`.

### Task 2: Diff and rendering

**Files:**
- Modify: `houselib/leaderboard.py`
- Test: `tests/test_leaderboard.py`

**Interfaces:**
- Consumes: `snapshots`, `poll_state`, `config` (Task 1).
- Produces:
  - `@dataclass Change(team: str, score: float, old_score: float | None, rank: int, old_rank: int | None)`
  - `changes(old: list[dict], new: list[dict], team: str | None) -> list[Change]`
  - `table(rows: list[dict], team: str | None) -> str`
  - `@dataclass Summary(text: str, at: str | None, sentences: bool)` — `at` = snapshot shown; `sentences` = answer should quote the previous `/lb`.
  - `summary(contest: Path, seen_at: str | None, asked: datetime | None, now: datetime) -> Summary`

- [ ] **Step 1: Write failing tests:**
  - `test_changes_new_and_improved`: old `[A .54, B .53, C .52]`, new `[A .54, D .535, C .53, B .53]` → changes D (new, rank 2) and C (.52→.53, 3→3); B (rank shift only) absent.
  - `test_tie_swap_is_not_a_change`: `[A .5, B .5]` → `[B .5, A .5]` → `[]`.
  - `test_changes_outside_window_ignored`: change at rank 30 with us at rank 5 → ignored; at rank 7 with us at 5 → counted (±2).
  - `test_table_top10_and_us`: 15 rows, us at 13 → 10 lines `1 | A | 0.54`…, then `…`, then `13 | UwUr2 | 0.5 ← my`; us at 4 → no `…`, line 4 marked.
  - `test_summary_first_time_is_table`: `seen_at=None` → `sentences=False`, text contains `1 | `.
  - `test_summary_few_changes_sentences`: 2 changes, asked 1 h ago → `sentences=True`; text contains `D wszedł z 0.535 na #2`, `C poprawił na 0.53 (było 0.52), #3`, and our line `my: #4 (0.53), było #3`.
  - `test_summary_many_changes_table`: 4 changes → table. `test_summary_after_8h_table`: 1 change, asked 9 h ago → table.
  - `test_summary_no_changes`: → `sentences=True`, `bez zmian od 12:00 · my: #4 (0.53)`.
  - `test_render_team_absent`: team not on board → `nas nie ma w rankingu`.
  - `test_summary_stale_and_empty`: state error after last snapshot → text ends with `⚠ ostatnie pobranie nieudane (HH:MM); dane z HH:MM`; no snapshots → `brak danych rankingu` (+ the error if any).
  - Every non-empty summary ends with `ranking z HH:MM` (local time of the snapshot).
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** Implement. Worsened score uses „zmienił na”, improved „poprawił na”; rank part `z #a na #b` when it moved, `#b` otherwise. Previous snapshot = the one whose `at == seen_at`; missing → table.
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(lb): summarise leaderboard changes since the previous /lb`.

### Task 3: `/lb` in the Signal agent

**Files:**
- Modify: `houselib/signalagent.py:1053-1064` (the `lb` branch of `handle_command`)
- Modify: `core/templates/signal-hello.md` (`/lb` ranking i zmiany)
- Test: `tests/test_signalagent.py` (`CommandTest.test_lb_*`; replace `test_lb_with_contest`)

**Interfaces:**
- Consumes: `leaderboard.config`, `leaderboard.summary` → `Summary`.
- Produces: agent state key `lb_seen: {"<chat>|<contest path>": {"at": str, "ts": int, "asked": iso}}`.

- [ ] **Step 1: Write failing tests** (contest dir with spec + a prepared `leaderboard.jsonl`):
  - `test_lb_first_then_sentences_quote_previous`: first `/lb` → table; append a snapshot with one change; second `/lb` → sent with `quote` = ts of our first answer and the sentence text.
  - `test_lb_no_config`: contest without `leaderboard` → `brak konfiguracji rankingu (spec.yaml: leaderboard.cmd)`.
  - `test_lb_two_contests_separate_memory`: two contests in one chat → two messages; state has two `lb_seen` keys.
  - Keep `test_lb_without_contest` unchanged.
- [ ] **Step 2:** run `python3 -m pytest tests/test_signalagent.py -q -k lb` → FAIL.
- [ ] **Step 3:** Implement: for each contest, `summary(...)` with the stored `at`/`asked`; quote = `(prev["ts"], own number)` only when `sentences` and a previous ts exists, else the user's `/lb` message; store the new `ts` returned by `_say` (skip storing when None). Drop the `exp.summary_line` use from `/lb`.
- [ ] **Step 4:** PASS; whole `tests/test_signalagent.py` PASS.
- [ ] **Step 5:** Commit `feat(lb): /lb answers from the stored leaderboard`.

### Task 4: CLI, Agorize fetcher and timer

**Files:**
- Modify: `houselib/leaderboard.py` (`agorize_board`), `houselib/cli.py` (`cmd_lb`, parser, `house setup --lb-timer`), `houselib/setup.py` (shared timer helper + `install_lb_timer`), `README.md` (S7 section: `/lb`, `house lb`, `--lb-timer`)
- Test: `tests/test_leaderboard.py`, `tests/test_signal_timer.py`

**Interfaces:**
- Produces:
  - `agorize_board(page_url: str, fetch=None, sort: str | None = None) -> list[dict]` — `fetch(url, headers) -> str` (default urllib); parses `const url = '…'` and `const token = '…'` from the page; API JSON `{"data": [{"team__name", "score", "id"?, "filename"?}]}` → contract rows; `sort` `"max"`/`"min"` stable-sorts by score. Raises `ValueError` when url/token not found.
  - `house lb poll [--force] [name]`, `house lb show <name>` (prints the table of the latest snapshot), `house lb agorize <url> [--sort max|min]` (prints JSON).
  - `setup.install_lb_timer(root=HOUSE_ROOT, home=None, run=subprocess.run, every="5min") -> list[Path]` — units `house-lb.service` (`ExecStart=<root>/bin/house lb poll`, `TimeoutStartSec=900`) / `house-lb.timer`.

- [ ] **Step 1: Write failing tests:**
  - `test_agorize_parses_page` with an HTML fixture containing the two consts and a fake `fetch`: API called with `Authorization: Bearer <token>`; returns `[{"team": "A", "score": 0.54, ...}]`; `sort="max"` reorders.
  - `test_agorize_missing_token` → ValueError.
  - `test_cli_poll_all_contests`: two registered contests, one configured → one snapshot written, other skipped; exit 0.
  - `test_lb_timer_units` (in `tests/test_signal_timer.py`, same style as the existing timer test): unit files contain `house lb poll` and `OnUnitActiveSec=5min`; `systemctl --user enable --now house-lb.timer` called.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** Implement; refactor `install_signal_timer` to use the shared helper without changing its units (existing timer tests must still pass).
- [ ] **Step 4:** full suite `python3 -m pytest -q` PASS.
- [ ] **Step 5:** Commit `feat(lb): house lb poll/show/agorize and the house-lb timer`.
