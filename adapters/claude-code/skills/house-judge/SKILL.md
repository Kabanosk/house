---
name: house-judge
description: Judge round - score the current build or candidates against spec.scoring with evidence and name the cheapest next gain.
disable-model-invocation: true
---
Dispatch the `judge` agent with `$HOUSE_DIR`, what to evaluate (`$ARGUMENTS` or "current main"), and the round
number (next free `reports/judge-<n>.md`). Then act on its result: small decisions are logged by the judge;
hard ones are in `QUESTIONS.md`. Report the scores and the recommended next step to the human in Polish.
