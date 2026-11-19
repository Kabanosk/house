# K3 (documentation part): selective reading of long inputs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every agent that reads contest documents reads large files by range, never whole, and hands sub-agents paths and line ranges instead of content.

**Architecture:** One identical rule block (five bullets, English) in the profiles of `coordinator`, `coder`, `researcher` and `judge` in `core/agents/`. The generated `AGENTS.md` (Codex and other tools) embeds the coordinator body, so it gets the block without a template change. A content test pins that the block is present and identical everywhere, so the copies cannot drift.

**Tech Stack:** Markdown profiles, `unittest`.

**Spec:** `docs/specs/2026-11-16-short-context-design.md` §9 (K3, the rule paragraph); Agno spec §5 step 1 ("K3 część dokumentowa").

**Not in this plan:** `house sources add` / `house sources` and the `/house-start` change that depends on them (K3 code part), the K3 eval case (needs A3), `house out` (A4). §5 of the Agno spec schedules only the documentation part; the code part goes to step 5, next to K0, A5 and A4 (Wojtek, 2026-11-17, Signal). Until it exists, the rules name `grep -n '^#' <file>` as the table of contents.

## Global Constraints

- Values from the spec, verbatim: file larger than **~40 KB (~10K tokens)** → table of contents or `grep -n` first, then a range around the hits; one read at most **~200 lines or ~16 000 characters**; files in `.house/sources/` always by range: `Read` with `offset`/`limit`, **never `allow_large: true`**; in a brief for a sub-agent: **path and line range, never the document's content**.
- The block adds at most 6 lines per profile (always-loaded context; `house doctor --context` must not grow by more than ~1.5K characters in total).
- English, like the other profiles.
- `python3 -m unittest discover -s tests` green before the commit.

## Review Focus

- A Codex worker (no `Read` tool, no `allow_large`): the block must also say how to read a range in a shell (`sed -n 'A,Bp' <file>`, `grep -n`), or it is useless for Codex.
- A file with one huge line (minified JSON, a CSV without line breaks): "200 lines" is not a limit there; the character cap (~16 000) must be stated next to the line cap.
- Generated `AGENTS.md` of an existing contest after `house adapt`: contains the block (through the coordinator body) — test on the rendered text, not only on `core/agents/coordinator.md`.
- `house doctor --context` drift check after the change: generated files of contests become "out of sync" until re-adapt — expected, mention in the commit message, no code change.
- Researcher reading web pages fetched to disk: the rule applies to local files only; it must not forbid web fetch.

---

### Task 1: the rule block in four profiles, pinned by a test

**Files:**
- Modify: `core/agents/coordinator.md`, `core/agents/coder.md`, `core/agents/researcher.md`, `core/agents/judge.md` (append the block at the end of the body)
- Test: `tests/test_core_content.py`

**Interfaces:**
- Produces: the block, starting with the exact line `Long inputs (contest docs, logs, data):` followed by five bullets covering, in this order: (1) >~40 KB: table of contents (`grep -n '^#' <file>`) or `grep -n` on the question's terms first, then read the range around the hits; (2) one read ≤ ~200 lines or ~16 000 characters (`Read` with `offset`/`limit`; in a shell `sed -n 'A,Bp' <file>`); (3) `.house/sources/` always by range, never `Read` with `allow_large: true`; (4) briefs for sub-agents carry path + line range, never the document's text; (5) long command output: read the tail or `grep -n`, not the whole log. Exact wording is the implementer's within these values.

- [ ] **Step 1: Write the failing test** in `tests/test_core_content.py`:
  ```python
  READING_PROFILES = ["coordinator", "coder", "researcher", "judge"]

  def _reading_block(text):
      start = text.index("Long inputs (contest docs, logs, data):")
      return "\n".join(text[start:].splitlines()[:6])

  def test_reading_rules_identical_in_profiles(self):
      blocks = {p: _reading_block((CORE / "agents" / f"{p}.md").read_text()) for p in READING_PROFILES}
      self.assertEqual(len(set(blocks.values())), 1, blocks)
      block = next(iter(blocks.values()))
      for needle in ("40 KB", "200 lines", "16 000", "allow_large", "offset", "sed -n", "line range"):
          self.assertIn(needle, block)
  ```
  plus `test_reading_rules_in_generated_agents_md`: render `adapters.codex(contest)` for the fixture contest used by `tests/test_adapt.py` and assert `"Long inputs (contest docs, logs, data):"` and `"allow_large"` are in `["AGENTS.md"]`.
- [ ] **Step 2:** `python3 -m unittest tests.test_core_content -v` → the two tests FAIL (`ValueError: substring not found` / assertion).
- [ ] **Step 3:** Add the block to the four profiles.
- [ ] **Step 4:** `python3 -m unittest discover -s tests` → green; `bin/house doctor --context <a contest>` before/after: note the size delta in the commit message.
- [ ] **Step 5: Commit**
  ```bash
  git add core/agents/coordinator.md core/agents/coder.md core/agents/researcher.md core/agents/judge.md tests/test_core_content.py
  git commit -m "docs(agents): read long inputs by range, pass paths not content (K3 rules)"
  ```
