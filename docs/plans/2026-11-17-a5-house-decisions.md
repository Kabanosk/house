# A5: `house decisions` — decision log across contests — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One command lists decisions from the `DECISIONS.md` of every contest in `contests.yaml`, one line per entry, filterable by contest, author, date and text, so a human decision from an earlier contest ("we don't train big models on this hardware") is found instead of remembered.

**Architecture:** Reuses `houselib/decisions.py` (A2a: entries only inside `## D-###` blocks, `Zastępuje:` honoured, `by` = human/agent/unknown, `line`, `fields`). New `houselib/decisionlog.py` iterates contests from the registry, applies filters and renders lines; `cli.cmd_decisions` wires it; `/house-ideate` and `/house-start` get one step to consult human decisions of earlier contests as constraints to confirm.

**Tech Stack:** Python 3.11+ stdlib, `unittest`.

**Accepted:** Wojtek, 2026-11-17 (Signal, question 9).

**Spec:** `docs/specs/2026-11-16-agno-adoptions-design.md` §11 (A5); order §5 step 5. Already done in A2a: the parser, the protocol field `Zastępuje: D-###`, ids before `(` only.

## Global Constraints

- Stdlib only; synthetic contests in tests; full suite green before commit; English docstrings.
- Line format (spec): `<contest> D-### <date> <who>: <title> — <path>:<line>`; `--full` adds the entry text.
- `--by human` = `Kto` starting with `człowiek`; `--by agent` = judge or coordinator (incl. entries `merge._review_gate` writes); missing `Kto` = `unknown` (shown, not matched by either filter).
- Superseded entries hidden by default; `--all` shows them with `[zastąpiona przez D-###]`.
- Plain field filtering only; full-text search belongs to another project.

## Review Focus

- A contest in `contests.yaml` whose directory is missing or has no `DECISIONS.md` → skipped with one note line, no crash.
- The template header line `- **Kto:** człowiek | judge | coordinator` is never an entry (parser guarantee — pin with a CLI test).
- `--since` compares the entry's `Data` field (`YYYY-MM-DD[ HH:MM]`); an unparsable date is kept and marked `?` only when no `--since` is given.
- Same `D-###` in two contests → both shown, distinguished by contest.
- `--grep` is a literal, case-insensitive substring by default (not a regex), over title and fields.

---

### Task 1: log across contests and the CLI

**Files:** `houselib/decisionlog.py` (`entries(root, contest=None) -> list[tuple[str, Path, Decision]]`, `select(entries, by=None, since=None, grep=None, all=False)`, `render(entries, full=False) -> str`), `houselib/cli.py` (`cmd_decisions`: `house decisions [--contest C] [--by human|agent] [--since YYYY-MM-DD] [--grep TEXT] [--all] [--full]`), `tests/test_decisionlog.py`.
- [ ] Tests (spec acceptance) on two synthetic contests: each filter; missing file; old entries without `Kto` (unknown); template header not an entry; superseded hidden and shown with `--all` and the marker; a `_review_gate` entry is `agent`; line format exact; `--full`.
- [ ] Commit `feat(decisions): house decisions across contests (A5)`.

### Task 2: skills consult earlier human decisions; docs

**Files:** `adapters/claude-code/skills/house-ideate/SKILL.md`, `adapters/claude-code/skills/house-start/SKILL.md` (one step each: run `$HOUSE_ROOT/bin/house decisions --by human` and treat relevant entries as constraints to confirm with the human, never to adopt silently), `docs/modules/contest-workflow.md` (Polish, a short paragraph), `tests/test_core_content.py` (both skills mention `house decisions --by human`).
- [ ] Failing content test → edit → green → commit `docs(skills): consult earlier human decisions (A5)`.
