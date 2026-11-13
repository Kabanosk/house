# `house guard` and Claude Code hooks (P2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rules that today live only in prompts are enforced by code: Claude Code hooks block edits to protected files, `.house/checks/`, secret commits and unsafe pushes; the same rules run after the fact on a task branch (`house guard diff`) for Codex and as part of `house merge`; a SessionStart hook hands every session the contest state.

**Architecture:** `houselib/spec.py` reads the nested `spec.yaml`. `houselib/guard.py` holds the rules (pure functions over paths, commands and git state). `bin/house-hook` is the thin entry point Claude Code runs (stdin JSON → exit 0 / exit 2 + reason). `houselib/adapters.py` writes the hooks and a command allowlist into `.claude/settings.local.json` (never committed). `houselib/merge.py` and the Codex driver call `guard.diff_violations`.

**Tech Stack:** Python 3.11+ stdlib, `unittest`, git, Claude Code hooks (PreToolUse / SessionStart; input JSON on stdin with `tool_name`, `tool_input`, `cwd`; exit code 2 blocks and shows stderr to the model).

**Spec:** `docs/superpowers/specs/2026-11-12-system-hardening-design.md` §6 (P2); roadmap item 1 (allowlist) per Wojtek 2026-11-13.

## Global Constraints

- Stdlib only. Hooks must never crash a session: any internal error → exit 0 and a one-line warning on stderr (fail open), except the explicit block rules (exit 2).
- Hooks live only in `.claude/settings.local.json` (excluded via `.git/info/exclude`), never in a committed file (spec §6, Q6).
- `HOUSE_GUARD=off` disables `pre-tool` blocking and appends a Polish line to `PROGRESS.md` once per session.
- Protected = every path in any frozen task's `protected` whose hash is known (file existed at freeze or sealed at merge), plus files named in `spec.scoring.local_scorer`, plus everything under `.house/checks/`. Paths not yet existing at freeze stay writable (the task creates them; sealed at merge).
- Secret rules reuse `export.is_excluded`-style `.env*` names (except `.env.example`) and `export.has_secret`.
- Push rules: remote must be in `spec.guard.push_allow` (default `[origin]`); any force push to the contest's main branch is blocked.

## Review Focus

- File path given relative to a worktree (`.worktrees/T001-x/test_calc.py`) or absolute → mapped to the repo-relative path before matching protection.
- `git add -A` / `git add .` / `git commit -a` with an untracked `.env` present (repo without our .gitignore, e.g. adopted) → blocked; with `.env` gitignored → allowed.
- Existing `.claude/settings.local.json` with the user's own permissions and hooks → kept; re-adapt replaces only house's entries, no duplicates.
- Hook runs outside any house contest (cwd elsewhere) → exit 0 silently.
- Malformed hook input / missing spec.yaml → exit 0 (fail open) with a warning, never a traceback.

---

### Task 1: `houselib/spec.py` — read the nested spec

**Files:** Create `houselib/spec.py`; Test `tests/test_spec.py`
**Produces:** `load_spec(house: Path) -> dict` (top-level keys → scalar or dict of scalars/inline lists; unquoted values have trailing ` # comment` stripped; block lists reuse `fm`), `get(spec: dict, dotted: str, default=None)`.
- [ ] Tests: the real `core/templates/house/spec.yaml` loads; `get(s, "limits.max_parallel_agents") == 3`; `get(s, "scoring.local_scorer") is None`; comment stripping (`deploy: true  # x` → True); quoted value with `#` kept; missing file → `{}`.
- [ ] FAIL → implement → PASS → commit `feat(spec): read nested spec.yaml`.

### Task 2: freeze records hashes of protected files that already exist

**Files:** Modify `houselib/checks.py` (`freeze`, `refreeze`); Test `tests/test_checks.py`
**Produces:** `freeze(house, task_id, workdir: Path | None = None)` — hashes each protected path that exists under `workdir` (default `house.parent`), `null` otherwise. `refreeze` hashes newly listed paths the same way.
- [ ] Tests: existing protected file → 64-char hash at freeze; missing → null; refreeze adds a new path with its hash.
- [ ] FAIL → implement → PASS → commit `feat(check): freeze hashes protected files that exist`.

### Task 3: `houselib/guard.py` — the rules

**Files:** Create `houselib/guard.py`; Test `tests/test_guard.py`
**Produces:**
- `contest_root(cwd: Path) -> Path | None` — main checkout that has `.house/` (walks up; from a worktree uses `git rev-parse --git-common-dir` → its parent).
- `repo_relative(path: str, cwd: Path, contest: Path) -> str | None` — absolute/relative path → path relative to the repo root it lives in (main checkout or a `.worktrees/*` worktree).
- `protected_paths(contest: Path) -> set[str]` — per Global Constraints.
- `check_edit(rel: str, contest: Path) -> str | None` — reason if `rel` is protected or under `.house/checks/`.
- `check_bash(command: str, cwd: Path, contest: Path) -> str | None` — (a) writes to a protected path or `.house/checks/` via `>`, `>>`, `tee`, `sed -i`, `mv`, `rm`, `cp <src> <dest>`; (b) `git add`/`git commit -a` that would stage a `.env*` file or a file with `has_secret`; (c) `git push` to a remote not allowed, or force push (`-f`, `--force`, `--force-with-lease`, `+ref`) touching the main branch.
- `diff_violations(contest: Path, task_id: str, worktree: Path) -> list[str]` — on the task branch vs merge-base with the main checkout's HEAD plus uncommitted worktree changes: changed protected files (hash known) of any task, `.env*`/secret files; in the main checkout: uncommitted changes under `.house/checks/`.
- [ ] Tests (temp git repos via `tests.helpers`): each rule positive and negative; worktree path mapping; `.env` ignored vs not ignored; force push to main vs feature branch; `push_allow` from spec; `diff_violations` finds an edited sealed test and a tampered checks file.
- [ ] FAIL → implement → PASS → commit `feat(guard): protection, secret and push rules`.

### Task 4: `bin/house-hook` and SessionStart summary

**Files:** Create `bin/house-hook`, `houselib/hook.py`; Test `tests/test_hook.py`
**Produces:** `house-hook pre-tool` (stdin JSON: `tool_name` in `Edit|Write|MultiEdit|NotebookEdit` → `tool_input.file_path`/`notebook_path`; `Bash` → `tool_input.command`) → exit 2 with `house guard: <reason>. Ask the coordinator / human if this is really needed.` on stderr, else exit 0. `house-hook session-start` → stdout ≤ 25 lines: contest name, `kind`, `mode`, goal (first 120 chars), open Q count (`## Q-### [open]`), task counts by status + ready ids, last red checks, hours since `last_briefing`. `HOUSE_GUARD=off` handling per Global Constraints.
- [ ] Tests: run the script with `subprocess` and JSON on stdin: blocked edit → 2 + reason; allowed → 0; Bash secret add → 2; outside a contest → 0, no output; malformed JSON → 0 + warning; `HOUSE_GUARD=off` → 0 and a PROGRESS line; session-start output contains the counts.
- [ ] FAIL → implement → PASS → commit `feat(hook): house-hook pre-tool and session-start`.

### Task 5: adapt writes hooks and the allowlist into `settings.local.json`

**Files:** Modify `houselib/adapters.py`; Test `tests/test_adapt.py`
**Produces:** `claude_settings(contest, root) -> dict` = `{"hooks": {"PreToolUse": [{"matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash", "hooks": [{"type": "command", "command": "<abs>/bin/house-hook pre-tool"}]}], "SessionStart": [{"hooks": [{"type": "command", "command": "<abs>/bin/house-hook session-start"}]}]}, "permissions": {"allow": [...]}}` with allow = `Bash(<abs>/bin/house:*)`, `Bash(<abs>/bin/codex-task:*)`, `Bash(git status:*)`, `Bash(git diff:*)`, `Bash(git log:*)`, `Bash(git add:*)`, `Bash(git commit:*)`, `Bash(git worktree:*)`, `Bash(git -C:*)`, `Bash(python3 -m unittest:*)`, `Bash(pytest:*)`, `Bash(uv run:*)`, `Bash(npm test:*)`, `Bash(npx tsc:*)`. `adapt` merges it into `.claude/settings.local.json`: keeps every user key and every hook entry whose command does not contain `house-hook`; replaces house's entries; de-duplicates allow entries; adds `.claude/settings.local.json` to `.git/info/exclude`. Not in the manifest, not committed.
- [ ] Tests: fresh contest → file with both hooks; user's own `permissions.allow` and a foreign hook survive two adapts with no duplicates; file not in `git ls-files`; exclude line present once.
- [ ] FAIL → implement → PASS → commit `feat(adapt): guard hooks and allowlist in settings.local.json`.

### Task 6: guard in the merge gate, CLI, Codex driver

**Files:** Modify `houselib/merge.py`, `houselib/cli.py`, `houselib/adapters.py` (`CODEX_DRIVER`), `README.md`; Test `tests/test_merge.py`
**Produces:** `house guard diff <contest> T### [--worktree P]` (prints violations, exit 1 if any); `merge_task` runs `diff_violations` before the checks → violations = red with the list; `CODEX_DRIVER` step 4 runs `house guard diff` before committing and reverts the offending files (`git -C <wt> checkout -- <file>`) or reports them.
- [ ] Tests: merge refuses a branch that edited a sealed protected file; CLI exit codes; driver text mentions `house guard diff`.
- [ ] FAIL → implement → PASS → commit `feat(guard): diff check in merge gate, CLI and Codex driver`.

### Task 7: real Claude Code session

- [ ] In a scratch copy (like P1's e2e): adapt a contest, freeze a task with an existing protected test, then `claude -p --settings <contest>/.claude/settings.local.json --permission-mode acceptEdits "Append a line to test_calc.py"` in the contest. Expected: the edit is refused with the `house guard:` reason in the transcript; `test_calc.py` unchanged. Record the outcome in the PR description.
