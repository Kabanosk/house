# Protocol: demo and submission checklist (build contests)

Run before every release candidate and again before submitting. Each line is checked by doing it, not by
reading code; record the result per line in the judge report. Anything red blocks the submission.

- [ ] The deployed URL opens in an **incognito** browser without our credentials (unless credentials are given to
      the jury explicitly), on another device and network.
- [ ] The golden path in `$HOUSE_DIR/demo-flow.md` works end to end from a clean session.
- [ ] A seed/demo account or demo data exists; nothing depends on our local state.
- [ ] Every external API key is set in the production environment, has enough quota, and is not about to expire;
      a spare key is ready (`playbooks/build.md` principle 0).
- [ ] Every AI call has a **timeout** and a visible failure message; loading states always end.
- [ ] With no key at all, the app falls back to a clearly labelled demo mode.
- [ ] The browser **console** has no fatal errors.
- [ ] Checked at the projector/laptop resolution and at phone width.
- [ ] A Playwright happy path passes against the **deployed URL** (vendor skill `webapp-testing`).
- [ ] Backup screenshots and a **backup recording** of the working flow exist.
- [ ] The submission text and README match what is actually built (no promised features that are missing).
- [ ] The repo/build follows the event rules and licences (`spec.deliverables`, `spec.rules`); public copy made with
      `house export`.
- [ ] The **video** (if required) is uploaded early, its visibility is checked after processing, and it fits
      `spec.deliverables.video_max_seconds`.
- [ ] The final submission is opened from **another machine** or account **before the deadline**.
