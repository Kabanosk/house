# Context budget

Output of `house doctor --context <contest>` (estimate: characters / 4). What a Claude Code session in a
contest repo always carries before any work: instruction files, the name + description of every skill the
model can invoke, and subagent descriptions.

## Before (2026-11-13, vendor skills copied into the contest)

```text
Always loaded (files):
    1187  AGENTS.md
     215  CLAUDE.md
Skills visible to the model (22, name + description):
     311  contest: codebase-design, frontend-design, house-status, no-ai-slop, webapp-testing
      78  user: prototype, writing-for-agents
     664  plugin:superpowers: brainstorming, diagnosing-superpowers, dispatching-parallel-agents, executing-plans, finishing-a-development-branch, receiving-code-review, requesting-code-review, subagent-driven-development, systematic-debugging, test-driven-development, using-git-worktrees, using-superpowers, verification-before-completion, writing-plans, writing-skills
Skills hidden from the model (disable-model-invocation): 10
Agents (7, descriptions): 266
Duplicates (same skill name in several places):
  (none)
TOTAL ≈ 2721 tokens (estimate: chars/4)
```

## After (2026-11-13, vendor skills read by path, not copied)

```text
Always loaded (files):
    1197  AGENTS.md
     215  CLAUDE.md
Skills visible to the model (18, name + description):
      62  contest: house-status
      78  user: prototype, writing-for-agents
     664  plugin:superpowers: brainstorming, diagnosing-superpowers, dispatching-parallel-agents, executing-plans, finishing-a-development-branch, receiving-code-review, requesting-code-review, subagent-driven-development, systematic-debugging, test-driven-development, using-git-worktrees, using-superpowers, verification-before-completion, writing-plans, writing-skills
Skills hidden from the model (disable-model-invocation): 10
Agents (7, descriptions): 266
Duplicates (same skill name in several places):
  (none)
TOTAL ≈ 2482 tokens (estimate: chars/4)
```

Difference: −239 tokens (≈ −9%): the descriptions of codebase-design, frontend-design, no-ai-slop and
webapp-testing no longer reach every session. The largest remaining items are AGENTS.md (≈1.2k) and the
superpowers plugin skill list (≈0.7k). Not counted: Claude Code's own system prompt and tool definitions.
