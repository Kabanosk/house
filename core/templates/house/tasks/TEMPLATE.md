---
id: T000
title: <short imperative title>
status: template
owner: null
branch: null
depends_on: []
capabilities: []
acceptance:
  - id: AC-1
    run: "<command run in the task's worktree; exit 0 = pass>"
    timeout: 600
  - id: AC-2
    run: "<optional: command whose stdout is checked>"
    expect: "<regex, or a threshold like: score >= 0.71>"
  - id: AC-3
    manual: <only what no command can check; the judge records evidence>
protected:
  - <test or scorer files the worker must not change>
---
<!-- Copy to tasks/T###-slug.md and set status: todo. Files with status: template are ignored.
Status: todo → doing → review (worker stops here) → merged (house merge passed) → done (manual criteria too);
blocked when stuck or reverted. Optional `review: always` (scorer, submission pipeline, contracts): house merge
then needs an independent review of the task's head (`house review <contest> T###`). Criteria are frozen by `house check <contest> T### --freeze` at the end of
planning; changing them later needs `--refreeze --reason` (logged as D-###). -->
## Objective
One paragraph: what done looks like for this task.

## Context
Files, contracts, decisions (D-###) the worker needs. Inputs it may assume exist.

## Out of scope
What this task must not touch.
