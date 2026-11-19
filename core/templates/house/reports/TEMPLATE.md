# <Task ID>: <title>

- **Agent / model:** …
- **Branch / commits:** …
- **Date:** YYYY-MM-DD HH:MM

This report is the hand-off for the next agent: a brief for further work, not a history. Fill every section or write
`none`. Anything on disk is cited as `path:line`, not pasted.

## Goal
What is being achieved and why (link the task file).

## Next
What to do next, in order (open problems, follow-ups, Q-### ids). End with both lines:
Next action: <a concrete next command or edit>
Done when: <the completion condition>

## Critical context
Verbatim, never paraphrased: paths, ids (T###, Q-###, E-###), error messages, numbers.
### Observed
Facts you saw, each with its source: the command, or path:line.
### Unverified
Guesses and hypotheses. An entry moves to Observed only after you check it.

## Constraints and human decisions
Rules and human decisions still in force (what must not change, what was approved or refused).

## Dead ends
Abandoned approaches, with numbers. For each: the reason, the number of tries, and "do not repeat". Add the entry
when you abandon the approach. (Also add them to `ideas.md`.)

## Decisions and facts
Decisions made and facts established, with their D-### refs.

## Errors and fixes
Errors met, resolved and open: the message, the fix or the current state.

## Done
Each changed file and whether it is verified, meaning a check passed after the change. Say what was run.

## Result vs acceptance
Each acceptance check from the task: pass / fail.
The log is the evidence; see it with `house evidence <contest> T### --attempt N`.
