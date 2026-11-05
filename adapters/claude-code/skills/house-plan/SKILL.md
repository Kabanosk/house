---
name: house-plan
description: Turn the chosen idea into a task DAG in .house/tasks with acceptance checks, respecting max_parallel_agents.
disable-model-invocation: true
---
Run **stage 3: Plan** of `$HOUSE_DIR/playbooks/lifecycle.md`.

- One file per task from `$HOUSE_DIR/tasks/TEMPLATE.md`, ids `T001…`, checkable `acceptance`, `depends_on`.
- Contracts (types / API / file formats) first when tasks meet at an interface.
- Sequential work → one worker. Parallel only for independent tasks, at most `limits.max_parallel_agents`.
- Log the chosen decomposition as D-### if it encodes a real choice. Commit `.house/`. Summarize the DAG in Polish.
