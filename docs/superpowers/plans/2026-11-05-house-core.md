# House Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A usable AI Software House kit: `house` CLI + neutral core (agents, playbooks, protocols, `.house/` templates) + Claude Code and Codex adapters, dry-run tested before 17:00 on 2026-11-05.

**Architecture:** Python stdlib-only CLI (`bin/house` → `houselib/`) scaffolds independent contest repos under `contests/<name>/`, copies the neutral core into `.house/`, detects tools, and runs adapters that translate `core/agents/*.md` into `.claude/agents/`, `.claude/skills/`, `AGENTS.md`, `CLAUDE.md`. All agent knowledge is Markdown/YAML in `core/`; code only moves files and detects tools.

**Tech Stack:** Python 3.14 stdlib (argparse, pathlib, subprocess, shutil, json, re, unittest), git, Markdown/YAML content.

**Spec:** `docs/SPEC.md` (scope: §9 "Teraz (przed Grok Botem)").

## Global Constraints

- **Stdlib only.** No PyYAML, no pytest. Tests: `python3 -m unittest discover -s tests -v` from repo root.
- YAML in this repo is parsed by our own minimal parser (`houselib/fm.py`): flat `key: value`, inline lists `[a, b]`, `null`/`true`/`false`/ints. Content files must stay within that subset; `spec.yaml` is read only by agents, never by code.
- Machine-detected tools go to `.house/tools.json` (deviation from SPEC §4 `tools` field: spec.yaml keeps `tools: see tools.json`).
- Contest names: words/numbers separated by single hyphens, lowercase: `^[a-z0-9]+(-[a-z0-9]+)*$` (e.g. `grok-bot-wro-2026`). No leading/trailing/double hyphens.
- Contest repos get local git identity copied from the system repo's local config (`Wojciech Fiołka` / `56364007+Kabanosk@users.noreply.github.com`); never touch global config.
- Agent instructions, reports, public artifacts: English. Human-facing files (`PROGRESS.md`, `QUESTIONS.md`, `DECISIONS.md`, briefing, README): Polish.
- Model tiers allowed: `opus | sonnet | haiku | codex | local`.
- Defaults (SPEC §4): `mode: copilot`, `max_parallel_agents: 3`, `ideas_per_round: 5`, `stagnation_n: 3`, `auto_submit: true`, `explore_share: 0.25`, `endgame_share: 0.20`, `deploy: true`.
- Every path in code is resolved from `HOUSE_ROOT = Path(__file__).resolve().parents[1]` (works from any cwd).

## Review Focus

1. `house new` on an existing name → exits non-zero, touches nothing (test in Task 4).
2. Machine without codex / ollama models / nvidia-smi → doctor reports false/empty, never raises; adapter falls back (`coder-codex` omitted, `ideator` → opus, `summarizer` → haiku-only) (tests in Tasks 2, 5).
3. `house export` with a tracked `.env`, `.house/`, or a file containing an API-key-shaped string → blocked with the offending paths listed (test in Task 7).
4. Re-running `house adapt` overwrites generated files but never modifies anything in `.house/` except `tools.json` (test in Task 5).
5. Invalid contest name (`Grok Bot`, `../x`, `A`) → rejected before any filesystem change (test in Task 4).

---

### Task 1: CLI skeleton, frontmatter parser, contests registry

**Files:**
- Create: `bin/house`, `houselib/__init__.py`, `houselib/fm.py`, `houselib/registry.py`, `houselib/cli.py`
- Test: `tests/test_fm.py`, `tests/test_registry.py`

**Interfaces:**
- Produces:
  - `houselib.HOUSE_ROOT: Path`
  - `fm.parse_frontmatter(text: str) -> tuple[dict, str]` (raises `ValueError` if no `---` block)
  - `fm.parse_scalar(s: str) -> str | int | bool | None | list[str]`
  - `registry.load(path: Path) -> dict[str, str]` (missing file → `{}`; lines `name: url`, `#` comments, `url` may be empty string)
  - `registry.add(path: Path, name: str, url: str) -> None` (keeps sorted order)
  - `cli.main(argv: list[str]) -> int` with subcommands `doctor`, `new`, `adapt`, `export`, `sync` registered (later tasks fill them)

- [ ] **Step 1: Write failing tests**

```python
def test_parse_frontmatter_scalars_and_lists():
    meta, body = parse_frontmatter("---\nid: coder\nmax_turns: 40\nfallback: null\ntools: [read, edit]\n---\nBody\n")
    assert meta == {"id": "coder", "max_turns": 40, "fallback": None, "tools": ["read", "edit"]}
    assert body == "Body\n"

def test_parse_frontmatter_missing_block_raises():  # assertRaises(ValueError)

def test_registry_roundtrip(tmp):
    add(p, "grok-wro", "git@github.com:Kabanosk/grok-wro.git"); add(p, "a-first", "")
    assert load(p) == {"a-first": "", "grok-wro": "git@github.com:Kabanosk/grok-wro.git"}
    assert p.read_text().splitlines()[0].startswith("a-first")
```

- [ ] **Step 2:** Run `python3 -m unittest discover -s tests -v` → FAIL (ImportError).
- [ ] **Step 3:** Implement. `bin/house` is `#!/usr/bin/env python3`, inserts `HOUSE_ROOT` into `sys.path`, calls `sys.exit(cli.main(sys.argv[1:]))`; `chmod +x`. Values after `: ` are split on the first `: ` only (URLs contain `:`).
- [ ] **Step 4:** Tests PASS; `bin/house --help` lists the five subcommands.
- [ ] **Step 5:** Commit `feat: house CLI skeleton, frontmatter parser, contests registry`.

### Task 2: `house doctor`

**Files:**
- Create: `houselib/doctor.py`; Modify: `houselib/cli.py`
- Test: `tests/test_doctor.py`

**Interfaces:**
- Consumes: `cli.main`
- Produces: `doctor.detect(which=shutil.which, run=_run) -> dict` where `run(cmd: list[str]) -> str | None` (None on error/timeout 5 s). Result keys exactly:
  `{"claude": bool, "codex": bool, "cursor": bool, "grok_bot": bool, "ollama": {"installed": bool, "models": list[str]}, "gpu": {"name": str, "vram_mib": int} | None}`
  - `grok_bot`: `which("grok-bot")` or `run(["dpkg-query","-W","grok-bot"])` non-empty.
  - ollama models: first column of `ollama list` minus header.
  - gpu: `nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits`, first line.
- `doctor.write(contest_dir: Path, tools: dict) -> Path` writes `.house/tools.json` (indent 2, plus `"checked_at"` ISO timestamp).
- CLI: `house doctor [name]` prints a human summary (✓/✗ per tool, models, GPU); with `name` also writes tools.json.

- [ ] **Step 1: Failing tests:** all-missing fakes (`which` → None, `run` → None) give all False, `models == []`, `gpu is None`; fake `ollama list` output `"NAME ID SIZE MODIFIED\nqwen3:8b abc 5.2GB 1 day ago\n"` → `["qwen3:8b"]`; fake nvidia-smi `"NVIDIA RTX 500 Ada Generation Laptop GPU, 4094"` → `{"name": "...", "vram_mib": 4094}`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS; `bin/house doctor` on this machine shows claude ✓, cursor ✓, codex ✗, ollama installed with no models, RTX 500 Ada 4094 MiB.
- [ ] **Step 5:** Commit `feat: house doctor tool detection`.

### Task 3: Core content — `.house/` templates, protocols, playbooks

**Files:**
- Create: `core/templates/house/{spec.yaml,DECISIONS.md,QUESTIONS.md,PROGRESS.md,ideas.md,submissions.csv,state.json,reports/TEMPLATE.md,tasks/TEMPLATE.md}`
- Create: `core/protocols/{questions,briefing,decisions,submissions,stagnation,export}.md`
- Create: `core/playbooks/{lifecycle,build,optimize}.md`
- Test: `tests/test_core_content.py`

**Interfaces:**
- Produces (used by Tasks 4–6 and by agents):
  - `spec.yaml` keys (top level, each present, unfilled ones `null` with a `# who fills it` comment): `contest, kind, goal, scoring, timeline, rules, mode, limits, submissions, deploy, languages, tools, team`; defaults from Global Constraints; `tools: see tools.json`.
  - `QUESTIONS.md` entry format: `## Q-001 [open] <title>` then `- **Kontekst:** … - **Opcje:** … - **Rekomendacja:** … - **Blokuje:** T### | nic - **Odpowiedź:** —`; answered → `[answered YYYY-MM-DD]` and filled `Odpowiedź`.
  - `DECISIONS.md` entry: `## D-001 <title>` + `- **Decyzja / Dlaczego / Kto (człowiek|judge|coordinator) / Data**`.
  - Task file `tasks/T###-slug.md` frontmatter: `id, title, status (todo|doing|blocked|done|dropped), owner, branch, depends_on: [..], capabilities: [..], acceptance: [..]`.
  - `submissions.csv` header: `time,submission_id,commit,kind,local_score,leaderboard_score,note` (`kind` = `exploit|explore|probe`).
  - `state.json`: `{"last_briefing": null}`.
- Content requirements (from SPEC §6–§7, write them, do not paraphrase away):
  - `briefing.md`: briefing BEFORE questions; no length cap; sections: co się zmieniło (git log + tasks + reports since `last_briefing`), najlepszy wynik i trend, decyzje podjęte samodzielnie, liczba otwartych pytań; then ask open questions one at a time, record answers, update `last_briefing`.
  - `questions.md`: what counts as a hard decision (idea choice, direction change, cost > ~20% remaining time/limit, endgame submissions, anything irreversible not allowed by spec); `copilot` vs `autopilot` table.
  - `optimize.md`: correlation rule incl. the ban on "local tests are pointless" without numbers; `explore_share`; stagnation → ideator on a different model + `ideas.md` check; endgame rule; leaderboard probing allowed unless rules forbid.
  - `build.md`: golden path first, early integration, feature freeze from `timeline`, fallback demo, rubric-as-scorer before each milestone, `house export` for submission.
  - `lifecycle.md`: the 6 stages of SPEC §6 with which agent owns each and which file each writes; parallelism capped by `limits.max_parallel_agents`; worktree agents write to `$HOUSE_DIR` (absolute path to main checkout `.house/`).
  - Language: playbooks/protocols in English (agent-facing); template files for humans in Polish.

- [ ] **Step 1: Failing test:** every listed file exists; `spec.yaml` contains each of the 13 keys as `^<key>:` line; `briefing.md` mentions `last_briefing`; `optimize.md` contains `explore_share`, `stagnation_n`, `endgame_share`.
- [ ] **Step 2:** FAIL. **Step 3:** Write content. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat: core templates, protocols and playbooks`.

### Task 4: Agent profiles + `house new`

**Files:**
- Create: `core/agents/{coordinator,researcher,ideator,coder,coder-codex,judge,pitch,summarizer}.md`, `houselib/scaffold.py`; Modify: `houselib/cli.py`
- Test: `tests/test_agents.py`, `tests/test_new.py`

**Interfaces:**
- Consumes: `fm.parse_frontmatter`, `registry.add`, `doctor.detect`, `doctor.write`
- Produces:
  - Profiles with frontmatter keys exactly: `id, model_tier, fallback, description, tools, inputs, outputs, acceptance, max_turns`; tiers/fallbacks per SPEC §5 (`ideator: codex` fallback `opus`; `coder-codex: codex` fallback `coder`; `summarizer: local` fallback `haiku`; `researcher: haiku`; `coordinator`, `judge: opus`; `coder`, `pitch: sonnet`). Bodies: English instructions; each says which `.house/` files it reads/writes and that it reports in English but writes `PROGRESS.md` entries in Polish. `summarizer` body: try `ollama run <model>` from `tools.json`, keep raw file paths in output. `ideator` body: must consult `ideas.md`, must propose approaches unlike tried ones.
  - `scaffold.new_contest(name: str, repo_url: str | None, root: Path = HOUSE_ROOT, detect=doctor.detect) -> Path`: validate name; `contests/<name>` must not exist; clone if `repo_url` else `git init -b main`; set local identity from `git -C root config user.name/email`; copy `core/templates/house` → `.house/`, `core/playbooks` → `.house/playbooks/`, `core/protocols` → `.house/protocols/`; write tools.json; `registry.add(root/"contests.yaml", name, repo_url or "")`; commit `chore: scaffold house` in the contest repo. Raises `ValueError` (bad name) / `FileExistsError`.
  - CLI: `house new <name> [--repo URL]` prints path and next step (`house adapt <name>`).

- [ ] **Step 1: Failing tests:** all 8 profiles parse, have exactly the keys, tiers in allowed set, `ideator.model_tier != coder.model_tier`; `new_contest` in a temp root (copy of `core/` + a git-initialised root with local identity) creates `.house/spec.yaml`, `.house/playbooks/build.md`, `.house/tools.json`, registry entry, one commit authored with the root's identity; second call → `FileExistsError` and nothing changed; names `"Grok Bot"`, `"../x"`, `""`, `"-x"`, `"a--b"`, `"x-"`, `"Grok-Bot"` → `ValueError` and `contests/` untouched.
- [ ] **Step 2:** FAIL. **Step 3:** Implement (tests pass a fake `detect`). **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat: agent profiles and house new`.

### Task 5: Claude Code adapter + `house adapt`

**Files:**
- Create: `houselib/adapters.py`, `adapters/claude-code/skills/{house-start,house-status,house-ideate,house-plan,house-run,house-judge,house-ship,house-submit}/SKILL.md`, `adapters/codex/AGENTS.md.tmpl`, `bin/codex-task`; Modify: `houselib/cli.py`
- Test: `tests/test_adapt.py`

**Interfaces:**
- Consumes: profiles (Task 4), `tools.json` (Task 2), playbooks (Task 3)
- Produces:
  - `adapters.resolve_tier(profile: dict, tools: dict) -> str | None`: `codex` → `"codex"` if `tools["codex"]` else fallback resolved recursively (`coder` → that profile's tier); `local` → `"local"` if `tools["ollama"]["models"]` else fallback; others unchanged. `None` means "omit agent".
  - `adapters.claude_code(contest: Path, root: Path = HOUSE_ROOT) -> list[Path]`: for each profile except `coordinator` write `.claude/agents/<id>.md` with frontmatter `name, description, model` (`opus|sonnet|haiku`; `codex` tier → `haiku` agent whose body runs `bin/codex-task`; `local` → `haiku` agent whose body tries ollama first) + body; `coder-codex` omitted when resolved tier ≠ `codex`; copy skills to `.claude/skills/`; write `CLAUDE.md` = `@AGENTS.md` + one line telling Claude to use superpowers skills (worktrees, TDD, verification) where the playbooks call for them.
  - `adapters.codex(contest: Path, root) -> Path`: render `AGENTS.md` from template with `{contest_name}`, `{house_dir}` (absolute), roster table (id → resolved tier); coordinator profile body embedded as the main-session instructions.
  - `bin/codex-task <task-file> <worktree>`: runs `codex exec --full-auto -C <worktree> "<prompt>"` where prompt = task file content + "Write your report to $HOUSE_DIR/reports/<task-id>.md"; `--dry-run` prints the command; exits 3 with a message if `codex` not on PATH.
  - Skills: each `SKILL.md` has frontmatter `name`, `description`, `disable-model-invocation: true` except `house-status` (also triggers on "jak tam"); body = which playbook/protocol to read and follow (thin wrappers, no duplicated rules).
  - CLI: `house adapt <name>` re-runs doctor, then both adapters; prints generated files.

- [ ] **Step 1: Failing tests:** with tools all-false: no `coder-codex.md`, `ideator.md` has `model: opus`, `summarizer.md` has `model: haiku`; with codex true + models `["qwen3:8b"]`: `coder-codex.md` exists; every generated agent has `model` in `{opus,sonnet,haiku}`; `CLAUDE.md` first line `@AGENTS.md`; `AGENTS.md` contains absolute `.house` path; snapshot hash of `.house/` (excluding `tools.json`) unchanged across two `adapt` runs; `bin/codex-task --dry-run` prints a line starting `codex exec --full-auto -C`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement + write skills. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat: claude-code and codex adapters`.

### Task 6: `house sync`

**Files:** Modify `houselib/registry.py`, `houselib/cli.py`; Test: `tests/test_sync.py`

**Interfaces:**
- Produces: `registry.sync(root: Path) -> list[str]` clones every entry with a non-empty URL whose `contests/<name>` is missing, sets local identity, returns cloned names; entries with empty URL are reported as "local only" and skipped.

- [ ] **Step 1: Failing test:** bare repo in tmp as URL → `sync` clones it and returns `["x"]`; second run returns `[]`.
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** Commit `feat: house sync`.

### Task 7: `house export`

**Files:** Create `houselib/export.py`; Modify `houselib/cli.py`; Test: `tests/test_export.py`

**Interfaces:**
- Produces: `export.export(contest: Path, dest: Path, force: bool = False) -> list[Path]`:
  - Source set = `git ls-files` (tracked only, so untracked junk never leaks).
  - Excluded prefixes: `.house/`, `.claude/`, `.cursor/`, `.codex/`, and files `AGENTS.md`, `CLAUDE.md`, `.env*` except `.env.example`.
  - Secret scan on remaining text files, regexes: `sk-[A-Za-z0-9_-]{20,}`, `xai-[A-Za-z0-9]{20,}`, `ghp_[A-Za-z0-9]{30,}`, `AKIA[0-9A-Z]{16}`, `-----BEGIN [A-Z ]*PRIVATE KEY-----`. Hit → raise `ExportBlocked(paths)` unless `force`.
  - `dest` must not exist or be empty; result is a fresh `git init` with one commit (local identity).
- CLI: `house export <name> <dest> [--force]`; prints exported count and excluded paths.

- [ ] **Step 1: Failing tests:** tracked `.house/x.md`, `CLAUDE.md`, `.env` absent from dest, `.env.example` present; tracked `app.py` with `xai-` + 24 chars → `ExportBlocked` naming `app.py`; untracked `notes.txt` not exported; non-empty dest → `FileExistsError`.
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** Commit `feat: house export with secret scan`.

### Task 8: README + end-to-end dry run

**Files:** Create `README.md` (Polish: what it is, install = clone + `bin/house doctor`, quickstart `new → adapt → claude → /house-start`, contest lifecycle, link to SPEC). Modify nothing else unless the dry run finds bugs (fix them with a test first).

- [ ] **Step 1:** Full suite PASS.
- [ ] **Step 2:** `bin/house new dryrun-grok` → `bin/house adapt dryrun-grok`; inspect generated `.claude/agents`, skills, `AGENTS.md`.
- [ ] **Step 3:** In `contests/dryrun-grok`, run `claude -p "/house-start" ` with the Grok Bot judging criteria pasted as input; expected: spec.yaml `scoring` filled with the 5 criteria marked `proposed: true`, rules check result, questions in `QUESTIONS.md`. Then `claude -p "jak tam"` → briefing first, then questions.
- [ ] **Step 4:** `bin/house export dryrun-grok <scratchpad>/export` → no `.house/`.
- [ ] **Step 5:** Record findings in `docs/dry-run-2026-11-05.md` (Polish), delete `contests/dryrun-grok` only after the human confirms, commit `docs: README and dry-run notes`.
