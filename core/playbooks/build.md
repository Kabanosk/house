# Playbook: build contests (hackathon MVP)

Goal: a working, demoable product + pitch, scored by humans/AI judges against `spec.scoring.rubric`.

## Principles
0. **External APIs and keys work for the jury, not only for us.** (grok-bot-wro-2026: we missed the final because
   the xAI key failed when the jury ran the demo.) At intake, list every external API and key the idea needs and
   verify each with one real, cheap call; log it as D-###. Never assume the jury has our key or our `.env`:
   - the deployed demo has the key set server-side in the hosting environment and is checked from a clean browser
     on another device and network, by the first milestone, not at the end;
   - without any key the app falls back to a clearly labelled demo mode (seeded/recorded data) instead of failing;
   - the README says exactly how to run it with their own key and without one; a key-check command exists;
   - check quota, rate limits and expiry of trial/hackathon keys before the demo; have a spare key ready.
1. **Rubric is the scorer.** Before every milestone the judge scores the current build against each rubric
   criterion (1–5, with evidence) and names the cheapest way to gain a point. Work follows the biggest gain.
2. **Golden path first.** Define the demo in 3 sentences (who, does what, sees what). Build that end-to-end,
   ugly, before any polish or second feature.
3. **Integrate early.** First milestone = golden path running end-to-end with mocks where needed. Merge to
   `main` continuously; never schedule integration for the end.
4. **Feature freeze** at the `timeline` milestone marked as freeze (default: 30 min before end). After that:
   bug fixes, demo, pitch only.
5. **Fallback demo.** Record a demo video and keep a no-network mode (`?demo=1` or seeded data) before freeze.
6. **No auth, no DB, no payments** unless the idea is about them. Hardcode, in-memory, seeded data.

## Flow
1. Intake + rules check (lifecycle §1). Note: is AI judging first? Then write README/pitch for an AI reader too.
2. Ideation: `ideas_per_round` ideas, judge scores each on the rubric; also "can we demo it in the time left?".
3. Plan: contract task first (types / API), then parallel tasks (e.g. backend agent logic, frontend, deploy),
   capped by `max_parallel_agents`.
4. Milestones (adapt to `timeline`): golden path end-to-end → judge round → "wow" feature → deploy + test on a
   phone → judge round → freeze → pitch.
5. Deploy: if `deploy: true`, put it on a public URL (Vercel / Cloudflare tunnel) and test from another device.
6. Pitch agent prepares in `spec.languages.public`: 3-minute script (problem → demo → how it works → impact),
   slides, submission README (what it is, how to run, architecture, which rubric criteria it hits and how),
   demo video plan.
7. Submission: `house export <contest> <dest>` → push the export, never the contest repo.

## Judge checklist for build (in addition to the rubric)
Runs like the jury will run it: the deployed URL from a clean browser profile, and a fresh clone without our
`.env` (fallback mode visible, no crash)? Runs from a clean clone with documented commands? Golden path works without the author? Survives bad input /
API failure with a visible message? Demo works without network?
