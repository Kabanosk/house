---
name: house-retro
description: Retrospective on a contest or session - finds what to improve in the house system (core/ and adapters/) and writes a severity-ordered report. Run after a contest, a hard day, or a session that went wrong.
disable-model-invocation: true
---
You are suggesting improvements to the agents' **environment** (the house system), not to the contest code.
Adapted from `retro` by Matt Pocock (MIT, https://github.com/mattpocock/skills).

1. If the `writing-for-agents` skill is available, call the Skill tool with `writing-for-agents` for the style
   guide (any suggested wording change to a playbook, agent or skill must follow it).
2. Read the primary sources for the period `$ARGUMENTS` names (default: the whole contest):
   `$HOUSE_DIR/DECISIONS.md`, `QUESTIONS.md`, `PROGRESS.md`, `reports/`, `submissions.csv`, `ideas.md`, task files,
   `git log --all`, and the Claude Code session logs for this repo (`~/.claude/projects/<this repo path with / as ->/`).
   Look for: time lost, questions that blocked work, agents circling one idea, failed or rejected commands,
   wrong merges, scores that dropped, things the human had to fix by hand.
3. Find candidates in these categories:
   - **Navigation**: agents slow to find a file, a fact, or the right playbook section. Fix: a pointer.
   - **Automated checks**: a mistake a test, linter, scorer check or pre-commit hook could have caught; a contest
     repo with no guardrail at all is itself a finding. Prefer building the check over writing a rule.
   - **Coding standards** for the reviewer (judge): a judgement-call mistake the judge missed. Mechanical ones get a
     check instead of a rule.
   - **AGENTS.md / CLAUDE.md bloat**: steering text that belongs in a playbook, protocol or check.
   - **Tool economy**: expensive calls (huge logs pasted, repeated reads, Opus doing haiku work, Codex timeouts).
   - **No-ops**: instructions in core/ that did not change any agent's behaviour.
   - **Information access**: something an agent needed and could not see (logs, leaderboard, docs, data).
4. Write `$HOUSE_DIR/reports/retro-<YYYY-MM-DD>.md` (English): candidates ordered by severity, each with the
   evidence (file/commit/session), the proposed change to a specific file in `$HOUSE_ROOT/core/` or
   `$HOUSE_ROOT/adapters/`, and the expected effect. Append one Polish line to `PROGRESS.md`.
5. Present the top candidates to the human in Polish. Do not edit `core/` or `adapters/` without the human's
   approval; approved changes go through the house repo's tests (`python3 -m unittest discover -s tests`).
