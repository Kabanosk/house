# Signal notifications, answers from the phone and decision deadlines (P3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every open question reaches Wojtek (Note to Self) and the contest's team group as one Signal message; a quote-reply (or `Q-007: B`) from anyone is recorded as the answer (first wins) with a D-### and a confirmation; critical questions with a deadline fall back to the judge's default when nobody answers.

**Architecture:** `houselib/signal.py` wraps `signal-cli` (send / receive / listGroups) behind an injectable runner. `houselib/notify.py` formats and sends, recording every sent message in `~/.local/share/house/sent.jsonl`. `houselib/inbox.py` pulls messages, keeps only Note to Self + configured groups, matches answers to questions and writes `QUESTIONS.md` / `DECISIONS.md`. `houselib/questions.py` parses and edits `QUESTIONS.md` entries and applies deadlines. CLI: `house notify`, `house ask`, `house inbox`, `house deadlines`. Hooks: PostToolUse on `QUESTIONS.md` sends new questions; SessionStart pulls the inbox.

**Tech Stack:** Python 3.11+ stdlib, `signal-cli` 0.14.x native (JSON output), `unittest`.

**Spec:** `docs/superpowers/specs/2026-11-12-system-hardening-design.md` §7 (P3, Signal decisions of 2026-11-13) and §7a (decision levels, deadlines).

## Global Constraints

- Config outside every repo: `~/.config/house/notify.env` (`HOUSE_NOTIFY_CHANNEL=signal`, `HOUSE_SIGNAL_ACCOUNT=+48…`). No config or no `signal-cli` → every send/receive is a no-op returning success (never breaks a run).
- Per contest in `spec.yaml`: `notify.alias` (lowercase letters/digits) and `notify.signal_group` (group name). Prefix of every message: `🤖 [<alias or contest name>]`.
- Send to Note to Self with `--note-to-self --notify-self`; to the group with `-g <groupId>` (group resolved by name via `listGroups`, cached in `~/.local/share/house/groups.json`; unknown or ambiguous name → warning, Note to Self only).
- Receive: `signal-cli -a <acct> -o json receive --timeout <n>`; keep only Note to Self (`syncMessage.sentMessage` with `destinationNumber` = own number and no `groupInfo`) and messages in configured groups (`dataMessage.groupInfo.groupId` or `syncMessage.sentMessage.groupInfo.groupId`); everything else is dropped without being written anywhere. Kept messages are appended to `~/.local/share/house/inbox.jsonl`.
- Answer matching, in order: `quote.id` = timestamp of a message in `sent.jsonl` → (contest, Q); else `Q-007: B` / `Q7 B` / `Q-007 B` (contest by alias prefix `fr Q-007: B`, or the only running contest in that chat with Q-007 open); ambiguous → reply asking for the alias. Messages without a question reference are ignored. The first answer (lowest Signal timestamp) wins; later ones get "already decided: B (Kuba)".
- Sender name = `envelope.sourceName`, else `sourceNumber`; for Note to Self = "Wojtek" (owner, from `spec.team` if present, else "właściciel").
- Recording an answer: header `## Q-### [open] …` → `[answered YYYY-MM-DD]`, `- **Odpowiedź:** <text> (<sender>, Signal)`, new `D-###` (Decyzja = answer, Dlaczego = "odpowiedź na Q-### przez Signal", Kto = `człowiek (<sender>)`), tasks listed in `- **Blokuje:**` with `status: blocked` → `todo`; commit `.house/`. Confirmation `✓ [alias] Q-007: B (<sender>)` to the chat the answer came from.
- Decision levels (§7a) in `QUESTIONS.md` entries: `- **Poziom:** mały|średni|krytyczny|zawsze człowiek`, `- **Termin:** YYYY-MM-DD HH:MM` (local time), `- **Domyślnie:** <option>`. `house deadlines`: past `Termin` + `Domyślnie` + level `krytyczny` → record the default as the answer with `Kto: judge (domyślnie, brak odpowiedzi do HH:MM)` and notify; half-way to the deadline → one reminder. `zawsze człowiek` never defaults.

## Review Focus

- A quote-reply to a message house did not send (or to an old message) → ignored, no crash.
- Two contests in one group both have Q-003 open, answer `Q3 A` without alias or quote → asks for the alias, records nothing.
- Answer arrives for a contest whose `timeline.end` has passed → ignored.
- `signal-cli` prints warnings on stderr / non-JSON lines on stdout → those lines are skipped.
- QUESTIONS.md entry written by an agent with slightly different spacing/casing (`**odpowiedź:**`, missing `Blokuje`) → still recorded.

---

### Task 1: `houselib/signal.py`
**Produces:** `Signal(account: str, binary: str, run=subprocess.run)` with `send(text, group_id=None) -> int | None` (timestamp printed by signal-cli; Note to Self when `group_id` is None), `receive(timeout: int = 1) -> list[dict]` (one parsed JSON object per stdout line; non-JSON lines skipped), `list_groups() -> list[dict]` (`id`, `name`); `from_config(path=~/.config/house/notify.env) -> Signal | None`.
- [ ] Tests with a fake `run` capturing argv and returning canned stdout: argv for self vs group send, timestamp parsing, receive skips junk lines, missing config → None, missing binary → None. FAIL → implement → PASS → commit.

### Task 2: `houselib/questions.py`
**Produces:** `@dataclass Question: id, status, title, level, deadline: datetime | None, default, blocks: list[str], answer, raw`; `parse(text) -> list[Question]`; `answer(house, qid, text, who, via) -> str | None` (D id, or None if already answered) doing everything in "Recording an answer"; `due(house, now) -> tuple[list[Question], list[Question]]` (defaults to apply, reminders to send; reminder state in `state.json` `reminded`).
- [ ] Tests on the real `core/templates/house/QUESTIONS.md` format plus loose spacing/casing; answer twice → second returns None; blocked task unblocked; D numbering; due() at half-time and after deadline; `zawsze człowiek` never due. FAIL → implement → PASS → commit.

### Task 3: `houselib/notify.py` + `house notify` / `house ask`
**Produces:** `prefix(contest) -> str`; `send(contest, text, question: str | None = None, sig=None) -> list[int]` (Note to Self + group, both recorded in `sent.jsonl` as `{"ts", "contest", "question", "chat"}`); `question_message(q: Question, alias) -> str` (format from spec §7: title line, options, recommendation, default + deadline, `↩ odpowiedz cytatem: …`); `ask_new(contest) -> list[str]` (open questions not yet in `state.json` `sent_questions` → send, record). CLI: `house notify <contest> "<text>"`, `house ask <contest> [Q-###]`.
- [ ] Tests with a fake Signal: both chats get the message, records written (HOME redirected to a temp dir), group resolved by name and cached, unknown group → Note to Self only + warning, ask_new sends each open question once. FAIL → implement → PASS → commit.

### Task 4: `houselib/inbox.py` + `house inbox` / `house deadlines`
**Produces:** `pull(sig, contests) -> list[Message]` (filter + append to inbox.jsonl); `@dataclass Message: ts, chat, sender, text, quote_ts`; `route(msg, contests, sent) -> tuple[contest, qid, answer_text] | str` (str = reply asking for alias, or None to ignore); `process(...)` applying answers (first wins by ts, confirmations, "already decided" replies) and recording handled timestamps per contest in `state.json` `inbox_handled_ts`. `house inbox [contest]` (all running contests when omitted; running = `timeline.end` empty or in the future), `house deadlines [contest]`.
- [ ] Tests with canned signal-cli JSON envelopes for: Note to Self quote-reply, group dataMessage from a teammate, own group message (syncMessage + groupInfo), unrelated DM (dropped, not stored), quote to unknown ts, ambiguous Q without alias, alias prefix, ended contest, second answer. FAIL → implement → PASS → commit.

### Task 5: hooks, doctor, spec template, protocols
**Files:** `houselib/hook.py` (PostToolUse on edits of `QUESTIONS.md` → `ask_new`; SessionStart → `inbox` + `deadlines` with a 1 s receive, fail open), `houselib/adapters.py` (PostToolUse entry), `houselib/doctor.py` (signal-cli present + account linked, alias collisions among running contests, last receive older than 7 days → warning), `core/templates/house/spec.yaml` (`notify:` section), `core/templates/house/QUESTIONS.md` (Poziom / Termin / Domyślnie lines), `core/protocols/questions.md` (levels table from §7a, deadlines, `house notify` for średni decisions: "zdecydowałem, możesz cofnąć"), `adapters/claude-code/skills/house-start/SKILL.md` (ask for alias + group), README.
- [ ] Content tests (templates parse, protocol mentions levels and `house notify`), hook tests for PostToolUse, doctor tests with fake tools. FAIL → implement → PASS → commit.

### Task 6: live check with Wojtek's Signal
- [ ] `house ask` a test question `Q-900 Test house: odpowiedz cytatem „ok”` in a scratch contest copy to Note to Self; when Wojtek has quote-replied (asked via Signal, non-blocking), `house inbox` records it and the confirmation arrives. Record the observed JSON shape of a quote-reply in the spec. Group format: verified on the first real group answer (logged as a follow-up if not available now).
