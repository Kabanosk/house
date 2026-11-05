# Playbook: build contests (hackathon MVP)

Goal: a working, demoable product + pitch, scored by humans/AI judges against `spec.scoring.rubric`.

## Principles
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
Runs from a clean clone with documented commands? Golden path works without the author? Survives bad input /
API failure with a visible message? Demo works without network?
