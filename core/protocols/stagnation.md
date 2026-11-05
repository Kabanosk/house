# Protocol: stagnation

Failure mode this prevents: an agent circling one solution, stuck in a local optimum, refusing to change path.

## Detection
Stagnation = `spec.limits.stagnation_n` consecutive iterations (experiments / submissions / judge rounds)
without improving the best score. The coordinator checks after every iteration.

## Response
1. Stop tuning the current approach.
2. Dispatch the **ideator** — on a different model than the one that produced the current approach
   (codex if available, else opus) — with: the goal, the current best and why it works, `ideas.md`, and the
   instruction to propose `ideas_per_round` **radically different** approaches (different algorithm family,
   problem reframing, data/feature change), not variations of parameters.
3. Every proposal is checked against `ideas.md`; an idea already tried needs a new argument to return.
4. `copilot`: the direction change is a question (Q-###). `autopilot`: coordinator picks, logs D-###.
5. Keep the previous best reproducible (branch + commit) so the pivot is never a loss.
