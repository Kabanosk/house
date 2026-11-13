# Context measurement (P0) and generated-files manifest (P6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `house doctor --context <contest>` reports how much always-loaded context a contest session carries, `house adapt` stops copying vendor skills into contests, and `house adapt` only ever touches files it generated itself (manifest), never deleting teammates' files.

**Architecture:** Two new stdlib modules: `houselib/context.py` (read-only measurement of files Claude Code loads) and `houselib/manifest.py` (record of generated files with sha256, used to write, back up and remove safely). `houselib/adapters.py` switches from "write directly + rmtree" to "build a {relpath: text} map, hand it to the manifest writer".

**Tech Stack:** Python 3.11+ standard library only, `unittest`.

**Spec:** `docs/superpowers/specs/2026-11-12-system-hardening-design.md` §4 (P0) and §10 (P6).

## Global Constraints

- Standard library only (README: „Python 3.11+ (tylko biblioteka standardowa)”).
- `house adapt` must never overwrite spec, decisions, questions, tasks or reports (README „Aktualizacja systemu”).
- A pre-existing hand-written `AGENTS.md`/`CLAUDE.md` is kept as `*.orig` (existing behaviour, tests in `tests/test_review_fixes.py::AdaptRobustnessTest`).
- Generated paths that are gitignored are not committed (existing `_commit_owned` behaviour).
- Token estimate = `ceil(len(text) / 4)`; the report says it is an estimate.
- Tests: `python3 -m unittest discover -s tests -v`, all 118 existing tests stay green (adjusted only where this plan changes the behaviour they pin).

## Review Focus

- Adopted team repo with its own `.claude/agents/teammate.md` → survives `house adapt`, is not committed by it.
- Contest adapted by the old code (no `.house/generated.json`, vendor skills copied) → first new `adapt` removes identical vendor copies, keeps a vendor copy someone edited, does not back up our own old agents as `.orig`.
- A generated agent edited by hand → next `adapt` keeps the edit as `<name>.orig` and warns, then writes the fresh one.
- A profile removed from `core/agents/` (stale generated file) → removed if unchanged, kept with a warning if edited.
- `~/.claude/plugins/installed_plugins.json` missing or malformed, plugin disabled in `settings.json` → `doctor --context` still prints a report (plugin section empty) instead of crashing.

---

### Task 1: `house doctor --context` (measurement)

**Files:**
- Create: `houselib/context.py`
- Modify: `houselib/cli.py` (`cmd_doctor`, `doctor` subparser)
- Test: `tests/test_context.py`

**Interfaces:**
- Produces:
  - `@dataclass SkillEntry: name: str; source: str; path: Path; tokens: int; model_invocable: bool`
  - `estimate_tokens(text: str) -> int`
  - `skill_entries(skills_dir: Path, source: str) -> list[SkillEntry]` — one entry per `<dir>/*/SKILL.md`; `tokens` = estimate of `name` + `description` from frontmatter (what the model sees in its skill list); `model_invocable = meta.get("disable-model-invocation") is not True`.
  - `plugin_skill_dirs(home: Path) -> list[tuple[str, Path]]` — `("plugin:<plugin id before @>", installPath/"skills")` for plugins in `home/.claude/plugins/installed_plugins.json` that are `True` in `home/.claude/settings.json` `enabledPlugins`; missing/malformed files → `[]`.
  - `@dataclass ContextReport: files: list[tuple[str, int]]; skills: list[SkillEntry]; agents: list[tuple[str, int]]; duplicates: dict[str, list[str]]` with property `total -> int` = files + invocable skills + agents.
  - `measure(contest: Path, home: Path) -> ContextReport` — files: `contest/AGENTS.md`, `contest/CLAUDE.md`, `home/.claude/CLAUDE.md` (only those that exist, label = path relative to contest or `~/…`); skills from sources `contest` (`contest/.claude/skills`), `user` (`home/.claude/skills`), each plugin; agents: `(name, tokens of description)` from `contest/.claude/agents/*.md`; duplicates: skill names present in ≥ 2 sources → sorted list of sources.
  - `format_report(r: ContextReport) -> str` — sections „Always loaded”, „Skills (model-invocable)”, „Skills hidden from model (disable-model-invocation)” (count only), „Agents”, „Duplicates”, last line `TOTAL ≈ <n> tokens (estimate: chars/4)`.

- [ ] **Step 1: Write the failing tests** in `tests/test_context.py` with a fake `home` and contest in a `TemporaryDirectory`:
  - `test_estimate_tokens`: `estimate_tokens("abcd") == 1`, `estimate_tokens("abcde") == 2`, `estimate_tokens("") == 0`.
  - `test_skill_entries_reads_frontmatter`: skill `a` with `description: x` and skill `h` with `disable-model-invocation: true` → two entries, `a.model_invocable is True`, `h.model_invocable is False`, `a.source == "user"`.
  - `test_plugin_dirs_only_enabled`: installed_plugins.json lists `p1@m` and `p2@m`, settings enables only `p1@m` → `[("plugin:p1", <installPath>/skills)]`.
  - `test_plugin_dirs_tolerate_missing_files`: empty home → `[]`; malformed JSON → `[]`.
  - `test_measure_finds_duplicates_and_totals`: skill `grill-me` in `home/.claude/skills` and in the plugin dir, `AGENTS.md` of 400 chars → `duplicates == {"grill-me": ["plugin:p1", "user"]}`, `("AGENTS.md", 100) in r.files`, disabled skill not counted in `r.total`.
  - `test_format_report_has_total`: last line starts with `TOTAL ≈`.
  - `test_cli_doctor_context_requires_name`: `cli.main(["doctor", "--context"])` returns 1 and prints `error:` to stderr.

- [ ] **Step 2: Run** `python3 -m unittest tests.test_context -v` — expected: FAIL (`ModuleNotFoundError: houselib.context`).

- [ ] **Step 3: Implement** `houselib/context.py` per Interfaces (frontmatter via `houselib.fm.parse_frontmatter`; skip a skill whose frontmatter fails to parse, counting its whole `SKILL.md` header line as `name`). In `cli.py`: `doctor` gets `--context` (`store_true`); with `--context` and no name → error, return 1; with name → `print(context.format_report(context.measure(contest_dir(name).resolve(), Path.home())))` after the tools summary, without writing `tools.json` twice.

- [ ] **Step 4: Run** `python3 -m unittest tests.test_context -v` — expected: PASS.

- [ ] **Step 5: Baseline measurement.** Run `bin/house doctor --context grok-bot-wro-2026` and save the output to `docs/context-budget.md` under a heading `## Before (2026-11-13, vendor skills copied)` in a fenced block.

- [ ] **Step 6: Commit**

```bash
git add houselib/context.py houselib/cli.py tests/test_context.py docs/context-budget.md
git commit -m "feat(doctor): --context measures always-loaded context per contest"
```

### Task 2: manifest module

**Files:**
- Create: `houselib/manifest.py`
- Test: `tests/test_manifest.py`

**Interfaces:**
- Produces:
  - `MANIFEST = ".house/generated.json"` (format: `{"version": 1, "files": {"<relpath>": "<sha256 hex>"}}`, keys sorted, trailing newline).
  - `sha256_text(text: str) -> str`
  - `load(contest: Path) -> dict[str, str] | None` — `None` when the file does not exist (legacy contest).
  - `save(contest: Path, files: dict[str, str]) -> Path`
  - `@dataclass WriteResult: written: list[str]; backed_up: list[str]; removed: list[str]; kept_modified: list[str]`
  - `apply(contest: Path, generated: dict[str, str], old: dict[str, str] | None, legacy_owned: Callable[[str], bool]) -> WriteResult` — writes every `generated[rel]` (creating parents) and returns what happened. Ownership of an existing file at `rel`:
    - `old` is not None and `rel in old`: ours; if its current sha256 ≠ `old[rel]` (edited by hand) **and** ≠ new text's sha → back up first.
    - `old` is None (legacy): ours iff `legacy_owned(rel)`; otherwise foreign.
    - foreign and content differs from new text → back up first.
    - Backup name: `<name>.orig`; if that exists, keep the existing `.orig` and do not create another (matches current `AGENTS.md` behaviour).
    - Stale: every `rel in old` not in `generated` → delete if unchanged (sha matches `old[rel]`), else leave in place and list in `kept_modified`; afterwards remove now-empty parent dirs up to (not including) `contest`.
  - `drift(contest: Path) -> list[tuple[str, str]]` — for each manifest entry: `"modified"` (sha differs) or `"missing"`; empty list when no manifest.

- [ ] **Step 1: Write the failing tests** in `tests/test_manifest.py` (plain temp dir, no git needed):
  - `test_fresh_write_records_hashes`: `apply(c, {"a/x.md": "1"}, None, lambda r: True)` → file written, `written == ["a/x.md"]`; `save` then `load` round-trips `{"a/x.md": sha256_text("1")}`.
  - `test_foreign_file_backed_up_in_legacy_mode`: existing `AGENTS.md` = `"theirs"`, `legacy_owned` returns False → `AGENTS.md.orig == "theirs"`, `AGENTS.md == new`, `backed_up == ["AGENTS.md"]`.
  - `test_legacy_owned_overwritten_without_backup`: existing `.claude/agents/coder.md`, `legacy_owned` True → no `.orig`.
  - `test_hand_edited_generated_file_backed_up`: old manifest has hash of `"gen1"`, file now `"edited"`, new text `"gen2"` → `coder.md.orig == "edited"`, file `== "gen2"`.
  - `test_existing_orig_not_overwritten`: `.orig` already `"first"` → stays `"first"`.
  - `test_stale_unchanged_removed_and_dir_pruned`: old has unchanged `.claude/agents/gone.md` (next to a still-generated `.claude/agents/keep.md`) and unchanged `x/only.md`; neither stale file is generated now → `gone.md` deleted, `.claude/agents/` kept, `x/` removed entirely, `removed == [".claude/agents/gone.md", "x/only.md"]`.
  - `test_stale_edited_kept`: stale file edited → still exists, `kept_modified == [rel]`.
  - `test_unlisted_files_untouched`: `.claude/agents/teammate.md` not in old/generated, `old` not None → untouched, not in any result list.
  - `test_drift`: manifest entry modified → `[("a.md", "modified")]`; deleted → `"missing"`; no manifest → `[]`.

- [ ] **Step 2: Run** `python3 -m unittest tests.test_manifest -v` — expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement** `houselib/manifest.py` per Interfaces.

- [ ] **Step 4: Run** `python3 -m unittest tests.test_manifest -v` — expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add houselib/manifest.py tests/test_manifest.py
git commit -m "feat(manifest): record generated files, back up hand edits, remove only our stale files"
```

### Task 3: adapters use the manifest; vendor skills no longer copied

**Files:**
- Modify: `houselib/adapters.py` (`claude_code`, `codex`, `_write_generated` removed, `_commit_owned`, `adapt`)
- Modify: `houselib/cli.py` (`cmd_adapt` prints warnings; `cmd_doctor <name>` prints drift)
- Modify: `tests/test_vendor_skills.py`, `tests/test_adapt.py`
- Test: `tests/test_adapt.py` (new class `ManifestAdaptTest`)

**Interfaces:**
- Consumes: `manifest.apply`, `manifest.load`, `manifest.save`, `manifest.drift`, `manifest.MANIFEST` (Task 2).
- Produces:
  - `claude_code(contest: Path, root: Path = HOUSE_ROOT) -> dict[str, str]` — relpath → text for `.claude/agents/*.md`, `.claude/skills/house-*/SKILL.md`, `CLAUDE.md` (with `MARKER`). No vendor skills, no writing.
  - `codex(contest: Path, root: Path = HOUSE_ROOT) -> dict[str, str]` — `{"AGENTS.md": MARKER + "\n" + text}`.
  - `remove_legacy_vendor_copies(contest: Path, root: Path) -> tuple[list[str], list[str]]` — for each dir name in `root/vendor/skills`: if `contest/.claude/skills/<name>` is a tree identical to the vendor copy (reuse `setup._same_tree`) → `shutil.rmtree`, add to `removed`; if present but different → add to `kept`. Returns `(removed, kept)`.
  - `adapt(contest, root=HOUSE_ROOT, detect=doctor.detect) -> AdaptResult` with `@dataclass AdaptResult: written: list[Path]; warnings: list[str]` (warnings: one line per backed-up file `"kept your edit as <rel>.orig"`, per kept stale file, per kept edited vendor copy).
  - `legacy_owned(rel)` used for old contests: `rel in {"AGENTS.md","CLAUDE.md"}` → ours only if the existing file contains `MARKER`; `.claude/agents/<id>.md` with `<id>` in core profiles, or `.claude/skills/<name>/SKILL.md` with `<name>` in `adapters/claude-code/skills` → ours; else foreign.
  - `_commit_owned(contest, paths: list[str])` — stages exactly `paths` (written + removed + `.house/generated.json`, `.house/tools.json`, `.house/playbooks`, `.house/protocols`, `*.orig` backups) minus gitignored ones; `git add -A -- <paths>` so deletions are staged.

- [ ] **Step 1: Update/add failing tests.**
  - `tests/test_vendor_skills.py`: replace `test_adapt_installs_vendored_skills` with `test_adapt_does_not_copy_vendored_skills` (after `adapt`, no `.claude/skills/<vendor>` exists) and `test_legacy_vendor_copies_cleaned`: copy `root/vendor/skills/frontend-design` into `c/.claude/skills/frontend-design`, copy `webapp-testing` and append a line to its `SKILL.md` → after `adapt`, `frontend-design` gone, `webapp-testing` kept, result warnings mention `webapp-testing`.
  - `tests/test_adapt.py::ManifestAdaptTest`:
    - `test_teammate_agent_survives_and_is_not_committed`: write `.claude/agents/teammate.md` (untracked) → after `adapt` file exists and `git status --porcelain` contains `?? .claude/agents/teammate.md`.
    - `test_manifest_written_and_committed`: `.house/generated.json` lists `AGENTS.md`, `CLAUDE.md`, `.claude/agents/coder.md`; `git ls-files` contains `.house/generated.json`.
    - `test_hand_edit_kept_as_orig`: `adapt`, edit `.claude/agents/coder.md`, `adapt` again → `.claude/agents/coder.md.orig` has the edit, warning list non-empty.
    - `test_stale_agent_removed`: `adapt` with `ALL` (coder-codex generated) then with `NONE` → `coder-codex.md` gone and its deletion committed (`git ls-files` lacks it).
    - `test_legacy_contest_no_spurious_orig`: simulate old adapt (write `AGENTS.md` with `MARKER`, `.claude/agents/coder.md` without manifest) → `adapt` creates no `.orig` files.
  - Existing tests that iterate `adapters.adapt(...)` return value: update to `.written`.

- [ ] **Step 2: Run** `python3 -m unittest tests.test_adapt tests.test_vendor_skills tests.test_review_fixes -v` — expected: new tests FAIL, old ones still PASS.

- [ ] **Step 3: Implement** the Interfaces in `adapters.py`; `cmd_adapt` prints `wrote <rel>` for each written path and `! <warning>` for each warning; `cmd_doctor <name>` prints `drift: <rel> (<state>)` lines from `manifest.drift` (or `generated files: in sync`).

- [ ] **Step 4: Run** the full suite `python3 -m unittest discover -s tests -v` — expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add houselib/adapters.py houselib/cli.py tests/test_adapt.py tests/test_vendor_skills.py tests/test_review_fixes.py
git commit -m "fix(adapt): manifest-owned writes, never delete teammates' agents; stop copying vendor skills"
```

### Task 4: verify on the real Grok Bot contest, docs

**Files:**
- Modify: `docs/context-budget.md`, `README.md` („Skille zewnętrzne”, „Aktualizacja systemu”, „Gdy coś nie działa”), `docs/SPEC.md` §3 tree comment for `.claude/skills`.

- [ ] **Step 1:** `bin/house adapt grok-bot-wro-2026` (allowed by the owner: the contest is over). Expected: vendor copies removed (identical), no `.orig` created, commit `chore(house): adapt` in the contest repo, `.house/generated.json` present.
- [ ] **Step 2:** `bin/house doctor --context grok-bot-wro-2026` → append under `## After (vendor skills not copied)` in `docs/context-budget.md`, plus one line with the difference in tokens.
- [ ] **Step 3:** `bin/house doctor grok-bot-wro-2026` prints `generated files: in sync`.
- [ ] **Step 4:** README: vendor skills are read by agents from `$HOUSE_ROOT/vendor/skills/…` (not installed into contests); `adapt` keeps hand edits of generated files as `*.orig` and never touches files it did not generate; `doctor --context` row in the CLI list. SPEC §3: `.claude/skills` = house-* only.
- [ ] **Step 5: Commit**

```bash
git add README.md docs/SPEC.md docs/context-budget.md
git commit -m "docs: context budget before/after, manifest-owned adapt"
```
