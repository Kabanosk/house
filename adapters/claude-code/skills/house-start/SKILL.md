---
name: house-start
description: Contest intake - fill .house/spec.yaml from the pasted task statement, rules and judging criteria, check the rules, raise questions for gaps.
disable-model-invocation: true
---
You are the coordinator (see AGENTS.md). Run **stage 1: Intake** of `$HOUSE_DIR/playbooks/lifecycle.md`.

Input: `$ARGUMENTS` plus anything the human pasted (task statement, rules, judging criteria, timeline, links).
If nothing was given, ask for it in one short message and stop.

1. Fill `$HOUSE_DIR/spec.yaml` with what the human gave. Keep their values verbatim.
2. Dispatch the `researcher` agent for the rules check (links/text from the input) and any missing facts.
   Also list every external API key the contest or likely ideas need (e.g. sponsor APIs) and verify each key works
   with one cheap real call (see `build.md` principle 0); a missing or failing key is a question for the human now.
   The researcher also fills `spec.deliverables` from the rules (video and its length, live demo, slides, repo,
   starter code, AI APIs, external data, cloud deploy, submission format). Never assume: an item the rules do
   not settle stays null and goes into the intake question.
3. Fill the remaining gaps yourself, marking each `# proposed`. Raise ONE question in `QUESTIONS.md` listing all
   proposed values for approval (format: `protocols/questions.md`).
4. Commit `.house/` (`chore(house): intake`). Append a Polish line to `PROGRESS.md`.
5. Reply in Polish: what is filled, what is proposed, rules verdict, the next step (`/house-ideate`).
