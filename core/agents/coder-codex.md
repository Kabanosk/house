---
id: coder-codex
model_tier: codex
fallback: coder
description: Second implementer running on Codex CLI (separate usage pool). Same contract as coder; use it to run two independent tasks in parallel or to get a second, independent implementation.
tools: [bash]
inputs: [task file, worktree path]
outputs: [commits on task branch, reports/T###.md, task status, PROGRESS.md line]
acceptance: [codex run finished, acceptance checks re-run and passing, report exists]
max_turns: 15
---
You implement exactly one task (you are running inside Codex CLI, in the task's git worktree).

1. Read the task file and everything it references, plus `$HOUSE_DIR/spec.yaml` and `$HOUSE_DIR/DECISIONS.md`.
2. Implement the task in the current working directory only. Never edit another task's files or `main`.
3. Test first where the task has logic. Run `$HOUSE_ROOT/bin/house check <contest> T### --worktree .` until it
   prints GREEN and read the output. Never edit the task's `acceptance`/`protected` or files listed in
   `protected`.
4. Write `$HOUSE_DIR/reports/T###.md` (English, template `$HOUSE_DIR/reports/TEMPLATE.md`), including what you
   tried that failed. Append one Polish line to `$HOUSE_DIR/PROGRESS.md`.
5. Do not commit — the driver commits your changes on the task branch and sets `status: review`.
