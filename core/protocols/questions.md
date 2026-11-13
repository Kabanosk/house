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

## A run never waits
- A run never waits for a human: it ends or moves to the next item. The human's later answer starts new work.
  Blocked on a question? Park that branch (`status: blocked`), take the next ready item, and when nothing is
  ready, research new directions (see the playbook for `spec.kind`).
- **Push right**: defer the human checkpoint as late as it will go. Do all the work that does not depend on the
  answer first, then ask once, with a tight decision-ready brief: what was produced, why, the options, and links
  to the assets (reports, branches, scores). The human reads a brief, not a draft.

## Decision levels and deadlines

| Level | Examples | Who decides |
|---|---|---|
| mały | library, task split, retry | coordinator, log D-### |
| średni | ideas with similar scores, pivot after stagnation in `autopilot`, cost > ~20% | judge, log D-###, then `$HOUSE_ROOT/bin/house notify <contest> "Zdecydowałem: … (D-###), możesz cofnąć"` |
| krytyczny | idea choice in `copilot`, endgame submissions, the final submission, anything irreversible/public not allowed by spec | human, **with** `Termin` + `Domyślnie` (judge's recommendation) |
| zawsze człowiek | rules ambiguity, organisers' consent | human, never defaulted |

- `copilot`/`autopilot` only moves the line between średni and krytyczny (table in "Mode").
- `Termin` = the latest sensible moment (push right); endgame example: final submission = contest end − 10 min.
  `house deadlines` (run by the night loop and at session start) reminds once at half-time and, when the
  deadline passes with no answer, records `Domyślnie` as `D-###` (Kto: judge, domyślnie) and notifies.
- Every new open question is sent to Signal automatically (hook after editing `QUESTIONS.md`; by hand:
  `house ask <contest>`). People answer with a quote-reply; `house inbox` records the first answer.

## How to ask
1. Append to `.house/QUESTIONS.md` (Polish) using the template: `## Q-### [open] <title>`, context, options,
   recommendation with one-line reason, what it blocks.
2. If it blocks a task, set that task `status: blocked`; keep working on anything not blocked.
3. Never wait idle for an answer; never decide a question yourself after asking it.
4. Answers arrive during the briefing (see `briefing.md`): mark `[answered YYYY-MM-DD]`, fill **Odpowiedź**,
   write the resulting `D-###`, unblock tasks.
