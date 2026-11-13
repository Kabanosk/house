# `house check` and the merge gate (P1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Acceptance criteria are run by code, frozen at plan time, and a task reaches `main` only through `house merge`, which checks the branch, merges, re-runs every merged task's checks on `main` and reverts the merge if anything regressed.

**Architecture:** `houselib/fm.py` learns block lists (YAML `- ` items, incl. one level of mappings) and in-place field updates. New `houselib/checks.py` owns criteria parsing, freezing, running and recording (`.house/checks/T###.json`). New `houselib/merge.py` owns the gate (check → merge → regression → revert or seal). `cli.py` exposes `house check` and `house merge`. Core playbooks/profiles/skills switch to the new statuses.

**Tech Stack:** Python 3.11+ stdlib, `unittest`, git.

**Spec:** `docs/superpowers/specs/2026-11-12-system-hardening-design.md` §5 (P1).

## Global Constraints

- Standard library only.
- Statuses: `todo` → `doing` → `review` (coder stops here) → `merged` (gate passed; manual criteria may be pending) → `done` (all criteria incl. manual passed); `blocked` as before. For `depends_on`, `merged` and `done` both count as finished.
- Only `house` writes `.house/checks/` and task `status` after `review` (spec §5 „Statusy ustawia coordinator po bramce”).
- A task file without the new format (plain-string `acceptance`) keeps working: each string is a `manual` criterion `AC-<n>`.
- Default criterion timeout 600 s; `timeout:` per criterion overrides.
- Human-facing lines (PROGRESS.md, D-###) in Polish; code, reports, CLI output in English.

## Review Focus

- A criterion command that hangs → marked failed after its timeout; `house check`/`house merge` never hang.
- Task file edited after freeze (e.g. `run: "true"`) → check is red with "criteria changed since freeze".
- Merge conflict → merge aborted, `main` unchanged, exit 1.
- Main checkout has uncommitted changes to tracked files outside `.house/` → `house merge` refuses before touching git.
- Task with only `manual` criteria (pitch, research) → merges, status `merged` (not `done`).

---

### Task 1: frontmatter block lists and field updates

**Files:** Modify `houselib/fm.py`; Test `tests/test_fm.py`

**Interfaces — Produces:**
- `parse_frontmatter(text)` / `parse_mapping(text)` additionally accept a key with an empty value followed by indented `- ` items: scalars (`- foo`) or one-level mappings (`- id: AC-1` then deeper-indented `run: "…"` lines). Anything deeper → `ValueError`.
- `set_fields(text: str, updates: dict[str, object]) -> str` — rewrites top-level `key: value` lines of the frontmatter (scalars only, rendered like the existing files: `null`, `true`, quoted only if the value contains `: ` or starts with a quote), appends missing keys just before the closing `---`, leaves body and all other lines byte-identical.

- [ ] **Step 1: Failing tests** in `tests/test_fm.py`: `test_block_list_of_mappings` (acceptance with two items `{id: AC-1, run: "pytest -q"}` and `{id: AC-2, manual: "golden path"}`), `test_block_list_of_strings` (`protected:` + `- tests/test_a.py`), `test_inline_list_still_parsed`, `test_too_deep_raises`, `test_set_fields_replaces_and_appends` (status `todo`→`review`, new key `owner: coder` added, body unchanged), `test_set_fields_keeps_block_lists` (acceptance block untouched byte-for-byte).
- [ ] **Step 2:** `python3 -m unittest tests.test_fm -v` → FAIL.
- [ ] **Step 3:** Implement in `houselib/fm.py` (line-based; indentation of the first `- ` item sets the list level).
- [ ] **Step 4:** `python3 -m unittest tests.test_fm -v` → PASS; full suite green.
- [ ] **Step 5:** Commit `feat(fm): block lists and in-place field updates`.

### Task 2: `houselib/checks.py`

**Files:** Create `houselib/checks.py`; Test `tests/test_checks.py`

**Interfaces — Consumes:** Task 1. **Produces:**
- `@dataclass Criterion: id: str; kind: str  # "run" | "manual"; run: str | None; expect: str | None; text: str | None; timeout: int`
- `@dataclass Task: id: str; path: Path; meta: dict; criteria: list[Criterion]; protected: list[str]`
- `load_task(house: Path, task_id: str) -> Task` (file `house/tasks/<task_id>-*.md`; `FileNotFoundError` if none).
- `parse_acceptance(raw) -> list[Criterion]`: string → manual `AC-<n>` (1-based position); mapping with `run` or `manual`; missing `id` → `AC-<n>`; mapping with neither → `ValueError`.
- `freeze(house: Path, task_id: str) -> Path` → `house/checks/<id>.json` = `{"task": id, "frozen": {"criteria": [...], "protected": {path: null}}, "frozen_at": iso, "results": {}}`; `FileExistsError` if already frozen.
- `refreeze(house: Path, task_id: str, reason: str) -> str` → rewrites `frozen` (keeps results of unchanged criterion ids, protected hashes of still-listed paths), appends to `DECISIONS.md` a `## D-<next 3-digit> Zmiana kryteriów <id>` entry in the template's format (Decyzja / Dlaczego = reason / Kto: coordinator / Data), returns the D id.
- `@dataclass Result: id: str; passes: bool | None; exit: int | None; output_tail: str; ran_at: str | None; commit: str | None`
- `@dataclass CheckReport: task_id: str; results: list[Result]; mismatch: bool; init_ok: bool` with `green -> bool` (no mismatch, init ok, every `run` result passes) and `complete -> bool` (green and every manual `passes is True`).
- `run_checks(house: Path, task_id: str, workdir: Path) -> CheckReport` — uses the **frozen** criteria; `mismatch` when the task file's current acceptance/protected differ from the frozen ones; runs `house/init.sh` first if it exists (`bash <path>` in `workdir`, failure → `init_ok=False`); each `run` via `subprocess.run(cmd, shell=True, cwd=workdir, capture_output=True, text=True, timeout=c.timeout)`; timeout → fail with `exit=None`, tail `"timeout after <n> s"`; `commit` = `git rev-parse HEAD` in workdir; stores results in the checks file (manual results keep their stored value). Unfrozen task → `ValueError("T### is not frozen; run house check --freeze")`.
- `expect_ok(expect: str | None, stdout: str) -> bool`: `None` → True. Comparison form `^\s*(?P<label>[A-Za-z_][\w .-]*?)\s*(?P<op>>=|<=|==|>|<)\s*(?P<num>-?\d+(?:\.\d+)?)\s*$` → take the **last** match of `<label>\s*[:=]\s*(-?\d+(?:\.\d+)?)` in stdout (case-insensitive); no number found → False. Otherwise a regex searched in stdout (`re.M`).
- `record_manual(house: Path, task_id: str, criterion_id: str, evidence: str) -> None` — `passes: True`, `output_tail: "evidence: <evidence>"`; unknown id or `run` criterion → `ValueError`.
- `seal_protected(house: Path, task_id: str, workdir: Path) -> list[str]` — fills `null` hashes with sha256 of the files now in workdir; returns paths still missing.

- [ ] **Step 1: Failing tests** in `tests/test_checks.py` (temp `.house/` + workdir, no git needed except `commit` → tolerate non-repo with `commit=None`): parse legacy strings; parse mappings incl. default ids; freeze writes file and refuses twice; run pass/fail; timeout (`timeout: 1`, `run: "sleep 5"`) → fails fast; expect regex; expect `score >= 0.71` with stdout `score: 0.70\nscore: 0.75` → True, `score: 0.5` → False, no number → False; mismatch after editing task to `run: "true"` → `green False`; init.sh failing → `green False`; manual-only task → `green True`, `complete False`, after `record_manual` → `complete True`; refreeze appends `D-001` (and `D-004` when D-001..D-003 exist) and keeps results of unchanged ids; seal fills hashes and reports missing.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** run → PASS, full suite green.
- [ ] **Step 5:** Commit `feat(check): frozen acceptance criteria run by code`.

### Task 3: `house check` CLI

**Files:** Modify `houselib/cli.py`; Test `tests/test_check_cli.py`

**Interfaces — Consumes:** Task 2. **Produces:** `house check <contest> <T###> [--worktree PATH] [--freeze | --refreeze --reason TEXT | --manual AC-n --evidence TEXT]` and `house check <contest> --all`:
- default: `run_checks` with workdir = `--worktree` or the contest checkout; prints one line per criterion `PASS AC-1 …` / `FAIL AC-2 … (exit 1)` / `MANUAL AC-3 pending|ok`, then `GREEN`/`RED` (+ `criteria changed since freeze` on mismatch); exit 0 iff green.
- `--all`: every task with status `merged`/`done`, in the contest checkout; exit 0 iff all green.
- `--refreeze` without `--reason` → error exit 2 (argparse).

- [ ] **Step 1: Failing tests** calling `cli.main([...])` against a contest from `scaffold.new_contest` (helpers `make_root`, `FAKE_TOOLS`): exit codes for green/red, output lines, `--freeze` creates the json, `--all` covers two merged tasks.
- [ ] **Step 2–4:** FAIL → implement → PASS, full suite green.
- [ ] **Step 5:** Commit `feat(cli): house check`.

### Task 4: `house merge` — the gate

**Files:** Create `houselib/merge.py`; Modify `houselib/cli.py`; Test `tests/test_merge.py`

**Interfaces — Consumes:** Tasks 1–3. **Produces:** `merge_task(contest: Path, task_id: str, worktree: Path | None = None) -> MergeOutcome` with `@dataclass MergeOutcome: ok: bool; status: str; message: str; reverted: bool`; CLI `house merge <contest> <T###> [--worktree PATH]` printing `message`, exit 0 iff `ok`. Steps, in this order:
1. Refuse (ok=False, nothing changed) if the contest checkout has uncommitted changes to tracked files outside `.house/`. Commit pending `.house/` changes as `chore(house): state before <id>`.
2. Branch = task `branch` field (required). Worktree = `--worktree`, else `<contest>/.worktrees/<branch without "task/">`; missing worktree → ok=False (every task runs in its own worktree, lifecycle stage 4).
3. `run_checks` in the worktree; red → ok=False, status unchanged (`review`), message lists failures.
4. `git merge --no-ff --no-edit <branch>` in the contest checkout; conflict → `git merge --abort`, ok=False.
5. Regression: `run_checks` in the checkout for this task and every `merged`/`done` task. Red → `git revert -m 1 --no-edit HEAD`, status `blocked`, Polish line in `PROGRESS.md` (`- <id> cofnięte po scaleniu: regresja w <ids>`), `.house/` committed, ok=False, `reverted=True`.
6. Green → `seal_protected` in the checkout; status `done` if `complete` else `merged`; `.house/` committed as `chore(house): <id> merged`; `git worktree remove` (if under `.worktrees/`) and `git branch -d <branch>`; ok=True.

- [ ] **Step 1: Failing tests** in `tests/test_merge.py` with real git in temp dirs: happy path (task branch adds `calc.py` + its test; criterion `python3 -m unittest -q`), status `merged`→ with only run criteria `done`, worktree and branch removed; red branch → status `review`, main unchanged; regression (T002 breaks T001's check) → reverted, T002 `blocked`, T001 still green on main; conflict → aborted; dirty tracked file outside `.house/` → refused, file intact; manual-only task → `merged`.
- [ ] **Step 2–4:** FAIL → implement → PASS, full suite green.
- [ ] **Step 5:** Commit `feat(merge): house merge gate with regression revert`.

### Task 5: core content and README follow the new flow

**Files:** Modify `core/templates/house/tasks/TEMPLATE.md`, `core/playbooks/lifecycle.md` (stages 3–5), `adapters/claude-code/skills/house-plan/SKILL.md`, `adapters/claude-code/skills/house-run/SKILL.md`, `core/agents/coder.md`, `core/agents/coder-codex.md`, `houselib/adapters.py` (`CODEX_DRIVER` step 5), `core/agents/judge.md`, `README.md`; Test `tests/test_core_content.py` (new assertions).

Content to land (wording is the implementer's; these facts are fixed):
- TEMPLATE: `acceptance` as block list of `{id, run, expect?, timeout?}` / `{id, manual}`; `protected:` block list; status values listed in a comment.
- house-plan: at least one `run` criterion per task with logic; write `.house/init.sh` (start app / smoke test) when the project has one; at the end `house check <contest> T### --freeze` for every new task, commit `.house/`.
- house-run: ready = `todo` with every `depends_on` in `merged`/`done`; after a worker returns `review`, run `$HOUSE_ROOT/bin/house merge <contest> T###`; red gate → send back with the failing lines; never `git merge` by hand.
- coder / coder-codex / `CODEX_DRIVER`: run `house check <contest> T### --worktree <path>` before finishing; finish with `status: review`, never `done`; never edit `acceptance`/`protected` or files listed in `protected`.
- judge: manual criteria are recorded with `house check … --manual AC-n --evidence reports/judge-<n>.md`.
- README: new statuses, `house check`, `house merge` in the quick-start table/flow.

- [ ] **Step 1: Failing tests** in `tests/test_core_content.py`: TEMPLATE parses with the new fm and has `protected`; house-run mentions `house merge` and `merged`; coder and coder-codex mention `status: review` and `house check`; generated `coder-codex` agent (via `adapters.claude_code`) contains `house check`; judge mentions `--manual`.
- [ ] **Step 2–4:** FAIL → edit content → PASS, full suite green.
- [ ] **Step 5:** Commit `docs(core): review/merged statuses, house check and house merge in the flow`.

### Task 6: end-to-end on a throwaway contest

- [ ] **Step 1:** In the scratchpad: `house new`-style contest from the worktree code (`scaffold.new_contest` via a short script), two tasks frozen, worktrees per `/house-run`, one good and one regressing branch; run `bin/house check` and `bin/house merge` as the coordinator would. Expected: first `done`, second reverted + `blocked`, PROGRESS.md line, `house check --all` GREEN.
- [ ] **Step 2:** Record the transcript summary in the PR description (no repo file).
