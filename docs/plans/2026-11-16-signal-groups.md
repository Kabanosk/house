# Czytanie grup Signala (S1–S6, S8) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One durable store of messages from allowed Signal chats (Note to Self, contest groups, groups listed in
`signal.toml`), written line by line while receiving and on every send, from which agents read with
`house signal read|unread|show` and to which `house inbox`, the session hook, `night` and `doctor` are rewired.

**Architecture:** `signal.py` streams `signal-cli receive` through `Popen` and hands each JSON object to a callback;
`signalparse.py` turns one envelope into one store event (or drops it: privacy filter in memory);
`signalstore.py` owns `~/.local/share/house/signal/` (append with `seq` under `store.lock`, `pull` under
`receive.lock`, state, migration, later `compact`); `signalchats.py` decides which chats are allowed and their
aliases (self + running contests + `signal.toml`); `signalread.py` folds reactions/edits/deletes onto messages,
filters and formats; `signalcli.py` holds the `house signal …` subcommands. `Signal.send` logs every outgoing
message itself. `inbox.py` becomes a reader of the store.

**Tech Stack:** Python 3.11+ stdlib only (`tomllib`, `fcntl`, `subprocess.Popen`, `threading.Timer`), `signal-cli`
0.14.x JSON output, `unittest` (`python3 -m unittest discover -s tests`).

**Spec:** `docs/specs/2026-11-16-signal-groups-design.md` (accepted 2026-11-16, Q1–Q10 and F1–F7 answered).
S7 (agent started from Signal, §15) is **not** in this plan: separate plan after S1–S6.

**Order and deadline:** S1 (Tasks 1–5) → S3 (6–7) → S2 (8) → S4 (9–11) → S5 (12–16) → live run (17) **before
Collabothon 20–22.11**. After it: S6 (18–19), S8 (20).

## Global Constraints

- `signal-cli receive` CONSUMES messages. Tests use only fake `run`/`Popen`; `tests/helpers.py` keeps
  `HOUSE_NOTIFY_CONFIG` pointing nowhere and additionally sets `HOUSE_SIGNAL_CONFIG` (signal.toml) and
  `HOUSE_SIGNAL_ATTACHMENTS` (signal-cli attachments dir) to nonexistent paths, so no test can read or write the
  real config or copy real files.
- Store dir `notify.data_dir() / "signal"` (`$HOUSE_DATA_DIR/signal` or `~/.local/share/house/signal`), dir 0700,
  files 0600, set on every creation. Files: `messages.jsonl`, `state.json`, `cursors.json`, `inbox_done.json`,
  `pending.jsonl`, `receive.lock`, `store.lock`, `files/<alias>/…` (the spec's `pending-out.jsonl` is named
  `pending.jsonl` because `pull` also falls back to it).
- JSON lines with `ensure_ascii=False`; every append is `write` + `flush` + `os.fsync` before the next line is read.
- Event `v: 1` fields exactly as spec §11: `v, seq, key, kind, dir, chat, chat_alias, ts, time, received_at,
  sender{name,number,uuid}, text, quote{ts,author,text}|null, mentions, attachments, expires_in, target, meta,
  status, raw`. Extra fields allowed by this plan: `reaction{emoji,remove}` (kind `reaction`), `label` (kind
  `other`: `[naklejka]`, `[ankieta]`, `[zmiana grupy]`, `[inne]`).
- `kind ∈ {message, reaction, edit, delete, other}`, `dir ∈ {in, own, out}`, `status ∈ {ok, failed}`,
  `key = f"{chat}:{author}:{ts}"` (author = uuid, else number; out events use the account number; failed sends use
  `out-failed-<local ms>` as ts part; migrated events use `migrated`).
- Privacy: messages from DMs and non-allowed groups are dropped in memory before any write (file, log, stdout,
  stderr); only the integer `dropped_last_pull` may record them. Non-JSON stdout lines are counted, never stored.
- Only `signalstore.pull` calls `Signal.receive` (static test greps `houselib/`). Docstring of `signal.py` says so.
- Every message house sends to a chat starts with `🤖 [house]` or `🤖 [<contest alias>]`.
- `allow`/`deny` need a TTY on stdin; without one: exit 2 and `dodanie grupy to decyzja człowieka: poproś Wojtka
  o \`house signal allow …\``.
- Exit codes of `house signal`: 0 (also empty result), 1 (Signal not configured for `send`, failed send),
  2 (bad argument, unknown alias, unknown/ambiguous ts, refused file, no TTY). A signal-cli error during the quick
  pull never changes a reading command's exit code.
- User-facing text in Polish, code/docstrings in English, Google-style docstrings like the rest of `houselib`.
- Commits small and by path (`git add <files>`), identity from local git config.

## Review Focus

1. **A pull killed by the hook timeout mid-stream** — lines already printed by signal-cli must be in the store;
   after `terminate`/watchdog kill the remaining buffered stdout is still drained and stored
   (Task 1 `test_receive_drains_after_stop`, Task 4 `test_pull_interrupted_keeps_lines`).
2. **Old code still writing `inbox.jsonl` after migration** (main checkout on another branch, manual loops) —
   `migrate` runs whenever `inbox.jsonl` exists, de-duplicates by `(chat, ts)` and renames it again
   (Task 5 `test_reappearing_inbox_is_migrated_again`).
3. **Same `ts` in one chat from two authors** — `show`/`--reply-to` must refuse and ask for `--from`
   (Task 7 `test_reply_to_ambiguous_ts`, Task 10 `test_show_ambiguous`).
4. **A reaction/edit/delete arriving before (or without) its target** — never shown as a message, never crashes,
   folded once the target exists (Task 9 `test_orphan_reaction_and_edit_are_ignored`).
5. **Non-UTF-8 or huge stdout line / emoji with skin tone and ZWJ** — decoding uses `errors="replace"`; hearts
   and thumbs recognised by set after stripping U+FE0F, U+200D and skin-tone modifiers
   (Task 1 `test_receive_bad_bytes`, Task 13 `test_reaction_variants`).

---

## S1 — Store

### Task 1: streaming `Signal.receive`

**Files:** Modify `houselib/signal.py`; Test `tests/test_signal.py`

**Interfaces:**
- Produces:
  - `@dataclass ReceiveResult: objects: int = 0, bad_lines: int = 0, error: str | None = None,
    returncode: int | None = None, stopped: bool = False`
  - `Signal.__init__(account, binary, run=subprocess.run, timeout=TIMEOUT_S, popen=subprocess.Popen,
    attachments_dir: Path | None = None)`; `attachments_dir` defaults to `$HOUSE_SIGNAL_ATTACHMENTS` or
    `~/.local/share/signal-cli/attachments`.
  - `Signal.receive(handle: Callable[[dict], None], timeout: int = 1, max_messages: int | None = None,
    should_stop: Callable[[], bool] = lambda: False) -> ReceiveResult`
  - `Signal._call` passes `stdin=subprocess.DEVNULL` (signal-cli reads stdin when `-m` is missing).

- [ ] **Step 1: failing tests** with a `FakePopen(lines, die_after=None, rc=0, stderr="")` whose `stdout` is an
  iterator (raises `OSError` after `die_after` lines), recording `terminate`/`kill`:
  - `test_receive_streams_each_object`: handle called with `[1, 2]` timestamps in order; argv ==
    `["sc","-a","+1","-o","json","receive","--timeout","3","--ignore-stories"]`; with `max_messages=200`
    `["--max-messages","200"]` precedes `--ignore-stories`.
  - `test_receive_counts_bad_lines`: stdout `'{"envelope":{}}\nnot json\n[1]\n\n'` → `objects == 1`,
    `bad_lines == 2` (blank lines are not counted).
  - `test_receive_process_dies_midway`: 3 lines then `OSError` → handle saw 3, `error` starts with `"read:"`.
  - `test_receive_drains_after_stop`: `should_stop` true after the first object → `terminate()` called once,
    the remaining 2 buffered lines still handled, `stopped is True`.
  - `test_receive_error_line`: rc 1, stderr `"INFO x\nERROR User +1 is not registered.\n"` →
    `error == "ERROR User +1 is not registered."` (first non-`INFO` line, cut to 200 chars).
  - `test_receive_missing_binary`: popen raises `FileNotFoundError` → `error == "signal-cli not found"`,
    handle never called.
  - `test_receive_bad_bytes`: a line with invalid UTF-8 does not raise (decode `errors="replace"`).
  - `test_send_closes_stdin`: `run` kwargs include `stdin=subprocess.DEVNULL`.
  - Replace the old `test_receive_skips_junk` / `test_receive_timeout_keeps_partial_output`.
- [ ] **Step 2:** `python3 -m unittest tests.test_signal -v` → FAIL.
- [ ] **Step 3: implement.** `Popen(argv, stdin=DEVNULL, stdout=PIPE, stderr=<TemporaryFile>, text=True,
  encoding="utf-8", errors="replace")`; a `threading.Timer(timeout + self.timeout, kill)` watchdog sets
  `error="timeout"`; `for line in proc.stdout` → `json.loads` → dict → `handle(obj)` (exceptions from `handle`
  propagate after the process is killed); after each object, if `should_stop()` → `terminate()` once and keep
  reading until EOF; `finally`: cancel timer, kill if alive, `wait`. Update the module docstring: "only
  `signalstore.pull` calls `receive`; never run `signal-cli … receive|send` by hand".
- [ ] **Step 4:** tests PASS; full suite still green except `test_inbox` users of the old list API (fixed in Task 4).
- [ ] **Step 5:** commit `feat(signal): stream receive line by line (Popen, callback, drain after stop)`.

### Task 2: envelope → event (`signalparse.py`)

**Files:** Create `houselib/signalparse.py`, `tests/signal_fixtures.py`; Test `tests/test_signalparse.py`

**Interfaces:**
- Produces: `extract(obj: dict, own: str, chats: dict[str, str], now: datetime | None = None)
  -> tuple[dict | None, bool]` — `(event without seq, dropped)`; `chats` maps chat id (`"self"` or group id) to
  alias. `dropped` is True only for content from a DM or a non-allowed group.
- Produces (fixtures, reused by every later test): `OWN = "+48111"`, `GID = "gT"`, builders
  `self_note(ts, text, quote=None)`, `group_in(ts, text, gid=GID, name="Kuba Nowak", number="+48999",
  uuid="u-kuba", quote=None, attachments=None, mentions=None, expires=0)`, `group_own(ts, text, gid=GID, …)`,
  `dm(ts, text)`, `reaction(ts, emoji, target_ts, target_author, gid=GID, remove=False, own=False)`,
  `edit(ts, target_ts, text, gid=GID)`, `delete(ts, target_ts, gid=GID)`, `sticker(ts, gid=GID)`,
  `receipt(ts)`, `typing(ts)`; quotes as `{"id", "author", "authorNumber", "authorUuid", "text"}`.

- [ ] **Step 1: failing tests** (`chats = {"self": "self", "gT": "techarena"}`):
  - `test_group_message_from_teammate`: kind message, dir in, chat gT, chat_alias techarena, sender
    `{"name": "Kuba Nowak", "number": "+48999", "uuid": "u-kuba"}`, key `"gT:u-kuba:<ts>"`, `time` is local ISO
    with offset, `raw` is the whole envelope wrapper, status ok.
  - `test_quote_text_and_author_kept`: `quote == {"ts": 100, "author": "u-own", "text": "🤖 [house] Cześć"}`.
  - `test_note_to_self_is_own`: chat self, dir own.
  - `test_own_message_in_group_is_own`.
  - `test_attachment_only_message`: no `message`, one attachment → kind message, text "", attachments
    `[{"content_type": "image/jpeg", "filename": "a.jpg", "size": 84000, "id": "x.jpg", "path": None}]`.
  - `test_mention_replaced`: text `"￼ zobacz"` with mention name "Ania" → `"@Ania zobacz"`; raw unchanged.
  - `test_reaction`: kind reaction, `reaction == {"emoji": "👍", "remove": False}`, `target == {"ts": 100,
    "author": "u-own"}`, text "".
  - `test_reaction_remove`, `test_edit` (kind edit, text new, target ts), `test_remote_delete` (kind delete),
    `test_sticker_is_other` (label `[naklejka]`), `test_expiring` (`expires_in == 3600`).
  - `test_dm_and_other_group_dropped`: `(None, True)` for `dm()` and a message in group `gX`.
  - `test_receipt_and_typing_ignored`: `(None, False)`.
  - `test_privacy_no_trace`: for a dropped DM the returned tuple contains no string at all (`repr` lacks the
    text, number and name).
- [ ] **Step 2:** FAIL. **Step 3:** implement. Container: `syncMessage.sentMessage` (own) else `dataMessage` else
  `editMessage` (envelope or `sentMessage.editMessage`; content in its `dataMessage`, target
  `targetSentTimestamp`). Chat: `groupInfo.groupId` must be in `chats`; else sync with `destinationNumber` or
  `destination` == own → `"self"`; else drop. Kind order: reaction, remoteDelete, edit, message (text or
  attachments), other (sticker / `poll*` / `groupInfo.type == "UPDATE"` / `isExpirationUpdate`), else
  `(None, False)`. Mentions: replace each U+FFFC in order of `start` with `@name`.
- [ ] **Step 4:** PASS. **Step 5:** commit `feat(signal): parse envelopes into store events with privacy filter`.

### Task 3: store core (`signalstore.py`)

**Files:** Create `houselib/signalstore.py`; Modify `tests/helpers.py`; Test `tests/test_signalstore.py`

**Interfaces:**
- Produces:
  - `store_dir() -> Path` (creates, chmod 0700)
  - `append(event: dict) -> int | None` — under `store.lock` (blocking, 5 s timeout): `seq = state.next_seq`,
    write line (+fsync, 0600), `state.next_seq += 1`; returns seq. Lock timeout → line to `pending.jsonl`
    (`O_APPEND`, no lock), returns None.
  - `ingest_pending() -> int` (under `store.lock`; moves pending lines into the store with fresh seq, truncates)
  - `iter_events(raw: bool = True) -> Iterator[dict]` (file order; skips a last line without `\n` and unparsable
    lines; `raw=False` drops the `raw` key)
  - `bad_lines() -> list[int]` (1-based numbers of unparsable complete lines; for doctor)
  - `read_state() -> dict`, `update_state(**changes) -> dict` (atomic replace under `store.lock`)
  - `find(chat: str, ts: int) -> list[dict]` (latest version per key of events with that chat and ts)
  - `last_seq() -> int`
  - `@contextmanager lock(name: str, blocking: bool, timeout: float = 5.0)` → yields bool (acquired)
- [ ] **Step 1: failing tests:**
  - `test_append_assigns_increasing_seq_and_perms`: two appends → seq 1, 2; file mode `0o600`, dir `0o700`;
    line is UTF-8 (`"ż" in raw bytes decoded`, no `\\u017c`).
  - `test_reader_skips_partial_and_bad_lines`: file with good line, `"{bad"`, good line, `'{"v":1'` (no `\n`) →
    two events; `bad_lines() == [2]`.
  - `test_append_falls_back_to_pending_when_locked`: hold `store.lock` in another fd, patch timeout to 0.1 →
    `append` returns None, `pending.jsonl` has the event; after release `ingest_pending() == 1` and the event is
    in the store with seq.
  - `test_find_returns_latest_version_per_key`.
  - `test_state_roundtrip`.
- [ ] **Step 2:** FAIL. **Step 3:** implement; add `HOUSE_SIGNAL_CONFIG` and `HOUSE_SIGNAL_ATTACHMENTS` to
  `tests/helpers.py` (nonexistent paths). **Step 4:** PASS. **Step 5:** commit
  `feat(signal): durable message store (seq under store.lock, pending fallback, tolerant reader)`.

### Task 4: `pull` (+ allowed chats from contests)

**Files:** Modify `houselib/signalstore.py`, `houselib/inbox.py`; Create `houselib/signalchats.py`;
Test `tests/test_signalstore.py`, `tests/test_signalchats.py`, `tests/test_inbox.py`

**Interfaces:**
- Produces:
  - `signalchats.Chat` dataclass: `id: str, alias: str, name: str, source: str, contests: tuple[Path, ...] = ()`
    (`source`: `"zawsze"`, `"konkurs <name>"`, later `"signal.toml"`).
  - `signalchats.allowed(sig=None, contests: list[Path] | None = None) -> dict[str, Chat]` — `self` plus groups of
    running contests (`notify.alias` as alias; several contests on one group → one Chat with all contests).
    `contests=None` → `inbox.all_running()`. `sig=None` → group ids from `groups.json` cache only (no signal-cli).
  - `signalchats.alias_of(chat_id: str) -> str | None` (cache only, never calls signal-cli).
  - `@dataclass PullResult: kept: int = 0, dropped: int = 0, bad_lines: int = 0, error: str | None = None,
    skipped: str | None = None, interrupted: bool = False`
  - `signalstore.pull(sig, chats: dict[str, Chat] | None = None, timeout: int = 1,
    max_messages: int | None = None) -> PullResult`
- [ ] **Step 1: failing tests** (a `FakeSig` exposing `account`, `attachments_dir`, `receive(handle, …)` feeding
  fixture objects, optionally raising after k):
  - `test_pull_stores_allowed_and_counts_dropped`: group_in, self_note, dm, other-group → 2 events;
    `state.dropped_last_pull == 2`; `last_pull_ok` set; `last_pull_error` None.
  - `test_privacy_nothing_written`: unique DM text/number/name and other-group id never appear in any file under
    `$HOUSE_DATA_DIR` (walk all files, read bytes) nor in captured stdout/stderr.
  - `test_pull_interrupted_keeps_lines`: fake raises after 3 objects → 3 events, error recorded.
  - `test_pull_skips_when_receive_locked`: hold `receive.lock` → `skipped == "inny odbiór trwa"`, `receive` never
    called.
  - `test_attachment_copied_for_group_only`: file `<attachments_dir>/x.jpg` → copied to
    `signal/files/techarena/2026-11-16_<ts>_a.jpg` (0600), event `path` points there; a DM attachment is not
    copied; missing source → `path is None`.
  - `test_sigterm_finishes_line_then_reraises`: the fake calls `os.kill(os.getpid(), SIGTERM)` after the first
    object with a test handler installed beforehand → all buffered objects stored, `interrupted is True`, the
    previous handler is called once after `pull` restores it.
  - `test_bad_envelope_counted`: an envelope that makes `extract` raise → skipped, others stored.
  - `test_allowed_self_and_contest_groups` / `test_one_group_two_contests` (signalchats).
  - `test_inbox` keeps passing: `inbox.pull` now calls `signalstore.pull` (FakeSignal in `tests/test_inbox.py`
    gets the callback-style `receive`); inbox still reads its own `inbox.jsonl` until Task 12 — so in this task
    `inbox.pull` appends kept *message* events in the old `Message` shape to `inbox.jsonl` too.
- [ ] **Step 2:** FAIL. **Step 3:** implement. `pull`: `ensure_migrated()` (Task 5; stub returning `[]` here),
  `receive.lock` non-blocking, `ingest_pending()`, then `sig.receive(handle, timeout, max_messages,
  should_stop)`; `handle` = `extract` → `append` (+ attachment copy before append). SIGTERM/SIGINT handlers only in
  the main thread: set a flag; after restoring the old handlers re-send the signal to self if it arrived. State
  update at the end: `last_pull_ok` (ISO, only when `error is None`), `last_pull_error`, `dropped_last_pull`,
  `bad_lines` (cumulative). **Step 4:** PASS. **Step 5:** commit `feat(signal): pull writes each allowed message
  before reading the next`.

### Task 5: migration

**Files:** Modify `houselib/signalstore.py`; Test `tests/test_signalstore.py`

**Interfaces:**
- Produces: `migrate(now: datetime | None = None) -> list[str]` (log lines), `ensure_migrated() -> list[str]`
  (runs `migrate` only if `inbox.jsonl`, `inbox_done.json` or `sent.jsonl` exists in the data dir).
- [ ] **Step 1: failing tests** on the real current shape (`{"ts","chat","sender","text","quote_ts"}`) with 3
  self lines and 1 group line (sender "Patryk Zieliński"), `inbox_done.json == [ts1]`, a `sent.jsonl` with one
  record:
  - `test_migrate_inbox`: 4 events kind message, self → dir own, group → dir in (sender not among self senders),
    `quote == {"ts": q, "author": None, "text": None}`, `meta == {"migrated": True}`, `raw is None`,
    seq in ts order, key `"<chat>:migrated:<ts>"`; original renamed to `inbox.jsonl.migrated-<YYYYmmdd-HHMMSS>`
    with identical bytes.
  - `test_migrate_done_keys`: `signal/inbox_done.json == {"self:migrated:<ts1>": null}`.
  - `test_migrate_sent`: out event with `meta == {"origin": "notify", "contest": c, "question": q,
    "migrated": True}`.
  - `test_migrate_twice_is_noop`: second run → `[]`, store unchanged.
  - `test_reappearing_inbox_is_migrated_again`: new `inbox.jsonl` with one duplicate ts and one new → only the
    new one appended.
- [ ] **Step 2:** FAIL. **Step 3:** implement under `store.lock`; `state.migrated = <ISO>`.
  Add `house signal migrate` and `house signal pull [--timeout 5]` in a new `houselib/signalcli.py`
  (`register(sub)` adds the `signal` parser with nested subcommands; `cli.build_parser` calls it). `pull` prints
  `odebrano N, odrzucono M (spoza dozwolonych)` or the skip/error reason; exit 0.
- [ ] **Step 4:** PASS. **Step 5:** commit `feat(signal): migrate inbox.jsonl/inbox_done.json/sent.jsonl into the
  store; house signal pull|migrate`.

## S3 — Log of everything sent

### Task 6: `Signal.send` logs `out` events

**Files:** Modify `houselib/signal.py`, `houselib/signalstore.py`, `houselib/notify.py`, `houselib/inbox.py`,
`houselib/cli.py` (night failure notice); Test `tests/test_signal.py`, `tests/test_notify.py`, `tests/test_inbox.py`

**Interfaces:**
- Produces:
  - `Signal.send(text: str, group_id: str | None = None, *, attachments: Sequence[str] = (),
    quote: tuple[int, str] | None = None, meta: dict | None = None) -> int | None` — argv adds
    `--attachment P` per file and `--quote-timestamp TS --quote-author A`; always passes `-m text`.
  - `signalstore.record_out(account: str, chat: str, text: str, ts: int | None, *, attachments=(), quote=None,
    meta=None) -> int | None` — event kind message, dir out, sender `{"name": "house", "number": account,
    "uuid": None}`, status `ok`/`failed`, `quote.text` looked up with `find`, `attachments` as
    `[{"filename", "size", "path"}]` with the original path; never raises (logs nothing on its own failure).
  - `notify.send(..., origin: str = "notify")`; `ask` passes `"ask"`, `deadlines` reminders/defaults `"deadline"`,
    `cmd_night` failure notice `"night"`, `inbox._reply` `"inbox-reply"` (with contest when known). `meta =
    {"origin", "contest", "question"}`; default origin of a bare `Signal.send` is `"python"`.
- [ ] **Step 1: failing tests:** `test_send_logs_out_event` (bare `Signal.send` with fake run → one out event,
  `meta.origin == "python"`, ts from stdout), `test_failed_send_logged` (rc 1 → `status == "failed"`,
  `ts is None`), `test_send_quote_and_attachments_argv`, `test_notify_meta` (origin notify/ask, contest, question
  on both chats), `test_inbox_confirmation_logged` (the `✓ Q-001` reply is an out event with origin
  inbox-reply). Notify/inbox FakeSignals accept `**kw`.
- [ ] **Step 2:** FAIL. **Step 3:** implement (lazy import of `signalstore` inside `send`). `notify.send` keeps
  writing `sent.jsonl` until Task 12. **Step 4:** PASS. **Step 5:** commit `feat(signal): every send is logged in
  the store (origin, contest, question, quote, files)`.

### Task 7: `house signal send`

**Files:** Modify `houselib/signalcli.py`; Test `tests/test_signal_cli.py`

**Interfaces:**
- Consumes: `signalchats.allowed`, `signalstore.find`, `Signal.send`.
- Produces: `signalchats.resolve(name: str, sig=None) -> Chat` (raises `ValueError` with a Polish message for
  unknown alias, phone number or `all`); CLI `house signal send CHAT [TEXT...] [--reply-to TS] [--file PATH]...
  [--from NAME]` (`--from` disambiguates `--reply-to`).
- [ ] **Step 1: failing tests** (patch `signal.from_config` to a Signal with fake run; cwd outside contests):
  - `test_send_prefixes_and_logs`: `send self hej` → argv `-m "🤖 [house] hej"`, stdout
    `✓ self <ts>`, exit 0; inside a contest repo the prefix is `🤖 [fr]`; text already starting with the prefix is
    not doubled.
  - `test_reply_to`: store has Kuba's event ts 50 → argv has `--quote-timestamp 50 --quote-author u-kuba`, stdout
    `(cytat: Kuba Nowak HH:MM „…”)`.
  - `test_reply_to_unknown_ts` → exit 2, nothing sent. `test_reply_to_ambiguous_ts` (two authors) → exit 2,
    message mentions `--from`.
  - `test_phone_number_refused`, `test_unknown_alias` → exit 2.
  - `test_file_rules`: `--file` adds `--attachment`; > 100 MB (sparse file via `truncate`), `~/.ssh/x`,
    `~/.config/x`, `~/.local/share/signal-cli/x`, `a.env`, `.env`, `k.pem`, `k.key` → exit 2, nothing sent;
    stdout `(📎 lb.png, 84 KB)`.
  - `test_not_configured` → exit 1 `Signal nie jest skonfigurowany`.
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** commit `feat(signal): house signal send (prefix,
  --reply-to, --file)`.

## S2 — Allowed groups

### Task 8: `signal.toml`, `groups`, `allow`, `deny`

**Files:** Modify `houselib/signalchats.py`, `houselib/signalcli.py`; Test `tests/test_signalchats.py`,
`tests/test_signal_cli.py`

**Interfaces:**
- Produces: `config_path() -> Path` (`$HOUSE_SIGNAL_CONFIG` or `~/.config/house/signal.toml`),
  `load_config() -> dict[str, dict]` (alias → `{id, name, added, retention_days?, contests?}`; missing or
  invalid file → `{}` plus a warning available via `config_problems() -> list[str]`),
  `save_config(groups: dict[str, dict]) -> None` (own minimal TOML writer, header comment from spec §5, atomic,
  0600, dir 0700), `validate_alias(alias, gid, contest_chats) -> str | None` (regex `^[a-z0-9][a-z0-9-]*$`,
  reserved `self`/`all`, unique, collision with a contest alias unless same id),
  `collisions() -> list[str]` (for doctor). `allowed()` adds toml groups (`source "signal.toml"`).
  CLI: `groups [--all]`, `allow NAME [--alias A] [--id ID]`, `deny ALIAS`.
- [ ] **Step 1: failing tests:**
  - `test_toml_roundtrip_keyed_by_id` (aliases, quotes and `"` in names survive; file mode 0600).
  - `test_allowed_includes_toml_groups`; `test_contest_alias_wins_only_with_same_id`.
  - `test_allow_without_tty_refused`: exit 2, stderr has `poproś Wojtka`, file not created.
  - `test_allow_with_tty`: `isatty` patched True, input `t` → entry `{id, name, added}`; input `n` → no change.
  - `test_allow_ambiguous_name_lists_ids`, `test_allow_unknown_group`, `test_allow_by_id`.
  - `test_default_alias_slug`: `techarena_claude` → `techarena-claude`.
  - `test_deny_removes_entry_keeps_history`; `test_deny_contest_group_refused` (exit 2, message about
    `notify.signal_group`).
  - `test_groups_table`: columns `alias grupa źródło wiadomości ostatnia`, counts from the store.
  - `test_groups_all_without_tty_hides_names`: prints `+ 2 innych grup konta (nazwy tylko w terminalu)`, no
    names; with TTY lists names with `[dozwolona]` marks.
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** commit `feat(signal): allowed groups in signal.toml
  (groups, allow/deny need a TTY)`.

## S4 — Reading

### Task 9: folding and filters (`signalread.py`)

**Files:** Create `houselib/signalread.py`; Test `tests/test_signalread.py`

**Interfaces:**
- Produces:
  - `@dataclass Msg: ev: dict, text: str, edited: bool = False, deleted: bool = False,
    reactions: list[tuple[str, str]] = field(default_factory=list), replies: list[int] = …`
  - `build(events: Iterable[dict], now: datetime) -> list[Msg]` — dedupe by key (later line wins), fold
    reactions (removals cancel, one per sender+emoji), edits (latest edit wins; `edited=True`), deletes; drop
    expired (`received_at + expires_in < now`); only kinds message/other become `Msg`; sorted by `(ts, seq)`;
    `replies` = ts of messages quoting it (same chat).
  - `parse_when(text: str, now: datetime) -> datetime` — `30m`, `2h`, `1d`, `10:00` (today; yesterday if that is
    in the future), `2026-11-16`, `2026-11-16T10:00`, `2026-11-16 10:00`; else `ValueError`.
  - `select(msgs, *, chat=None, since=None, until=None, sender=None, grep=None, thread=None, n=20) -> list[Msg]`
    — `sender="me"` = dir own/out; otherwise case-insensitive substring of name or number; `grep` case-insensitive
    on text and quote text; `thread=TS` = upward quote chain + all descendants; `n=0` = all, else last n.
  - `display_name(ev) -> str` (`🤖 house` for out), `snippet(text, width) -> str`.
- [ ] **Step 1: failing tests:** `test_dedupe_later_wins`, `test_reactions_folded_and_removed`,
  `test_edit_applied`, `test_delete_hides_text`, `test_expired_hidden`, `test_orphan_reaction_and_edit_are_ignored`,
  `test_parse_when_formats` (every format incl. the future `HH:MM` case and `ValueError`), `test_select_filters`
  (`n`, since/until, `from me`, `from kuba`, grep), `test_thread`, `test_snippet`:
  `snippet("🤖 [house] Cześć! Jestem Claude Wojtka, będę tu wrzucał wyniki.", 28) == "Cześć! Jestem Claude Wojtka…"`,
  `snippet("Spoko, a jaki wynik ma teraz baseline?", 28) == "Spoko, a jaki wynik ma teraz…"`, short text
  unchanged. Rule: strip a leading `🤖 [..] `, collapse whitespace, cut at the last space within `width + 1`
  chars, strip trailing `,.;:!?`, add `…`.
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** commit `feat(signal): fold reactions/edits/deletes and
  filter messages`.

### Task 10: `read` and `show`

**Files:** Modify `houselib/signalread.py`, `houselib/signalcli.py`; Test `tests/test_signalread.py`,
`tests/test_signal_cli.py`

**Interfaces:**
- Produces: `fmt_time(dt, now) -> str` (`HH:MM` today, `wczoraj HH:MM`, else `YYYY-MM-DD HH:MM`),
  `render_read(msgs, *, total, chat: Chat | None, last_pull, note, now, all_chats: bool) -> str`,
  `render_show(msg, all_msgs, now) -> str`, `maybe_pull(offline: bool) -> str | None` (quick pull when Signal is
  configured, last ok > 60 s ago and `receive.lock` free; returns a header note: `Signal wyłączony, pokazuję
  zapisane`, `błąd odbioru: …`, `inny odbiór trwa`, else None). CLI `read [CHAT] [-n 20] [--since] [--until]
  [--from] [--grep] [--thread TS] [--json] [--offline]`, `show TS [--chat CHAT] [--from NAME] [--raw]`.
- [ ] **Step 1: failing tests:**
  - `test_read_golden`: fixture = spec §8 example (chat techarena / name techarena_claude, times 12:02, 12:15,
    12:18, 12:31 today, ts built from those datetimes, `last_pull_ok` 12:31, now 12:40, 4096-byte attachment
    copied to `P`):
    ```
    techarena · techarena_claude · 4 z 4 · odebrano 12:31
    [12:02] T1 🤖 house: 🤖 [house] Cześć! Jestem Claude Wojtka, będę tu wrzucał wyniki.
    [12:15] T2 Ania K.: super, to ja wrzucę dane z wczoraj [📎 wyniki.csv, 4 KB]
            📎 P
    [12:18] T3 Kuba Nowak ↩ 🤖 house 12:02 „Cześć! Jestem Claude Wojtka…”:
            Spoko, a jaki wynik ma teraz baseline?
            👍 Wojciech Fiołka
    [12:31] T4 🤖 house ↩ Kuba Nowak 12:18 „Spoko, a jaki wynik ma teraz…”:
            🤖 [house] Wrzuciłem nowy baseline, LB 0.806
    ```
    (deviation from the spec sample, on purpose: the attachment path gets its own line so agents can open it,
    AC-8.)
  - `test_read_all_prefixes_alias`, `test_failed_out_marked` (`✗ nie wysłano` instead of ts),
    `test_quote_unknown_before_migration` (`„(treść cytatu nieznana, odebrano przed migracją)”`),
    `test_attachment_not_downloaded` (`[📎 a.jpg, nie pobrano]`), `test_edited_marker` (`(edytowano)`),
    `test_deleted_marker` (`[usunięta przez autora]`).
  - `test_show_golden` (spec §8 `show` block incl. `reakcje: 👍 Wojciech Fiołka · odpowiedzi cytatem: T4`),
    `test_show_quote_text_without_target_in_store` (AC-2), `test_show_ambiguous` (exit 2, both listed, asks for
    `--from`), `test_show_raw`.
  - CLI: `test_read_json_has_no_raw`, `test_read_offline_never_pulls`, `test_read_pull_error_keeps_exit_0`
    (header has the note), `test_read_skips_pull_within_60s`, `test_unknown_chat_exit_2`,
    `test_bad_since_exit_2`.
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** commit `feat(signal): house signal read|show`.

### Task 11: cursors, `unread`, `status`

**Files:** Modify `houselib/signalread.py`, `houselib/signalcli.py`; Test `tests/test_signalread.py`,
`tests/test_signal_cli.py`

**Interfaces:**
- Produces: `reader_name(cwd: Path) -> str` (contest → `notify.contest_name`; git repo → name of the main
  checkout dir, i.e. parent of `--git-common-dir`; else dir name), `unread(name, *, chat=None, peek=False,
  now) -> tuple[list[Msg], dict]` (cursor `{"seq", "at"}` in `cursors.json` under `store.lock`; new reader
  starts at the last seq whose `time` is older than now − 24 h; only kinds message/other with dir in/own;
  moves cursor to `last_seq()` unless `peek`), `unread_counts(name) -> dict[str, int]` (alias → count, no cursor
  move; for the hook and inbox), `render_unread(...)`, `render_status(...)`. CLI `unread [CHAT] [--peek]
  [--as NAME] [--json]`, `status`.
- [ ] **Step 1: failing tests:** `test_unread_moves_cursor`, `test_unread_peek_keeps_cursor`,
  `test_two_projects_independent`, `test_new_reader_starts_24h_back`, `test_out_not_unread`,
  `test_unread_golden` (header `3 nowe od 2026-11-16 09:40 (dla: house)`, per chat `techarena · 2`, lines
  `  [12:15] T2 Ania K.: …` — ts included so agents can `--reply-to`; footer `(oznaczone jako przeczytane;
  \`--peek\` nie oznacza)`), `test_reader_name_contest_and_house_worktree`, `test_status_lines` (last pull,
  error, dropped, bad lines, events per chat, pending count, cursors).
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** commit `feat(signal): unread cursors per project,
  house signal unread|status`.

## S5 — Integration

### Task 12: `house inbox` on the store

**Files:** Modify `houselib/inbox.py`, `houselib/notify.py`; Test `tests/test_inbox.py`

**Interfaces:**
- Produces: `process(contests, sig, scope=None, *, timeout: int = 1, max_messages: int | None = None)
  -> list[str]` — always pulls (`signalstore.pull` with `signalchats.allowed(sig, contests)`), **also with no
  running contest**; routes only kinds message/edit/reaction from `self` and contest chats in `ts` order;
  `Message` gains `key`; `route` reads quoted questions from out events (`meta.question`, `meta.contest`);
  done marks in `signal/inbox_done.json` as `{key: "<contest> Q-###" | null}`; edits: unhandled original → edited
  text is the answer; handled → log `Q-007: odpowiedź edytowana po zapisaniu, decyzja bez zmian`; final line
  `signal: N nowe w grupach (house signal unread)` when the reader of the cwd has unread messages in
  non-self chats. `notify.send` stops writing `sent.jsonl`; `inbox.pull` and `inbox.jsonl` writing are removed.
- [ ] **Step 1: failing tests:** every existing `test_inbox.py` test ported to the store (`sent_question` writes
  an out event via `record_out`); `test_pull_without_running_contest`; `test_toml_group_stored_not_routed`
  (`Q-001: A` in a `signal.toml` group answers nothing); `test_done_key_is_chat_author_ts`;
  `test_edit_before_handling_uses_new_text`; `test_edit_after_handling_logged`; `test_unread_line`.
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** commit `refactor(inbox): read answers from the store,
  pull even with no running contest`.

### Task 13: reactions as answers

**Files:** Modify `houselib/inbox.py`; Test `tests/test_inbox.py`

**Interfaces:**
- Produces: `reaction_answer(emoji: str) -> str | None` (`"yes"`, `"no"`, `"default"`, None) after removing
  U+FE0F, U+200D and U+1F3FB–U+1F3FF; hearts = any of `❤ ❣ 💔 💓 💕 💖 💗 💘 💙 💚 💛 💜 💝 💞 💟 🖤 🤍 🤎 🧡 🩵 🩶 🩷`
  contained; thumbs 👍/👎.
- Rules: reaction to an out event with `meta.question`; yes/no only for a question without options (answer text
  `tak`/`nie`); thumbs on a question with options → reply `odpowiedz literą albo ❤ = domyślna`, no answer; heart
  → `q.default`, or reply `brak domyślnej, odpowiedz tekstem`; a later `remove` by the same sender for the same
  target cancels an unhandled reaction; after handling nothing changes; reactions in contest groups count like
  in Note to Self.
- [ ] **Step 1: failing tests:** `test_reaction_variants` (`👍🏽`, `❤️`, `❤️‍🔥`, `❤️‍🩹`, `💔`, `🩷`, `😂` → None),
  `test_thumb_yes_no`, `test_thumb_on_options_question_replies`, `test_heart_takes_default`,
  `test_heart_without_default_replies`, `test_removed_before_handling_cancels`, `test_removed_after_handling_noop`,
  `test_reaction_in_group`.
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** commit `feat(inbox): 👍/👎 and hearts answer questions`.

### Task 14: session hook in contests and in the house repo

**Files:** Modify `houselib/hook.py`, `houselib/adapters.py`, `houselib/cli.py` (`setup --signal-hook`);
Test `tests/test_hook.py`, `tests/test_adapt.py`

**Interfaces:**
- Produces: `hook.session_start` returns signal lines also when cwd is the house repo (or one of its worktrees):
  `hook.house_root(cwd) -> Path | None` (git main checkout that has `houselib/` and `bin/house-hook`);
  `hook._signal_lines(contest: Path | None, cwd: Path) -> list[str]` = `inbox.process(…, timeout=1,
  max_messages=200, scope=[contest] or [])` + `deadlines` (contests only) + summary from
  `signalread.unread_counts(reader_name(cwd))` without moving the cursor; at most 4 signal lines, each ≤ 100
  chars: `signal: 3 nowe (techarena 2, self 1) · \`house signal unread\` · ostatni odbiór 12:31`, up to 3 lines
  `signal:   <alias> [HH:MM] <name>[ ↩ <quoted name>]: <text>`; none new → `signal: brak nowych · ostatni odbiór
  HH:MM`; not configured → no line; exception → `signal: pominięto (<ExcName>)`.
  `adapters.write_house_settings(root) -> Path` (SessionStart + PreToolUse hooks only, merged with
  `merge_settings`, `.claude/settings.local.json` excluded from git). `house setup --signal-hook` calls it;
  plain `house setup` prints a hint when Signal is configured.
- [ ] **Step 1: failing tests:** `test_house_repo_session_shows_signal_only`, `test_signal_lines_limits` (≤ 4
  lines, ≤ 100 chars, whole output ≤ 25 lines), `test_hook_does_not_move_cursor`, `test_hook_fail_open`,
  `test_no_new_line`, `test_write_house_settings_merges`.
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** commit `feat(hook): Signal summary at session start in
  contests and the house repo`.

### Task 15: guard rules for Signal

**Files:** Modify `houselib/guard.py`, `houselib/hook.py`, `houselib/adapters.py` (PreToolUse matcher adds
`Read|Grep|Glob`); Test `tests/test_guard.py`, `tests/test_hook.py`

**Interfaces:**
- Produces: `guard.check_signal(tool: str, args: dict, cwd: Path, home: Path = Path.home()) -> str | None`,
  applied by `hook.pre_tool` before the contest check (so it also works in the house repo):
  - Bash: a segment running `signal-cli` (any path) with `send` or `receive` → `gołe signal-cli send/receive omija
    magazyn; użyj \`house signal send|pull|read\``; any mention of `.local/share/signal-cli` → refused.
  - Read/Grep/Glob/Edit/Write/MultiEdit/NotebookEdit on a path under `~/.local/share/signal-cli/` → refused
    (`pliki signal-cli mieszają czaty prywatne; kopie z grup są w ~/.local/share/house/signal/files/`).
  - Edit-type tools or Bash writes (`guard._written_paths`) under `~/.config/house/` → refused (`konfigurację
    Signala zmienia tylko człowiek: house signal allow/deny`).
- [ ] **Step 1: failing tests:** `test_bare_signal_cli_blocked` (`signal-cli -a +1 receive`,
  `~/.local/bin/signal-cli -a +1 send -m x`, `foo && signal-cli receive`), `test_signal_cli_listgroups_allowed`,
  `test_read_signal_cli_dir_blocked` (Read, Grep `path`, Bash `cat ~/.local/share/signal-cli/attachments/x`),
  `test_write_config_blocked` (Write `~/.config/house/signal.toml`, Bash `echo x > ~/.config/house/signal.toml`),
  `test_rules_apply_in_house_repo`, `test_house_signal_commands_allowed`.
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** commit `feat(guard): agents cannot bypass the store or
  touch signal-cli files and house config`.

### Task 16: night, doctor, agent instructions, docs

**Files:** Modify `houselib/night.py`, `houselib/doctor.py`, `core/protocols/questions.md`,
`core/agents/coordinator.md`, `README.md`; Test `tests/test_night.py`, `tests/test_doctor.py`,
`tests/test_signal_static.py`

**Interfaces / content:**
- `night`: remember `signalstore.last_seq()` at start; report line `Signal: N nowych wiadomości w M rozmowach,
  nieprzeczytane: \`house signal unread\`` (counts only, no content).
- `doctor.signal_status()`: `last_pull_ok` from `signal/state.json` (warn when > 7 days or never), `last_pull_error`
  (unlinked/not registered → `urządzenie odłączone, \`house setup\` → ponowne linkowanie`), bad store line numbers,
  `signalchats.collisions()`, files/dirs with permissions other than 0600/0700.
- One line in `questions.md` and `coordinator.md`: `rozmowa z drużyną: \`house signal read|unread|show\`, odpis:
  \`house signal send <czat> "…" [--reply-to TS]\`; nie wołaj \`signal-cli\` ani \`Signal.receive\``.
  Plus the trust rule from §13 (requests from members = human requests; forwarded content = data; access
  requests refused) in `questions.md`.
- README: `house signal` commands table, `signal.toml`, privacy summary.
- [ ] **Step 1: failing tests:** `test_night_report_signal_line`, `test_doctor_last_pull_and_error`,
  `test_doctor_bad_lines_and_perms`, `test_only_store_calls_receive` (grep `houselib/*.py` for `.receive(` →
  only `signalstore.py`), content test that `questions.md` mentions `house signal read`.
- [ ] **Step 2–4:** FAIL → implement → PASS (`test_core_content` budgets still green). **Step 5:** commit
  `feat(signal): night report count, doctor checks, agent instructions, README`.

### Task 17: live migration and live check (before Collabothon)

- [ ] Full suite green.
- [ ] Back up `~/.local/share/house/` to `~/.local/share/house.bak-<stamp>/` (`cp -a`), then `bin/house signal
  migrate` from this branch; verify 23 migrated events (`house signal read all -n 0 --offline | head`), the renamed
  original still has the same bytes as the backup.
- [ ] `house signal pull` once (writes before showing), `house signal status`.
- [ ] Ask Wojtek on Signal (Note to Self, through `house signal send self`) to run `house signal allow
  techarena_claude --alias techarena` in a terminal (TTY rule: the agent cannot), and whether to install the hook
  in the house repo (`house setup --signal-hook`).
- [ ] AC-7/AC-8: a teammate quote-replies in `techarena_claude`; `house signal read techarena` shows the quote
  text; a photo lands in `signal/files/techarena/`; `house signal send techarena --file …` delivers.
- [ ] Update memory `signal-questions` to use `house signal send self` / `house signal read self` instead of raw
  `signal-cli`.

## After Collabothon

### Task 18 (S6): `compact`, `reparse`, `deny --purge`

**Files:** Modify `houselib/signalstore.py`, `houselib/signalcli.py`; Test `tests/test_signalstore.py`

- `compact(now) -> dict` under `store.lock`: write `messages.jsonl.tmp`, fsync, rename, `generation += 1`.
  Clears `text`/`raw`/`attachments` (+ deletes copies in `files/`) of deleted messages, expired ones
  (`received_at + expires_in`), and those older than `retention_days`; removes all events (and `files/<alias>/`)
  of chats purged with `deny --purge`; keeps the skeleton `v, seq, key, kind, dir, chat, chat_alias, ts, time,
  meta, status`.
- `reparse() -> int`: rebuild events that have `raw` with the current `extract`, keeping `seq`, `key`,
  `received_at` and attachment `path`.
- Tests: `test_compact_deleted_expired_retention`, `test_purge`, `test_generation_bumps`,
  `test_reader_during_compact_sees_old_or_new`, `test_reparse_fixes_quote`.
- Commit `feat(signal): compact, reparse, deny --purge`.

### Task 19 (S6): systemd timer

**Files:** Modify `houselib/setup.py`, `houselib/cli.py`, `houselib/doctor.py`; Test `tests/test_setup_adopt.py`

- `house setup --signal-timer` writes `~/.config/systemd/user/house-signal-pull.{service,timer}`
  (`ExecStart=<root>/bin/house signal pull --timeout 5`, `OnUnitActiveSec=5min`, `Persistent=true`, plus a daily
  `house signal compact` in the same service via `ExecStartPost` guarded by a date stamp in state) and runs
  `systemctl --user daemon-reload` + `enable --now` (injectable runner). Doctor: installed but inactive → warning.
- Tests with a fake `run`: unit text, idempotent rewrite, doctor line. Commit `feat(signal): systemd timer`.

### Task 20 (S8): archive of inactive chats

- `compact` archives a non-contest chat with no message for 180 days: `signal/archive/<alias>-<from>-<to>.zip`
  (events jsonl without `raw` + its `files/`), skeleton left in the store, alias kept in `signal.toml`;
  `house signal archive --summarize ALIAS` runs `claude -p` with the jsonl to write
  `signal/archive/<alias>-<from>-<to>.md` (who/why, decisions with ts, technical findings, open threads, files)
  in the `DECISIONS.md` style. Tests: zip contents, skeleton, summary prompt built from events (fake runner).
  Commit `feat(signal): archive inactive chats`.
