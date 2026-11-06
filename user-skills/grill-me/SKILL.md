---
name: grill-me
description: Relentless interview, in rounds, that turns a loose idea into decisions the user owns. User-invoked only via /grill-me.
disable-model-invocation: true
---

# grill-me

_Based on grill-me/grilling by Matt Pocock (MIT), https://github.com/mattpocock/skills, adapted to the owner's preferences._

Interview the user relentlessly until you reach a shared understanding of the idea in `$ARGUMENTS`
(if empty, ask for it in one sentence and wait). Speak the user's language.

## The design tree

Map the idea as a **design tree**: every decision branches into the decisions that hang off it.
The **frontier** is every decision whose prerequisites are already settled: what you can ask _now_ without
guessing at answers you haven't heard yet. Work the tree in **rounds**: ask the whole frontier, wait for the
answers, recompute the frontier, ask the next round. A question that depends on another question still open in
this round belongs to a later round.

## Facts are your job

Decisions belong to the user; facts belong to you. When a frontier question needs a fact from the environment
(files, repo, tools, web), dispatch a subagent with a fresh context window to find it; never ask the user for
something you can look up. Don't block on it: only the questions downstream of a running lookup wait; ask the
rest of the frontier now.

## Round layout

Every round, in this order:

1. **Co zrozumiałem**: a few lines playing back what the last answers settled.
2. **Niejasne, opisz lepiej**: anything in the user's answers that was vague, contradictory, or that you might
   be misreading. Quote it and say what is unclear. Omit the section when nothing is.
3. **Pytania**: numbered, grouped by theme. **Open, descriptive questions** the user answers in their own words.
   No recommendations: the thinking stays on the user's side. Mark questions that decide a lot with ⚠️ and ask
   for a fuller answer on those.

## `NEXT`

When the user answers `NEXT` (for the whole round or for specific question numbers), they cannot answer in their
own words. In the next round, re-ask those items in **closed form**: yes/no or options a/b/c, each with your
recommended answer and a one-line reason, worded so that "yes" (or the first option) accepts the recommendation.
Only `NEXT` items get recommendations; everything else stays open.

## Steering

There is no question limit; the user steers with words ("kończ", "wystarczy", "skup się na X"). Push back when
the user only agrees ("ok", "zgoda") without adding anything: ask which choice they would actually defend.
"Nie wiem" is a valid answer: record it as an open item. When a question cannot be settled by talking (how
something should look or feel), call it _ungrillable_, suggest a throwaway prototype, and move on.

## Ending

The session ends when the frontier is empty: every branch visited, nothing silently assumed. Then output:

1. **Decyzje**: one line each, with the user's reason.
2. **Otwarte**: "nie wiem" answers and ungrillable questions (with the prototype suggestion).
3. **Poza zakresem**: what was explicitly excluded.

Then **do nothing** (no files, no code, no plans) until the user confirms this summary is the shared
understanding. Write no files during the session.
