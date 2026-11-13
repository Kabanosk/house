# Playbook: optimize contests (score, leaderboard)

Goal: maximize the contest metric on a hidden judge, using a limited number of submissions.

## 1. Local scorer first
- Before any clever idea, build a local scorer that reproduces the official metric exactly
  (`spec.scoring.local_scorer`). Validate it against the examples / first submission.
- Keep every candidate reproducible: branch + commit + config + seed. Name artifacts by score.

## 1a. Experiments (`house exp`)
Every local attempt goes through the experiment loop, so the record is kept by code, not by memory:
1. `$HOUSE_ROOT/bin/house exp new <contest> --from best --idea I-### --note "<one change>"` prints
   `E-### <worktree>`. Work only in that worktree.
2. Make ONE change (an idea from `ideas.md`, a fix, a parameter). Large outputs (weights, predictions) must be
   gitignored; data outside git goes to `exp.shared` in the spec.
3. `$HOUSE_ROOT/bin/house exp run <contest> E-### --budget 5m -- <train/predict command>`: the command runs with
   the budget, then `scoring.local_scorer`; strictly better than the parent → `keep`, otherwise `revert`
   (worktree removed, commit kept as `refs/exp/E-###`). Exit 0 = keep.
4. A `crash` or `timeout` is debugged as a child of it: `house exp new <contest> --from E-###`. Improve the best
   with `--from best`. When the change is only the command line (a sweep), one step:
   `house exp run <contest> --from best -- <command>`.
5. `house exp status <contest>` shows the best, the tree and the stagnation counter. Never change the scorer in an
   experiment (that is a `crash`, "tampered"); a scorer fix goes to the contest checkout, after which earlier
   scores are not compared.
6. `house exp prune <contest> --keep 3` frees disk (worktrees of weaker keeps; refs stay).

## 2. Local ↔ leaderboard agreement
- Every submission logs both scores in `.house/submissions.csv` (see `protocols/submissions.md`).
- After 3+ submissions, report agreement as a number (rank correlation of local vs leaderboard, and mean
  difference) in every report that discusses scores.
- **Forbidden:** claiming "local tests are meaningless / don't show anything" without those numbers.
  Weak agreement is a problem to fix, not a conclusion: build a better proxy (synthetic data generator
  matching the judge's distribution, harder holdout split, probes of the judge if rules allow).
- A weak local score does NOT kill a new approach: approaches never tested on the leaderboard go to the
  explore budget below.

## 3. Explore vs exploit
- `submissions.explore_share` of slots is reserved for `explore` submissions: new families of approaches,
  ones that look weak locally, or ones the local proxy cannot judge.
- Leaderboard probing (`probe` kind) is allowed unless `spec.rules` forbids it.

## 4. Stagnation
- `limits.stagnation_n` scored experiments without improving the best (`house exp status` prints the arrow
  `→ protocols/stagnation.md`) → follow `protocols/stagnation.md`:
  ideator on a different model, radically different directions, checked against `ideas.md`.

## 5. Endgame
- During the last `submissions.endgame_share` of contest time: ask before every submission. Prepare a ranked
  list of candidates with expected gain and risk so the human can decide quickly.
- Make sure the final selected submission (if the contest asks to choose) is decided explicitly, not by default.

## 6. Compute
- GPU priority: training/simulation over local LLMs. Check `tools.json` for VRAM before planning runs.
- Long runs: write the exact command, expected duration and output path into the task file; report on finish.
