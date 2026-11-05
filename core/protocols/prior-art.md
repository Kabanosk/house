# Protocol: prior-art check (before any idea is chosen)

Why: on 2026-11-05 (grok-bot-wro-2026) ideas were chosen on novelty and impact scores nobody checked; the judge
found the closest existing products only after the build had started. An idea is never chosen without a card.

Owner: `researcher` (cheap model, background, fresh context), one card per idea, in parallel with other ideas.

## Sources (check all that apply to `spec.kind`)
1. **YC company directory:** `$HOUSE_ROOT/bin/house yc <what the idea does, plain words>`. Try 2–3 phrasings
   (the problem, the user, the mechanism). Daily snapshot of YC's own index; only publicly launched companies.
2. **YC Requests for Startups** (https://www.ycombinator.com/rfs): is this problem one YC asks founders to solve?
3. **Devpost / past winners** of this event and similar hackathons (web search, plus `spec.contest` links): has a
   jury already rewarded this, or is it "another demo of the same"?
4. **Product Hunt** (web search): shipped products doing the same.
Optimize contests: replace 2–4 with papers, Kaggle/competition write-ups and public solutions for the same metric.

## Card: `$HOUSE_DIR/reports/prior-art-<idea id>.md` (English)
- **Closest existing** (max 5): name, source, year/batch, status, link, one line on how close it is.
- **What is genuinely different** in our idea, in one or two sentences a judge would accept.
- **Market signal:** evidence the problem is real (funded companies, RFS entry, paying users), or "none found".
- **Risk for the rubric:** novelty risk (a judge who knows X will say "this exists") and how to defend it in the pitch.

A close match is **risk, not a veto**: juries often do not know the prior art, and a known problem with a sharper
twist can win. The judge decides with the card in hand; the human sees the cards when picking (copilot).
Add the closest match to the idea's row in `ideas.md` (column "Najbliższe istniejące").
