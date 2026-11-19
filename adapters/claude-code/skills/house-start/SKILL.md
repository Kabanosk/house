---
name: house-start
description: Contest intake - fill .house/spec.yaml from the pasted task statement, rules and judging criteria, check the rules, raise questions for gaps.
disable-model-invocation: true
---
You are the coordinator (see AGENTS.md). Run **stage 1: Intake** of `$HOUSE_DIR/playbooks/lifecycle.md`.

Input: `$ARGUMENTS` plus anything the human pasted (task statement, rules, judging criteria, timeline, links).
If nothing was given, ask for it in one short message and stop.

1. Contest documents first, then the spec:
   - Ask the human for the files (rules, task statement, API docs, judging criteria). Put each into the repo byte
     for byte with `$HOUSE_ROOT/bin/house sources add <contest> <file>` (contest name = the AGENTS.md title; add
     `--name <name>` for a clearer file name).
   - Text pasted into the chat goes in only if it is at most ~5 000 characters, through a quoted heredoc so the
     shell changes nothing. Pick the end marker `HOUSE_SRC_<8 random hex>` fresh for every paste (for example
     `HOUSE_SRC_3fa9c01b`) and check that the pasted text does not contain it (pick another if it does):
         $HOUSE_ROOT/bin/house sources add <contest> - --name rules.md <<'HOUSE_SRC_<8 random hex>'
         ...the pasted text...
         HOUSE_SRC_<8 random hex>
     The heredoc adds one final newline, so the stored file is the text plus that newline. For longer text ask the
     human for a file instead.
   - Then run `$HOUSE_ROOT/bin/house sources <contest>` and read the documents by line range from that table of
     contents. Do not commit here; `.house/` is committed in step 5.
   - Fill `$HOUSE_DIR/spec.yaml` with what the human gave. Keep their values verbatim.
   - In `spec.yaml` and in task files refer to a document by path and line range (for example
     `.house/sources/rules.md:L120-L180`); never retype a document's content.
2. Earlier decisions: run `$HOUSE_ROOT/bin/house decisions --by human --exclude <this contest>` and pick the entries
   relevant to this contest (hardware, time, policy, approaches ruled out). Put them into the intake question
   (step 4) as constraints for the human to confirm or drop. In `autopilot` do not wait: list the relevant ones in
   the briefing instead. Never adopt one silently and never copy one into `DECISIONS.md` without the human's answer.
3. Dispatch the `researcher` agent for the rules check (links/text from the input) and any missing facts.
   Also list every external API key the contest or likely ideas need (e.g. sponsor APIs) and verify each key works
   with one cheap real call (see `build.md` principle 0); a missing or failing key is a question for the human now.
   The researcher also fills `spec.deliverables` from the rules (video and its length, live demo, slides, repo,
   starter code, AI APIs, external data, cloud deploy, submission format). Never assume: an item the rules do
   not settle stays null and goes into the intake question.
4. Notifications: ask the human for a short contest alias (`notify.alias`, e.g. `fr`; must differ from other
   running contests) and the team's Signal group name (`notify.signal_group`, or null = only their Note to Self).
   Fill the remaining gaps yourself, marking each `# proposed`. Raise ONE question in `QUESTIONS.md` listing all
   proposed values (and the earlier decisions from step 2) for approval (format: `protocols/questions.md`).
5. Commit `.house/` (`chore(house): intake`). Append a Polish line to `PROGRESS.md`.
6. Reply in Polish: what is filled, what is proposed, rules verdict, the next step (`/house-ideate`).
