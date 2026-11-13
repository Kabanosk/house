---
name: house-plan
description: Turn the chosen idea into a task DAG in .house/tasks with acceptance checks, respecting max_parallel_agents.
disable-model-invocation: true
---
Run **stage 3: Plan** of `$HOUSE_DIR/playbooks/lifecycle.md`.

- One file per task from `$HOUSE_DIR/tasks/TEMPLATE.md`, ids `T001…`, `depends_on`, `acceptance` as commands
  (`run`, optional `expect`); at least one `run` criterion for every task with logic, `manual` only for what no
  command can check. Tests/scorers the worker must not loosen go to `protected`.
- If the project can be started or smoke-tested, write `$HOUSE_DIR/init.sh` (checks run it first).
- Finally freeze every new task: `$HOUSE_ROOT/bin/house check <contest> T### --freeze` (contest name = the
  AGENTS.md title), then commit `.house/`.
- Contracts (types / API / file formats) first when tasks meet at an interface.
- Sequential work → one worker. Parallel only for independent tasks, at most `limits.max_parallel_agents`.
- Log the chosen decomposition as D-### if it encodes a real choice. Commit `.house/`. Summarize the DAG in Polish.
