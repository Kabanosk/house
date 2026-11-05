---
name: house-ideate
description: Ideation round - ideator proposes ideas_per_round ideas, judge scores them against the contest scoring, then the human (copilot) or judge (autopilot) picks.
disable-model-invocation: true
---
Run **stage 2: Ideation** of `$HOUSE_DIR/playbooks/lifecycle.md` (and `build.md` / `optimize.md` for `spec.kind`).
`$ARGUMENTS` = optional focus or constraints from the human.

1. Dispatch `ideator` with: the focus, `$HOUSE_DIR` path, and "propose `limits.ideas_per_round` ideas".
2. Dispatch `judge` to score them against `spec.scoring` and feasibility in the time left.
3. `copilot`: present the ranked list in Polish with the judge's recommendation and ask the human to pick
   (also log it as Q-###). `autopilot`: take the top idea, log D-###, continue to `/house-plan`.
