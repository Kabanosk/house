---
name: house-ideate
description: Ideation round - ideator proposes ideas_per_round ideas, researcher writes a prior-art card per idea (YC, past winners), judge scores them against the contest scoring, then the human (copilot) or judge (autopilot) picks.
disable-model-invocation: true
---
Run **stage 2: Ideation** of `$HOUSE_DIR/playbooks/lifecycle.md` (and `build.md` / `optimize.md` for `spec.kind`).
`$ARGUMENTS` = optional focus or constraints from the human.

1. Dispatch `ideator` with: the focus, `$HOUSE_DIR` path, and "propose `limits.ideas_per_round` ideas".
2. For every idea, dispatch `researcher` (in parallel, background) to write its prior-art card per
   `$HOUSE_DIR/protocols/prior-art.md` (YC directory via `house yc`, YC RFS, past hackathon winners, Product Hunt).
   An idea without a card is not eligible.
3. Dispatch `judge` to score them against `spec.scoring` and feasibility in the time left, using the cards as
   evidence for innovation and impact.
4. `copilot`: present the ranked list, with each idea's closest existing product and the defence, in Polish with the judge's recommendation and ask the human to pick
   (also log it as Q-###). `autopilot`: take the top idea, log D-###, then follow `.claude/skills/house-plan/SKILL.md`.
