# Playbook: contest lifecycle

All agents follow this. `$HOUSE_DIR` = absolute path to `.house/` in the MAIN checkout of the contest repo
(given in `AGENTS.md`). Agents working in a git worktree read and write `.house/` ONLY via `$HOUSE_DIR`,
never via the worktree's own copy.

At the start of every session: read `spec.yaml`, `DECISIONS.md`, open questions in `QUESTIONS.md`,
`tools.json`, and the playbook for `spec.kind` (`build.md` / `optimize.md`).

| # | Stage | Owner | Writes |
|---|---|---|---|
| 1 | Intake | coordinator (+ researcher for rules) | `spec.yaml` (gaps marked `proposed: true`), `QUESTIONS.md` |
| 2 | Ideation | ideator → judge | `ideas.md`, judge scores in `reports/ideation-<n>.md`, Q-### (copilot) or D-### (autopilot) |
| 3 | Plan | coordinator | `tasks/T###-*.md` (DAG), `PROGRESS.md` |
| 4 | Execution | coder / coder-codex / researcher | code on task branches, `reports/T###.md`, task `status`, `PROGRESS.md` |
| 5 | Evaluation | judge | `reports/judge-<n>.md`, D-### for decisions it may take |
| 6 | Release | pitch (build) / coordinator (optimize) | demo URL, pitch, slides, README, export / `submissions.csv` |

## 1. Intake
- Ask the human to paste: task statement, rules link/text, judging criteria, timeline. Fill what they give.
- Fill remaining gaps yourself from the sources, mark each such field `proposed: true` (or a trailing
  `# proposed` comment), and raise one Q-### listing everything proposed for approval.
- Researcher checks the rules: are AI tools allowed? autonomous agents? external APIs? public repo needed?
  Result goes to `spec.rules`. If forbidden or unclear: stop and ask — never work around rules.
- Run `bin/house doctor <contest>` (from the system repo) if `tools.json` is older than today.

## 2. Ideation
- Ideator proposes `limits.ideas_per_round` ideas, checking `ideas.md` first.
- Researcher writes a prior-art card per idea (`protocols/prior-art.md`). No idea is chosen without its card.
- Judge scores each against `scoring` (rubric for build, expected metric gain + risk for optimize), with a
  one-line justification per criterion.
- `copilot`: Q-### with the ranked list and a recommendation. `autopilot`: take the top one, log D-###.

## 3. Plan
- Break the chosen idea into a DAG of tasks (one file each, template in `tasks/TEMPLATE.md`). Each task has a
  `acceptance` list of commands (`run`, optional `expect`) that a machine can check; `manual` only for what no
  command can check. Tests or scorers a worker must not loosen go to `protected`. Declares `depends_on`.
- Optional `.house/init.sh`: starts the app / runs a smoke test; every check runs it first.
- End of planning: `$HOUSE_ROOT/bin/house check <contest> T### --freeze` for every new task, then commit `.house/`.
  From then on criteria change only with `--refreeze --reason` (logged as D-###).
- Parallelize only independent tasks, never more than `limits.max_parallel_agents` at once.
- If the work is essentially sequential, use ONE worker. More agents on sequential work makes results worse.
- Define contracts (API types, file formats) as their own first task when two tasks meet at an interface.

## 4. Execution
- Each worker: own branch `task/T###-slug` in its own worktree; sets task `status: doing`, `owner`, `branch`.
- Worker runs `house check <contest> T### --worktree <path>` until GREEN, writes `reports/T###.md` (English),
  appends one line to `PROGRESS.md` (Polish), sets `status: review` (or `blocked` + Q-###). Never `done`.
- Coordinator runs `house merge <contest> T###` for every task in `review`, early and often: it checks the branch,
  merges, re-runs the checks of every `merged`/`done` task on main and reverts the merge on regression
  (task → `blocked`). Status after the gate: `merged`, or `done` when manual criteria are also recorded.
  Never `git merge` a task branch by hand. `depends_on` is satisfied by `merged` or `done`.
- Agents communicate through files, not long chats. Pass paths, not pasted logs. Raw data stays in files.

## 5. Evaluation
- Judge evaluates against `scoring` with evidence (commands, screenshots, numbers), never vibes.
- Manual criteria: the judge records each with `house check <contest> T### --manual AC-n --evidence <report>`.
- Judge may decide small things itself (log D-###); hard decisions per `protocols/questions.md`.

## 6. Release
- Build: see `build.md` (deploy, fallback, pitch, slides, README, `house export`).
- Optimize: see `optimize.md` and `protocols/submissions.md`.

## Models (cost discipline)
Opus only for coordinator and judge. Research, search, log reading: haiku. Implementation: sonnet or codex.
Summaries: local model if available, else haiku. A subagent returns a short summary + file paths, not dumps.
