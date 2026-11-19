# K3 (code part): `house sources` — contest documents outside the model — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rules, task statements and API docs enter the contest as files in `.house/sources/` copied byte for byte by a command (not retyped by the model), with an index (sum, size, lines, origin) and a table of contents with line numbers, so agents read them by range and pass paths, not content.

**Architecture:** `houselib/sources.py` adds files (`add`), lists them with headings and warnings (`listing`), and keeps `.house/sources/index.json`; `cli.cmd_sources` wires `house sources add <contest> <file|-> [--name N] [--replace]` and `house sources <contest>`; `/house-start` asks for files / `house sources add` and writes `spec.yaml` references as path + line range. The reading rules (K3 documentation part, step 1) switch from `grep -n '^#'` to `house sources` for the table of contents.

**Tech Stack:** Python 3.11+ stdlib, `unittest`.

**Accepted:** Wojtek, 2026-11-17 (Signal, question 9).

**Spec:** `docs/specs/2026-11-16-short-context-design.md` §9 (K3) and §13 (left to the implementer: size threshold, `index.json` format, file names); order: step 5 (Wojtek, 2026-11-17: the code part goes next to K0, A5, A4).

## Decisions left to the implementer by the spec (made here)

- `index.json`: `{"files": [{"name", "sha256", "bytes", "chars", "lines", "added" (ISO), "origin" ("stdin" or the source file name, never an absolute path)}]}`, sorted by name.
- File names: `[a-z0-9][a-z0-9._-]{0,80}`; `--name` defaults to the source file's base name lower-cased with other characters → `-`; stdin requires `--name`.
- Size threshold in the rules stays ~40 KB (step 1); `house sources` warns on lines longer than 2 000 characters.

## Global Constraints

- Stdlib only; synthetic files in tests; full suite green before commit; English docstrings.
- Copy byte for byte (`shutil.copyfile` / stdin bytes); re-adding a name refuses without `--replace`; `--replace` updates the index entry.
- `.house/sources/` and `index.json` are tracked (spec §3 table); the command commits nothing itself (the coordinator commits `.house/` as usual) — or commits only those two paths; pick the house convention used by similar commands (`questions._commit` style, register/checks excluded).
- Listing: size in characters, ~tokens (chars/4), lines, Markdown headings with line numbers (`#`…`######`, outside code fences), a warning for lines > 2 000 characters and for a sum that differs from `index.json`; binary files (NUL byte in the first 8 KB) listed as binary and skipped for headings.
- URL fetching is out of scope (spec §14).

## Review Focus

- A source file that is a symlink → refused (copy the target? no: refuse with a message) — never copy a link into the tracked tree.
- A huge file (500K characters regulation) → `add` streams it, `listing` stays fast (line-by-line) and prints at most ~50 headings with a "and K more" line.
- An edited source (sum differs) → the listing says the line ranges in `spec.yaml` may have moved.
- A name that would escape the directory (`../x`, `/etc/passwd`) → rejected by the name rule.
- Empty `.house/sources/` → `no sources yet` and the `house sources add` hint, exit 0.

---

### Task 1: `sources.add` and the index

**Files:** `houselib/sources.py` (`add(contest, src: Path | None, data: bytes | None, name: str | None, replace=False) -> dict` entry), `tests/test_sources.py`.
- [ ] Tests (spec acceptance): file and stdin copied byte for byte; entry with sha256, bytes, chars, lines, date, origin; same name refused without `--replace`; `--replace` updates; bad names rejected; symlink source refused.
- [ ] Commit `feat(sources): add contest documents byte for byte with an index (K3)`.

### Task 2: listing

**Files:** `houselib/sources.py` (`listing(contest) -> str`), tests.
- [ ] Tests (spec acceptance): sizes, headings with line numbers (fences ignored), long-line warning, changed sum, empty directory, binary file skipped, heading cap with "and K more".
- [ ] Commit `feat(sources): table of contents and checks (K3)`.

### Task 3: CLI, `/house-start`, rules, docs

**Files:** `houselib/cli.py` (`house sources add …`, `house sources <contest>`), `adapters/claude-code/skills/house-start/SKILL.md` (ask for files or `house sources add`; pasted text ≤ ~5 000 characters only via `house sources add <contest> - --name …`; `spec.yaml` and tasks reference path + line range), the K3 "Long inputs" block in `core/agents/{coordinator,coder,researcher,judge}.md` (table of contents: `house sources <contest>`, else `grep -n '^#' <file>`; keep the block identical in all four — the test pins it), `docs/modules/contest-workflow.md` (Polish), README table row; tests (`tests/test_core_content.py` updated).
- [ ] Tests: CLI end to end (add from file and stdin, listing); `/house-start` mentions `house sources add`; the reading block still identical across profiles and mentions `house sources`.
- [ ] Commit `feat(sources): house sources CLI, /house-start and reading rules (K3)`.

### Task 4 (later, after A3): eval case

- [ ] When `house eval` exists (A3): case `sources-read-by-range` — a question about a ~500K-character rules file in `.house/sources/`; `forbid` Read without `limit`, Read with `allow_large`, and pasting the document into an Agent brief (spec §9 acceptance). Lands with A3's case set.
