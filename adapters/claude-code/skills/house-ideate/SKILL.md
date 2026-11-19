---
name: house-ideate
description: Ideation round - ideator proposes ideas_per_round ideas, researcher writes a prior-art card per idea (YC, past winners), judge scores them against the contest scoring, then the human (copilot) or judge (autopilot) picks.
disable-model-invocation: true
---
Run **stage 2: Ideation** of `$HOUSE_DIR/playbooks/lifecycle.md` (and `build.md` / `optimize.md` for `spec.kind`).
`$ARGUMENTS` = optional focus or constraints from the human.

1. Earlier decisions: run `$HOUSE_ROOT/bin/house decisions --by human --exclude <this contest>` and pick the entries
   relevant to this contest (hardware, time, policy, approaches ruled out). `copilot`: present them to the human, in
   Polish, as constraints to confirm or drop; log only what they confirm (D-###). `autopilot`: do not wait; list the
   relevant ones in the briefing as constraints, and pass them to the ideator marked as unconfirmed. Never adopt one
   silently and never copy one into `DECISIONS.md` without the human's answer. Give the confirmed ones to the
   ideator.
2. Dispatch `ideator` with: the focus, the confirmed constraints, `$HOUSE_DIR` path, and "propose
   `limits.ideas_per_round` ideas".
3. For every idea, dispatch `researcher` (in parallel, background) to write its prior-art card per
   `$HOUSE_DIR/protocols/prior-art.md` (YC directory via `house yc`, YC RFS, past hackathon winners, Product Hunt).
   An idea without a card is not eligible.
4. Dispatch `judge` to score them against `spec.scoring` and feasibility in the time left, using the cards as
   evidence for innovation and impact.
5. `copilot`: present the ranked list, with each idea's closest existing product and the defence, in Polish with the judge's recommendation and ask the human to pick
   (also log it as Q-###). `autopilot`: take the top idea, log D-###, then follow `.claude/skills/house-plan/SKILL.md`.
