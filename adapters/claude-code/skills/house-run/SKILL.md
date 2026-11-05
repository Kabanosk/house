---
name: house-run
description: Execute ready tasks from .house/tasks - dispatch coder / coder-codex / researcher in parallel worktrees, merge finished work early.
disable-model-invocation: true
---
Run **stage 4: Execution** of `$HOUSE_DIR/playbooks/lifecycle.md`.

1. Ready = `status: todo` with all `depends_on` done. Take at most `limits.max_parallel_agents`.
2. For each: create a worktree on `task/T###-slug` (superpowers:using-git-worktrees), then dispatch the agent
   (`coder`, or `coder-codex` to use the separate Codex pool, `researcher` for research tasks) with the task path,
   the worktree path and `$HOUSE_DIR`. Independent tasks in parallel (superpowers:dispatching-parallel-agents).
3. As each finishes: check its report and acceptance evidence, merge into `main`, run the app/tests on `main`.
4. Loop until nothing is ready. Blocked tasks → questions per `protocols/questions.md`; keep going on the rest.
5. After each merge wave, if `spec.kind` is build and a milestone is near, run `/house-judge`.
