---
name: house-run
description: Execute ready tasks from .house/tasks - dispatch coder / coder-codex / researcher in parallel worktrees, merge finished work early.
disable-model-invocation: true
---
Run **stage 4: Execution** of `$HOUSE_DIR/playbooks/lifecycle.md`.

1. Ready = `status: todo` with every `depends_on` in `merged` or `done` (ignore `status: template`). Take at most `limits.max_parallel_agents`.
2. For each: `git worktree add .worktrees/T###-slug -b task/T###-slug` (pre-approved; never move your own
   session into a worktree), then dispatch the agent
   (`coder`, or `coder-codex` to use the separate Codex pool, `researcher` for research tasks) with the task path,
   the worktree path and `$HOUSE_DIR`. Independent tasks in parallel (superpowers:dispatching-parallel-agents).
3. As each returns `status: review`: `$HOUSE_ROOT/bin/house merge <contest> T###` (contest name = the AGENTS.md
   title). It checks the branch, merges, re-checks every merged task on main, reverts on regression and removes
   the worktree and branch on success. Never `git merge` a task branch by hand. RED → send the failing lines back
   to a worker on the same worktree; reverted → the task is `blocked`, read the regression and re-plan.
   Workers commit with `git -C <worktree> ...` (never `cd <worktree> && git ...`).
4. Loop until nothing is ready. Blocked tasks → questions per `protocols/questions.md`; keep going on the rest.
5. After each merge wave, if `spec.kind` is build and a milestone is near, dispatch the `judge` agent
   (as in `.claude/skills/house-judge/SKILL.md`).
