# Protocol: briefing (human returns)

Triggered by `/house-status`, "jak tam", or the human coming back after a break.
Briefing comes BEFORE questions: the human must understand what happened before deciding anything.
Write it in Polish (`spec.languages.team`).

## Steps
1. Read `.house/state.json` → `last_briefing` (null = since the start).
2. Collect everything since then:
   - `git log --since=<last_briefing> --all --oneline` (all branches, incl. worktrees),
   - task files whose status changed (`.house/tasks/`),
   - new reports in `.house/reports/`,
   - new rows in `.house/submissions.csv`,
   - new `D-###` entries in `DECISIONS.md` made by judge/coordinator,
   - open `Q-###` in `QUESTIONS.md`.
3. Write the briefing. **No length cap**: if a lot happened, say a lot; the scale must be visible. Sections:
   - **Co się zmieniło** — grouped by task/idea, with links to reports. What works now that didn't before.
   - **Najlepszy wynik i trend** — build: current judge scores per rubric criterion; optimize: best local and
     leaderboard score, trend over the period, local↔leaderboard agreement.
   - **Decyzje podjęte samodzielnie** — every D-### made without the human, one line each, with the reason.
   - **Problemy / ryzyka** — blocked tasks, failures, budget/time burn.
   - **Pytania** — how many are open, and which block work.
4. Then ask the open questions **one at a time**, most blocking first. Give context again in one sentence each.
5. Record each answer immediately (`QUESTIONS.md` + `DECISIONS.md`), unblock tasks.
6. Set `last_briefing` in `.house/state.json` to now (ISO 8601 with timezone) and commit `.house/`.
