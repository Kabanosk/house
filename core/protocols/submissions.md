# Protocol: submissions (optimize contests)

## Before submitting
1. Read `spec.submissions` and count used slots in `.house/submissions.csv`.
2. Decide the `kind`:
   - `exploit` — expected to beat the current best.
   - `explore` — new / untested approach, or one that looks weak locally. Up to `explore_share` of slots.
   - `probe` — designed to learn about the hidden judge (only if `spec.rules` does not forbid probing).
3. Endgame (last `endgame_share` of contest time): ask the human before EVERY submission (see `questions.md`).
4. Otherwise: if `auto_submit: true` and you have a reason to expect improvement (or it's a planned explore/probe),
   submit. If `auto_submit: false`, ask.

## After submitting
- Append a row: `time,submission_id,commit,kind,local_score,leaderboard_score,note`. Fill `leaderboard_score`
  as soon as it is known. The commit hash must reproduce the submission exactly.
- Update the local↔leaderboard agreement (see `playbooks/optimize.md`) and mention it in the next report.
