# Protocol: questions to the human

The human is present rarely and decides only what matters. Everything else is decided by the judge or the
coordinator and logged in `DECISIONS.md`.

## What is a hard decision (→ ask)
- Choosing which idea to build / which direction to pursue.
- Changing direction (dropping the current approach, pivot after stagnation).
- Anything costing more than ~20% of the remaining time or usage limit.
- Submissions in the endgame (last `submissions.endgame_share` of the contest time).
- Anything irreversible or public that `spec.yaml` does not explicitly allow (`deploy`, `auto_submit`).
- Ambiguity in the rules or the task statement that changes what we build.

Everything else (library choice, task split, retries, which test to write) is NOT a question.

## Mode
| Situation | `copilot` | `autopilot` |
|---|---|---|
| Idea choice | ask | judge picks, log D-### |
| Direction change | ask | coordinator decides, log D-### |
| Big cost (>~20%) | ask | decide, log D-### |
| Submission (normal) | per `auto_submit` | per `auto_submit` |
| Submission (endgame) | ask | ask |
| Rules ambiguity | ask | ask |

## How to ask
1. Append to `.house/QUESTIONS.md` (Polish) using the template: `## Q-### [open] <title>`, context, options,
   recommendation with one-line reason, what it blocks.
2. If it blocks a task, set that task `status: blocked`; keep working on anything not blocked.
3. Never wait idle for an answer; never decide a question yourself after asking it.
4. Answers arrive during the briefing (see `briefing.md`): mark `[answered YYYY-MM-DD]`, fill **Odpowiedź**,
   write the resulting `D-###`, unblock tasks.
