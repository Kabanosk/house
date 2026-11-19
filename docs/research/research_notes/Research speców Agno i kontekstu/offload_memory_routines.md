# Offload, decision logs, memory curation and routines for coding agents (state 2026-11-17)

Scope: the four smaller mechanisms in the `house` specs: A4 `house out`, K3 `.house/sources/`, A5 `house decisions`,
A6 `house memory lint` and A7 `house routine due`. Spec sections read: `docs/specs/2026-11-16-agno-adoptions-design.md`
§10–13 and `docs/specs/2026-11-16-short-context-design.md` §9. Local facts were checked in the repo and on this machine
on 2026-11-17. Older material is flagged **[older]**. Secondary or unverified material is flagged **[secondary]**.

## 1. Large command and tool outputs: how current harnesses handle them

### Takeaway
Claude Code no longer simply cuts out the middle. A successful Bash result over about 30,000 characters is saved
whole to a session file, and the model sees the path plus a 2,000-character preview of the start. A **failed**
command still gets only a head-and-tail excerpt of about 10,000 characters and **no file**. Codex and
mini-swe-agent also keep head and tail with no file. Agno is the only one with a lossless store that has capped
paging and capped search. The A4 problem statement is half out of date. The real gaps are failing commands, the
tail of the output (the preview shows only the head), and a location that every agent can reach.

### Cited Findings
- **Claude Code, Bash (current docs).** Output streams to a working file while the command runs. A command whose
  output passes 5 GB is killed. A **valid** result comes inline up to roughly 30,000 characters. Past that,
  Claude gets "the path of a file saved to the session directory and truncated past 64 MiB, plus a preview of up
  to the first 2,000 characters", and reads or searches the file for the rest. A **failure** comes inline up to
  roughly 10,000 characters. Past that, it gets "a head-and-tail excerpt of that size cut from the read-back
  window, with no file path". — [Claude Code tools reference, Output limits](https://code.claude.com/docs/en/tools-reference)
- Exit code 1 counts as valid only for `grep`, `rg`, `egrep`, `fgrep`, `find`, `diff`, `test`, `[`, `git diff` and
  `git grep`. Any other command that exits 1 is a failure, so a failed `pytest` or training script gets the
  10K excerpt with no file. — [tools reference](https://code.claude.com/docs/en/tools-reference)
- `BASH_MAX_OUTPUT_LENGTH` sets the read-back window: default 30,000, ceiling 150,000. This is also the window
  the failure excerpt is cut from. The `bashOutputMaxChars` setting (max 128,000, needs v2.1.261+) sets the inline
  ceiling and the read-back window together and overrides the env var. — [env vars](https://code.claude.com/docs/en/env-vars); [tools reference](https://code.claude.com/docs/en/tools-reference)
- Disk persistence arrived in v2.1.2 and replaced the old ~30K middle truncation. — [AIWG disk-output-patterns](https://cdn.jsdelivr.net/npm/aiwg@2026.8.8/docs/disk-output-patterns.md) **[secondary]**
- Known problems with the persisted files:
  - no automatic cleanup of `~/.claude/projects/*/tool-results/` — [issue 31914](https://claudeissues.com/issue/31914-no-automatic-cleanup-of-persisted-tool-result-cache-files-in-claude-projects-too)
  - files with long lines cannot be chunked by Read `offset/limit` — [issue 41092](https://claudeissues.com/issue/41092-persisted-output-files-with-long-lines-cant-be-chunked-by-read-offset-limit)
  - the session can freeze when reading a persisted file — [issue 18095](https://claudeissues.com/issue/18095-bug-session-freezes-when-reading-persisted-output-tool-results-file-after-large)
  - the preview can show stale content — [issue 81129](https://claudeissues.com/issue/81129-bug-bash-tool-large-output-truncation-preview-shows-stale-unrelated-content-inst)

  All four are issue-tracker mirrors **[secondary]**.
- Observed in this research session: WebFetch results of 54.1 KB and 89.3 KB came back as
  `<persisted-output>` with "Preview (first 2KB)" and a path under
  `~/.claude/projects/-home-wojciech-fiolka-house/<session>/tool-results/`. Grepping that file worked well. There are
  35 such files on this machine (local check, 2026-11-17).
- **Claude Code, Read.** One call returns at most 25,000 tokens (`CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS`). A whole
  file over 256 KB is refused, with a hint to use `offset`/`limit` or Grep. Since v2.1.296, `allow_large: true` can
  read a whole file sized against the remaining context. — [tools reference, Large files](https://code.claude.com/docs/en/tools-reference)
- **mini-swe-agent** (the harness used in the NVIDIA paper). An observation of 10,000 characters or more is replaced
  by `output[:5000]` + `output[-5000:]` and a warning: "The output of your last command was too long… use head,
  tail or sed… a more selective search pattern." Nothing is stored. — [mini-swe-agent default.yaml](https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/config/default.yaml)
- **Codex CLI.**
  - Historically, exec output sent to the model was capped at 256 lines or 10 KiB, head + tail. PR #6476 splits the
    byte budget evenly (~5 KB head / ~5 KB tail) "to preserve error messages at the end", because before it "TAIL IS
    COMPLETELY LOST". Clients still get the full stream; only the model-facing text is capped. — [openai/codex PR #6476](https://github.com/openai/codex/pull/6476) (seen via search summary and mirrors **[secondary]**)
  - Newer versions have a `tool_output_token_limit` in `config.toml` (default not stated). — [Vaughan, Codex CLI KB](https://codex.danielvaughan.com/2026/08/30/agents-dont-paginate-first-chunk-selection-tool-responses-codex-cli/) **[secondary]**
- **OpenHands.** `truncate_content(content, max_chars)` cuts the middle of observations over `max_message_chars`.
  — [OpenHands docs, events.serialization.event](https://docs.all-hands.dev/modules/python/openhands/events/serialization/event). The default value was not confirmed (see Gaps).
- **Agno `offload/store.py`** (main, 2026-11-17):
  - Offload threshold: `DEFAULT_THRESHOLD_CHARS = 16_000`.
  - Envelope: head `DEFAULT_PREVIEW_LINES = 20` / `DEFAULT_PREVIEW_CHARS = 1200`, whichever binds first, plus the
    last `_TAIL_LINES = 5` lines after `[... N lines omitted ...]`.
  - Read: `READ_MAX_LINES = 400`, `READ_MAX_CHARS = 16_000`. A line that does not fit continues on the next page
    via `next_start_char`, so nothing is lost.
  - Search: `SEARCH_MAX_MATCHES = 20`, `SEARCH_MAX_CONTEXT_LINES = 20`, `SEARCH_LINE_CLIP = 500` chars around the
    match, one reply ≤16,000 chars, a `more` flag. Regexes that contain any of `*+?{(` run in a subprocess that is
    killed after `SEARCH_TIMEOUT_SECONDS = 10`.
  - Quotas: `MAX_RESULT_BYTES = 8_000_000`, `MAX_SESSION_NAMESPACE_BYTES = 200_000_000`, optional TTL with a sweep.
    Over a quota, the envelope says `stored="false"` with a reason and keeps the head and tail.
  - No model call on the write path. `offload_for_model` never raises.

  — [agno offload/store.py](https://github.com/agno-agi/agno/blob/main/libs/agno/agno/offload/store.py)
- **Measured effect, pagination.** Production MCP-middleware logs (clients: Claude Code, Cursor, Codex CLI, Aider)
  showed **zero** second-chunk requests. Putting the relevant content in the first chunk (keyword overlap, p₁ 24.2%
  → 35.0%) mattered more than ranking inside it. Downstream accuracy changed by −2.8…+2.2 pp, not significant.
  Advice: "design truncation as if the first chunk is the only chunk." — Petrova et al., [arXiv:2608.26130](https://arxiv.org/abs/2608.26130),
  via [Vaughan's summary](https://codex.danielvaughan.com/2026/08/30/agents-dont-paginate-first-chunk-selection-tool-responses-codex-cli/) **[secondary: paper not opened]**
- **Mini-swe-agent compaction prompt** (NVIDIA paper, App. G.1): "If needed output was truncated, give a narrow
  command to retrieve it." — local note `docs/research/2611.05306-you-dont-need-long-context.md`; [arXiv 2611.05306](https://arxiv.org/abs/2611.05306)

### Inferences
- In Claude Code today, the large output that still gets lost is the output of a **failing** command, such as a
  training crash or a red test suite. That is exactly where A4 matters. For successful commands, A4 adds three
  things: the tail in the envelope (the CC preview is head-only, and errors and final metrics sit at the end), a
  location shared across sessions, subagents and Codex (`.house/out/`, not a session-private dir), and pruning
  (CC never cleans up).
- Our envelope of "~40 head + ~40 tail lines" is bigger than Agno's (20 lines / 1,200 chars + 5 tail lines) and
  has **no character cap**. Training logs with `\r` progress bars (tqdm) or minified JSON produce single lines of
  hundreds of KB. A per-side character cap and a line clip are needed. CC issue 41092 shows the same long-line
  failure.
- Since agents rarely ask for page 2, the envelope itself must carry the decisive facts: exit code, size, line
  count, tail, and a ready `house out show <id> --grep …` hint.

### Gaps
- OpenHands' current default `max_message_chars` and whether OpenHands stores truncated output losslessly: only
  third-party mirrors were found.
- SWE-agent (full, not mini) windowed file viewer size: not verified in this pass.
- Codex's current default `tool_output_token_limit` and whether Codex persists overflow to a file: not found in
  primary sources.
- No study measured "head/tail N lines" against other envelope sizes for coding agents. The constants above are
  engineering choices, not tuned results.

## 2. Selective reading of long documents: evidence and thresholds

### Takeaway
The evidence is strong. Selective reading from files (grep + bounded windows) beats pasting long inputs in both
accuracy and cost. The NVIDIA paper reports up to +40 pp. The read windows it uses (6–8K chars) and the harness
caps (Agno 16K chars, the memory tool's `view` truncating at 16K chars, CC refusing whole files over 256 KB) all
sit near the K3 threshold of ~40 KB, which is the right order of magnitude.

### Cited Findings
- NVIDIA ("You don't need long context", 2026-11-03): long-input benchmarks were run with files on disk +
  `grep`/`sed` + `doc-read` (window max 6–8K chars) and "read only what you need".
  - Accuracy changed by −0.4 to **+40 pp**, at 4–233× lower cost.
  - Conversation length barely grows with document length.
  - Caveat: the cost is "estimated optimized retail cost"; at today's nominal API prices the savings are much smaller.

  — local note `docs/research/2611.05306-you-dont-need-long-context.md`; [arXiv 2611.05306](https://arxiv.org/abs/2611.05306)
- Anthropic recommends "just-in-time" retrieval: keep lightweight identifiers (paths, queries) and load data with
  tools at runtime. In its words, "primitives like glob and grep allow it to navigate its environment and retrieve
  files just-in-time"; Claude Code uses head/tail for large data and is "an agent that employs this hybrid model".
  It calls tool-result clearing "one of the safest lightest touch forms of compaction". — [Effective context engineering for AI agents, 2025-09-29](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) **[older: 2025]**
- The memory tool's `view` truncates text files longer than 16,000 characters, and the docs advise to "consider
  capping how many characters the `view` command returns, and let Claude page through the rest with
  `view_range`". — [Memory tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool)
- CC Read limits: 25,000 tokens per call, whole file refused over 256 KB, `allow_large: true` bypasses (v2.1.296+).
  — [tools reference](https://code.claude.com/docs/en/tools-reference)
- Grep in CC supports `head_limit`/`offset` and a `count` mode with totals. — [tools reference](https://code.claude.com/docs/en/tools-reference)

### Inferences
- K3's "~40 KB → grep first" is stricter than the harness (which only stops at 256 KB / 25K tokens), and that is
  intended. It matches the paper's spirit (6–8K-char windows). A range-size rule (for example ≤ ~200 lines or
  ~8–16K chars per read) would make it concrete.
- `allow_large: true` is a new escape hatch in CC that undoes K3. The K3 eval should fail when it is used on
  `.house/sources/*`.
- A headings index with line numbers (what the paper's `doc-stats`/`docs-list` give) lets the agent jump to a range
  without guessing grep terms. `house sources` can print it from Markdown headings in stdlib only.

### Gaps
- No public measurement for Claude Code specifically comparing "paste rules into the prompt" with "grep + ranges"
  on contest-style rule documents. The NVIDIA results are the closest evidence.

## 3. Decision logs for agents (ADR/MADR, author field, cross-project recall)

### Takeaway
The formats are settled: MADR 4.0, with status, date and decision-makers, and the "supersede, never edit" rule.
What makes agents actually use a log is **getting it into the agent's path at the right moment**: an index or
one-line summary in the instruction file, or a short block injected at task start. Agno injects recent decisions
into the system prompt. There is no measured data, only practitioner reports.

### Cited Findings
- MADR 4.0.0 (2024-09-17) has optional front-matter fields:
  - `status` (proposed | rejected | accepted | deprecated | … | superseded by ADR-0123)
  - `date`
  - `decision-makers` ("list everyone involved in the decision")
  - `consulted`
  - `informed`

  Files are `NNNN-title.md` in `docs/decisions/`. — [MADR](https://adr.github.io/madr/) **[older: 2024, still current]**
- Agno Decision Log:
  - Entry fields: `id`, `decision`, `reasoning`, `decision_type`, `context`, `alternatives`, `confidence`
    (0.0–1.0), `outcome`, `outcome_quality` (good/bad/neutral), `created_at`.
  - Scoped per agent, long-term, "agentic" (the agent decides what to log; nothing is captured automatically).
  - Tools: `log_decision`, `record_outcome`, `search_decisions` (filters such as `agent_id`, `decision_type`,
    `days`, `limit`).
  - Recent decisions are injected into the system prompt inside a `<decision_log>` block.
  - Postgres-backed.

  — [Agno docs, Decision Log store](https://docs.agno.com/learning/stores/decision-log)
- Practitioner guidance (Tian Pan, 2026-07-02) **[secondary, no data]**:
  - agents re-argue settled decisions because "every session starts from zero"
  - point to the ADR directory from `CLAUDE.md`/`AGENTS.md`, and inline the most re-litigated decisions as
    one-line constraints that link to the full record
  - put pointers where the temptation is ("see ADR-0031" in code)
  - "never edit an accepted record — write a new one that supersedes it", with status headers
  - audit instruction-file summaries periodically
  - "stale docs are poison delivered continuously"

  — [The ADR Your Agent Never Read](https://tianpan.co/blog/2026-07-02-the-adr-your-agent-never-read)
- Index-first pattern: a root CLAUDE.md names `docs/adr/INDEX.md` as "the map of the decisions in force", and the
  agent opens only the records the task touches. A separate warning describes accepted records edited "to read as
  current", which hid when the decision changed. — search summary of [Codeminer42, stale docs are prompt injection](https://blog.codeminer42.com/stale-docs-are-prompt-injection-you-wrote-yourself/) and [understandingdata.com](https://understandingdata.com/posts/adrs-for-agent-context/) **[secondary]**
- Claude Code auto memory has a `project` type for "decisions that Claude can't derive from the code or git
  history". It is machine-local and per repository. — [CC memory docs](https://code.claude.com/docs/en/memory)
- **Local facts.** The `house` protocol (`core/protocols/decisions.md`) defines the entry as `## D-### <title>` +
  Decyzja / Dlaczego / Kto (człowiek | judge | coordinator) / Data. It says "never renumber or delete", and "a
  reversed decision gets a new entry referencing the old". The real file
  `contests/grok-bot-wro-2026/.house/DECISIONS.md` has 5 entries, each with `- **Kto:** …`, but its **template
  header (line 9) also contains `- **Kto:** człowiek | judge | coordinator`** before D-001. Contests resolve
  through `houselib/registry.contests()` (only `contests/<name>` that contain `.house/`, often symlinks).

### Inferences
- Our D-### format already covers MADR's essentials (Kto ≈ decision-makers, Data, Dlaczego). What is missing is a
  machine-readable **supersede** link. Without it, `house decisions --by human` will surface a reversed decision
  as current, which is the "stale docs as poison" failure.
- Agno's `alternatives` and `outcome` fields are what make a log useful across contests ("we tried X, it went
  badly"). An optional `Wynik` field, filled during retro, gives that cheaply.
- The parser must only read fields **inside** `## D-###` blocks. Otherwise the template line pollutes the `--by`
  filter (verified on a real file).

### Gaps
- No quantitative study of whether agents read decision logs or of what injection format works best. All the
  evidence is anecdotal.
- No cross-project shared decision log was found in a mainstream coding-agent tool. Agno is per agent, and CC
  memory is per repo.

## 4. Memory curation (dedupe, staleness, what not to automate)

### Takeaway
Claude Code now enforces the index size itself (200 lines / 25 KB). It also ships a report-only audit,
`/doctor prompt-audit`, that finds "references to files or commands that don't exist", but for instruction files
rather than auto memory by default. Frameworks that curate automatically do it in simple ways: Agno uses exact or
normalized dedupe plus age/count pruning with auto-delete. Mem0 has **moved away** from LLM-driven
UPDATE/DELETE because it destroyed valid memories. Report-only is the conservative, well-supported choice.

### Cited Findings
- **CC auto memory:**
  - Location: `~/.claude/projects/<project>/memory/`, shared across worktrees of one repo, machine-local.
  - `MEMORY.md` is an index, "one line per memory".
  - Only the "first 200 lines of `MEMORY.md`, or the first 25KB, whichever comes first" load at session start.
    After Claude writes to it, CC warns near the limit: "keep one line per entry, move detail into topic files,
    and merge or drop stale entries". Over the limit, it returns an error telling Claude to rewrite the index.
  - Topic files load on demand.
  - Memory files are excluded from the `cleanupPeriodDays` sweep.
  - Four types go into the frontmatter `type`: `user`, `feedback`, `project`, `reference`.
  - Claude skips what can be derived from the code (architecture, file paths, debugging fixes).

  — [CC memory docs](https://code.claude.com/docs/en/memory)
- `/doctor prompt-audit` looks for "instructions written for older models, references to files or commands that
  don't exist, and files that contradict each other". It produces a report with proposed edits; "nothing in your
  files changes until you ask". By default it covers CLAUDE.md, CLAUDE.local.md, AGENTS.md, rules, skills,
  commands, subagents and output styles. A path argument limits it to one file or directory. — [CC memory docs](https://code.claude.com/docs/en/memory)
- **Local facts.** This machine's memory files carry frontmatter `name`, `description`, `metadata.type`,
  `metadata.originSessionId` and `metadata.modified` (ISO timestamp), for example
  `~/.claude/projects/-home-wojciech-fiolka-house/memory/git-identity.md`. 18 of 24 project dirs have a `memory/`
  dir, including non-`house` (work) projects.
- **Memory tool (API) guidance:**
  - "keep its content up-to-date, coherent and organized. You can rename or delete files that are no longer relevant"
  - "Periodically delete memory files that haven't been accessed in a long time"
  - cap file sizes

  — [Memory tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool)
- **Agno `learn/curate.py`:**
  - `prune(max_age_days=0, max_count=0)` and `deduplicate()`; 0 disables either.
  - Dedupe is "exact and near-exact string matching" (lowercase, strip punctuation, collapse whitespace), not
    semantic and not an LLM.
  - Both **delete automatically** and save.
  - Only the user_profile store is supported.

  — [agno learn/curate.py](https://github.com/agno-agi/agno/blob/main/libs/agno/agno/learn/curate.py)
- **Mem0:** the older pipeline let an LLM choose ADD/UPDATE/DELETE/NONE against the top-5 similar memories. A code
  review reports that the current OSS `Memory.add()` no longer does LLM-driven UPDATE/DELETE (add-only, with
  explicit update/delete APIs that keep history). The trade-off is less destructive overwriting but more
  accumulation. — [zby.github.io Mem0 review](https://zby.github.io/commonplace/agent-memory-systems/reviews/mem0/) **[secondary]**; the old logic is still in the [Mem0 custom update prompt docs](https://docs.mem0.ai/open-source/features/custom-update-memory-prompt)
- A failure mode of automatic conflict resolution: "the conflict resolver runs on text similarity, not on your
  application's notion of scope", so a context-specific preference was deleted as a "contradiction". — [dev.to: Mem0 silently deletes one you still need](https://dev.to/mukesh_13/mem0-auto-resolves-memory-conflicts-for-you-until-it-silently-deletes-one-you-still-need-4f4m) **[secondary]**
- **Letta:**
  - sleep-time agents consolidate memory between conversations
  - cheaper models are usually enough
  - no native time-based decay

  — [Letta forum guide](https://forum.letta.com/t/sleeptime-agents-for-memory-consolidation-best-practices-guide/154); [memory-decay note](https://publish.obsidian.md/cameron/reports/memory-decay-research) **[secondary]**. Background paper: [Sleep-time Compute, arXiv:2504.13171](https://arxiv.org/abs/2504.13171) (not opened).
- **"Auto Dream" in Claude Code:**
  - reportedly a background consolidation of memory files (duplicates, contradictions, stale dates)
  - triggers after ≥24 h and ≥5 sessions; manual trigger `/dream`
  - can write only memory files; uses a lock file
  - gradual rollout behind a flag

  **Not in official docs.** Community sources only: [implicator.ai](https://www.implicator.ai/anthropic-adds-auto-dream-to-claude-code-fixing-memory-decay-between-sessions/), [claudefa.st](https://claudefa.st/blog/guide/mechanics/auto-dream) **[secondary, unverified]**

### Inferences
- A6's three checks (duplicates, index↔file mismatch, dead references) match what both CC (`prompt-audit`) and
  Agno consider safe to detect. Deletion is where the documented harm is (Mem0), so "report only" is right.
- Two cheap, deterministic checks are missing from A6:
  - the index against CC's real load limit (200 lines / 25 KB), because entries past it are silently invisible
  - frontmatter (`type`, `modified`); `project`-type entries ("ongoing work, deadlines") are the ones that go
    stale, and `modified` gives their age with no model
- Memory dirs include work projects. The lint report must not copy memory **contents** of other projects into a
  `house` file. Printing paths, line numbers and short hashes is enough.
- If Auto Dream is active on this machine, it rewrites memory on its own. A read-only lint does not race with it,
  but any later "apply fixes" step would need to check for its lock.

### Gaps
- The Auto Dream status, its trigger rules and its lock file name could not be confirmed in Anthropic docs.
- Whether `/doctor prompt-audit <memory dir>` handles auto memory files well (the frontmatter, the index
  semantics) is untested.

## 5. Scheduled agent routines: cloud, local and CI options; idempotence, overlap, catch-up

### Takeaway
The industry pattern is "schedule → fresh session → proposal for a human". Anthropic's cloud routines push to
`claude/` branches and leave PRs for review. Agno routines "permit proposals, never acceptance", and the review
queue is the brake. For a local, stdlib, shared-machine setup, the reliable recipe is:
- a systemd **`OnCalendar=` timer with `Persistent=true`** (catch-up only works with OnCalendar)
- a "due" check in code that runs **one** catch-up for the latest missed slot
- a non-blocking lock
- resource limits on the unit

The Signal-agent design in this repo already has most of the parts.

### Cited Findings
- **Claude Code cloud scheduled tasks (formerly routines), research preview:**
  - They run on Anthropic cloud with a fresh clone of the repos, plus connectors.
  - Triggers: schedule, API (`/fire`, beta header `experimental-cc-routine-2026-04-01`), GitHub events.
  - Minimum interval 1 hour; runs exactly on the hour can start several minutes late ("pick 9:07").
  - Claude pushes to `claude/`-prefixed branches unless the prompt says otherwise.
  - Sessions start in auto mode.
  - Usage draws on the subscription "the same way interactive sessions do", plus 100 scheduled runs/hour per
    account.
  - If GitHub is disconnected, runs are skipped for up to 72 h, then the task turns off.

  — [CC routines docs](https://code.claude.com/docs/en/routines)
- **Comparison table** (official):

  | | Cloud | Desktop | `/loop` |
  |---|---|---|---|
  | Machine must be on | no | yes | yes |
  | Local file access | no | yes | yes |
  | Minimum interval | 1 h | 1 min when Claude sets it, 1 h in the forms | 1 min |

  — [CC Desktop scheduled tasks](https://code.claude.com/docs/en/desktop-scheduled-tasks)
- **Desktop local tasks, semantics worth copying:**
  - The schedule is checked every minute.
  - Each task gets a **deterministic** delay of a few minutes to stagger traffic.
  - On wake or start, Desktop checks the last **seven days** and "starts exactly one catch-up run for the most
    recently missed time and discards anything older".
  - Skipped runs are recorded with a reason ("computer was asleep, the previous run was still in progress, or
    other scheduled tasks were already running").
  - Prompts should carry timing guardrails ("A task scheduled for 9am might run at 11pm… add guardrails to the
    prompt itself").
  - Task prompt stored at `~/.claude/scheduled-tasks/<task-name>/SKILL.md`.

  — [CC Desktop scheduled tasks](https://code.claude.com/docs/en/desktop-scheduled-tasks)
- `CLAUDE_CODE_DISABLE_CRON=1` disables session-scoped scheduling (`/loop`, `CronCreate`). — [env vars](https://code.claude.com/docs/en/env-vars)
- **systemd.timer:**
  - `Persistent=`: "Note that this setting only has an effect on timers configured with `OnCalendar=`." When true,
    the last trigger time is stored on disk, and a missed run fires at the next activation.
  - `RandomizedDelaySec=` adds a random delay; `AccuracySec=` defaults to 1 min.
  - `OnUnitActiveSec=` is relative to the last activation.
  - If the service is still running when the timer elapses, it is "not restarted, but simply left running", so
    there is no double start from the timer itself.
  - `systemctl clean --what=state` removes the stamp.

  — [systemd.timer(5)](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html)
- **Local fact:** `houselib/setup.py` `TIMER_UNIT` for `house-signal-pull` uses `OnBootSec=1min` +
  `OnUnitActiveSec={every}` + **`Persistent=true`**. By the man page, Persistent has no effect there. It is
  harmless for a 1–5 min poll, but it would be wrong to copy for a weekly routine.
- **Agno `company/routines.py`:**
  - Routines become cron schedules (`cron_expr`, `timezone`).
  - The runtime automation runs hourly (`_AUTOMATION_CRON = "0 * * * *"`).
  - Each tick is capped at `_AUTOMATION_TICK_SECONDS = 240`, below "the scheduler's 300-second stale-claim
    threshold".
  - Claims carry `worker_id`/`locked_at`.
  - A new dispatch is refused with "The previous Task run is still active".
  - Routine authority "permits proposals, never acceptance or schedule edits".
  - Auto-accept needs a human ("Only a human may authorize automatic acceptance").
  - "The review queue is the brake: a person finishing a Task makes room for the next."

  — [agno company/routines.py](https://github.com/agno-agi/agno/blob/main/libs/agno/agno/company/routines.py)
- **Signal-agent design in this repo** (`docs/research/2026-11-16-signal-claude-agent.md`):
  - one `systemd-run --user` transient unit per job with `RuntimeMaxSec=2h`, `MemoryMax`,
    `KillMode=control-group`
  - claim-before-launch in `jobs.json`, append-only audit `jobs.jsonl`
  - `tick` takes `agent.lock` without waiting and reconciles state with `systemctl is-active`
  - a job older than 2 h does not start by itself
  - a kill switch through `systemctl --user stop …timer`

### Inferences
- A7 can reuse the Signal infra almost 1:1: transient unit per run, lock, claim-before-launch, a jsonl audit, an
  age guard. That avoids a second scheduler.
- Cloud routines don't fit the first routine. It needs local vendored skills, `house eval --tag smoke` on the
  subscription, and must not create branches. Cloud runs are fresh clones that default to pushing `claude/`
  branches. They could later host the pure "check new versions" half.
- Most of the weekly routine is deterministic: version lookups and `git ls-remote` of vendored skills. Doing that in
  Python and calling `claude -p` only to summarize changelogs keeps runs cheap and idempotent. The same inputs give
  the same report, so a repeated run is harmless.
- "Work sessions must not be affected": the unit needs `Nice=`, `IOSchedulingClass=idle`, `CPUQuota=`/`MemoryMax=`,
  a slot outside work hours, and a refusal when a night, contest or eval is running (answer to Q1 in the Agno spec).
  The subscription usage is shared with interactive sessions (CC routines docs), so the model step should stay
  small.

### Gaps
- `claude-code-action` scheduled-workflow specifics (auth with a subscription OAuth token vs API key, current
  inputs) were not fetched in this pass.
- Devin and Factory automations were not researched (budget). No primary sources are cited for them.
- Whether `systemd --user` timers run while the user is logged out on this machine depends on
  `loginctl enable-linger`. This was not checked here; the Signal timer setup may already cover it.

## Rekomendacje dla speca

Oznaczenia: **must** / **should** / **could**. Przy każdej zmianie podane źródło.

### A4 `house out`
1. **must.** Przepisać „Problem”. Claude Code nie przycina już środka udanej komendy. Wynik powyżej ~30K znaków
   zapisuje do pliku sesji i pokazuje 2K znaków **początku**. Komenda zakończona błędem dostaje tylko wycinek
   głowa+ogon ~10K znaków **bez pliku**. Źródło: [tools reference](https://code.claude.com/docs/en/tools-reference).
   Uzasadnienie A4 to cztery rzeczy:
   1. pełny wynik **nieudanych** długich komend (trening, testy);
   2. ogon w kopercie;
   3. miejsce wspólne dla podagentów i Codexa;
   4. sprzątanie, którego CC nie robi ([issue 31914](https://claudeissues.com/issue/31914-no-automatic-cleanup-of-persisted-tool-result-cache-files-in-claude-projects-too)).
2. **must.** Koperta ograniczona też znakami, nie tylko liniami:
   - ~40 linii z każdej strony, ale maks. ~2–4K znaków na stronę;
   - linie przycięte do ~500 znaków;
   - `\r` (paski postępu tqdm) zwijane do ostatniego stanu linii.

   Źródło: Agno `DEFAULT_PREVIEW_CHARS = 1200`, `SEARCH_LINE_CLIP = 500` ([store.py](https://github.com/agno-agi/agno/blob/main/libs/agno/agno/offload/store.py));
   mini-swe-agent 5000+5000 znaków ([default.yaml](https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/config/default.yaml));
   problem długich linii w CC ([issue 41092](https://claudeissues.com/issue/41092-persisted-output-files-with-long-lines-cant-be-chunked-by-read-offset-limit)).
3. **should.** Koperta pisana tak, jakby była jedyną stroną. Zawiera:
   - kod wyjścia (także sygnał 128+n);
   - rozmiar w bajtach i liniach;
   - czas trwania;
   - gotową podpowiedź `house out show <id> --grep '<wzorzec>'`.

   Źródło: brak drugich stron w logach agentów ([arXiv:2608.26130 via Vaughan](https://codex.danielvaughan.com/2026/08/30/agents-dont-paginate-first-chunk-selection-tool-responses-codex-cli/), **[secondary]**);
   „give a narrow command to retrieve it” (prompt kompakcji mini-swe-agent, notatka 2611.05306).
4. **should.** `--grep` z limitami Agno:
   - maks. 20 trafień;
   - kontekst ≤ 20 linii (domyślnie np. 3);
   - linie przycięte do 500 znaków;
   - całość ≤ 16 000 znaków;
   - flaga „są dalsze trafienia”.

   Stronicowanie dzieli zbyt długą linię zamiast ją gubić (`next_start_char`). Źródło: [store.py](https://github.com/agno-agi/agno/blob/main/libs/agno/agno/offload/store.py).
5. **should.** Limit rozmiaru i sprzątanie:
   - limit na plik (np. 8–64 MB, dalej tylko ogon kroczący);
   - limit całego `.house/out/`;
   - `house out prune` po wieku i rozmiarze.

   Źródło: CC ucina plik po 64 MiB i zabija komendę po 5 GB ([tools reference](https://code.claude.com/docs/en/tools-reference));
   Agno `MAX_RESULT_BYTES = 8_000_000`, `MAX_SESSION_NAMESPACE_BYTES = 200_000_000`, TTL.
6. **could.** Wzorce z wyrażeniami regularnymi z backtrackingiem (`*+?{(`) uruchamiać w podprocesie z limitem
   czasu. Prościej: domyślnie fixed-string, `--regex` na żądanie. Źródło: Agno `SEARCH_TIMEOUT_SECONDS = 10`.
7. **could.** Dziecku ustawiać `PYTHONUNBUFFERED=1`, żeby log treningu był w pliku na bieżąco i przetrwał Ctrl-C.
   To moje wnioskowanie, wspiera akceptację „plik przy przerwaniu”.

### K3 wybiórcze czytanie
1. **must.** Do reguły i do evalu dopisać zakaz `allow_large: true` na `.house/sources/*`. To nowy parametr Read w CC
   ≥ v2.1.296, który omija limity i unieważnia K3. Źródło: [tools reference, Large files](https://code.claude.com/docs/en/tools-reference).
2. **should.** Doprecyzować rozmiar okna: jeden odczyt to maks. ~200 linii albo ~8–16K znaków. Próg „> ~40 KB →
   najpierw grep” zostaje.
   Źródło: okno `doc-read` 6–8K znaków w paperze NVIDIA (notatka 2611.05306); `view` memory tool ucina przy 16 000 znaków
   ([memory tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool));
   Agno `READ_MAX_CHARS = 16_000`.
3. **should.** `house sources` wypisuje też liczbę linii i spis nagłówków Markdown z numerami linii (odpowiednik
   `doc-stats`), a długie linie oznacza ostrzeżeniem. Agent czyta wtedy zakres wskazany przez nagłówek, a nie
   zgaduje grepem. Źródło: `docs-list`/`doc-stats` w paperze; [issue 41092](https://claudeissues.com/issue/41092-persisted-output-files-with-long-lines-cant-be-chunked-by-read-offset-limit).
4. **could.** Zasadę „ścieżka i zakres, nigdy treść” uzasadnić w specu cytatem o just-in-time. Źródło: [Anthropic, context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) **[older: 2025]**.

### A5 `house decisions`
1. **must.** Parser czyta pola tylko wewnątrz bloków `## D-###`. Prawdziwy `DECISIONS.md` (grok-bot-wro-2026, l. 9)
   ma w nagłówku szablonu linię `- **Kto:** człowiek | judge | coordinator`. Do testów dodać fixture z tym
   nagłówkiem. Źródło: sprawdzenie lokalne.
2. **must.** Obsłużyć zastępowanie. Protokół mówi, że odwrócona decyzja to nowy wpis z odwołaniem do starej, ale
   nie ma pola, które da się odczytać kodem. Dodać do `protocols/decisions.md` opcjonalne `Zastępuje: D-###`.
   `house decisions` domyślnie ukrywa zastąpione wpisy albo oznacza je `[zastąpiona przez D-###]`.
   Źródło: MADR `status: superseded by` ([MADR 4.0](https://adr.github.io/madr/)); „never edit an accepted record — write a new one that supersedes it” ([Tian Pan](https://tianpan.co/blog/2026-07-02-the-adr-your-agent-never-read), **[secondary]**).
3. **should.** Wyjście: jedna linia na wpis (`konkurs D-### data kto: tytuł — ścieżka:linia`). Pełną treść agent
   czyta dopiero na żądanie. Tę listę `/house-start` wkleja jako „ograniczenia do potwierdzenia”.
   Źródło: Agno wstrzykuje ostatnie decyzje do system promptu w bloku `<decision_log>` ([Agno docs](https://docs.agno.com/learning/stores/decision-log));
   wzorzec „indeks, potem tylko potrzebne rekordy” ([understandingdata.com](https://understandingdata.com/posts/adrs-for-agent-context/), **[secondary]**).
4. **could.** Opcjonalne pola `Odrzucone opcje` i `Wynik` (dopisywane w `/house-retro`). Decyzja z poprzedniego
   konkursu niesie wtedy też to, jak wyszła. Źródło: Agno `alternatives`, `outcome`, `outcome_quality`.
5. **could.** Opcjonalny `Zakres: konkurs | ogólne`, żeby `--by human` z innych konkursów nie zaśmiecał startu
   decyzjami czysto lokalnymi. To moje wnioskowanie. Pasuje do tego, że Agno trzyma decyzje per agent, a nie
   globalnie.

### A6 `house memory lint`
1. **must.** Dodać sprawdzenie limitu indeksu. `MEMORY.md` ponad 200 linii albo 25 KB oznacza wpisy niewidoczne
   przy starcie sesji. Raportować też, które to wpisy. Źródło: [CC memory docs](https://code.claude.com/docs/en/memory).
2. **must.** Raport bez treści pamięci innych projektów: ścieżka, numer linii, krótki hash. Katalogi pamięci na tej
   maszynie obejmują projekty służbowe (18 z 24 katalogów `projects/` ma `memory/`). Domyślny zakres to tylko
   projekty `house` i konkursów z `contests.yaml`; `--all` na żądanie. Źródło: sprawdzenie lokalne i anty-cel
   „prywatna treść” w specu Agno §2.
3. **should.** Tolerancyjny odczyt frontmattera (`name`, `description`, `metadata.type`, `metadata.modified`) i dwa
   nowe wpisy w raporcie:
   - brak frontmattera albo typu;
   - wpisy typu `project` starsze niż N dni (np. 30) jako „do przejrzenia”, bez oceny treści.

   Źródło: typy `user/feedback/project/reference` ([CC memory docs](https://code.claude.com/docs/en/memory)); pola widoczne w lokalnych plikach.
4. **should.** Spec odwołuje się do `/doctor prompt-audit`, który robi raport z martwymi odwołaniami dla
   CLAUDE.md, skilli i reguł. `house memory lint` pokrywa to, czego audyt domyślnie nie obejmuje: auto memory,
   indeks i duplikaty. Audyt modelem można uruchomić ręcznie na katalogu pamięci (`/doctor prompt-audit <ścieżka>`).
   Źródło: [CC memory docs, Audit your instruction files](https://code.claude.com/docs/en/memory).
5. **should.** W „Sprawdzone i odrzucone” zapisać, dlaczego nie usuwamy automatycznie:
   - Agno `curate.py` usuwa sam ([curate.py](https://github.com/agno-agi/agno/blob/main/libs/agno/agno/learn/curate.py));
   - Mem0 wycofał LLM-owe UPDATE/DELETE z `add()` ([przegląd](https://zby.github.io/commonplace/agent-memory-systems/reviews/mem0/), **[secondary]**);
   - jest udokumentowany przypadek usunięcia potrzebnej pamięci ([dev.to](https://dev.to/mukesh_13/mem0-auto-resolves-memory-conflicts-for-you-until-it-silently-deletes-one-you-still-need-4f4m), **[secondary]**).
6. **could.** Duplikaty **między** projektami (ten sam fakt w kilku `memory/`) zgłaszać jako kandydatów do
   `~/.claude/CLAUDE.md`. To moje wnioskowanie.
7. **could.** Notatka dla przyszłego „zastosuj poprawki”: jeśli działa Auto Dream (nieudokumentowany, **[secondary]**),
   przed zapisem sprawdzić jego blokadę. Dla samego lintu (tylko odczyt) bez znaczenia.

### A7 rutyny
1. **must.** Timer: `OnCalendar=` (np. `hourly`) + `Persistent=true` + `RandomizedDelaySec=`. To, co jest „due”,
   decyduje `house routine due`. Nie kopiować wzoru z `house-signal-pull`, gdzie `Persistent=true` przy
   `OnUnitActiveSec=` nic nie robi. Źródło: [systemd.timer(5)](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html);
   `houselib/setup.py` `TIMER_UNIT`.
2. **must.** Zaległe przebiegi: po przerwie **jeden** przebieg za najnowszy pominięty termin, starsze odrzucone
   (okno np. 7 dni). Pominięcie zapisane z powodem w historii rutyny (`routines/state.json` + `runs.jsonl`).
   „Due” liczone od ostatniego **udanego** startu. Źródło: [CC Desktop scheduled tasks, Missed runs](https://code.claude.com/docs/en/desktop-scheduled-tasks).
3. **must.** Brak nakładania i brak wpływu na pracę:
   - `fcntl.flock` bez czekania, osobno dla każdej rutyny;
   - odmowa, gdy trwa noc, konkurs albo `house eval` (blokada z 9a);
   - uruchomienie przez `systemd-run --user` z `RuntimeMaxSec`, `MemoryMax`, `Nice=19`, `IOSchedulingClass=idle`,
     `KillMode=control-group`, tak jak zlecenia agenta Signal.

   Źródło: [systemd.timer(5)](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html) (timer
   sam nie uruchomi drugiej instancji, ale ręczne `house routine due` już tak); Agno „The previous Task run is still
   active” ([routines.py](https://github.com/agno-agi/agno/blob/main/libs/agno/agno/company/routines.py));
   `docs/research/2026-11-16-signal-claude-agent.md` §4–5.
4. **should.** Część deterministyczną zrobić w Pythonie, bez modelu:
   - wersje Claude Code i Codexa;
   - `git ls-remote` vendorowanych skilli i superpowers;
   - wynik `house eval --tag smoke`.

   `claude -p` tylko streszcza changelogi, z narzędziami tylko do odczytu, bez Edit i Write. Te same wejścia dają
   ten sam raport (idempotencja), a zużycie subskrypcji jest małe.
   Źródło: zużycie subskrypcji jak w sesjach interaktywnych ([CC routines](https://code.claude.com/docs/en/routines)).
5. **should.** Raport podaje planowany i faktyczny czas przebiegu oraz okno, które obejmuje („od ostatniego udanego
   przebiegu”). Prompt dostaje bezpiecznik czasowy. Źródło: „A task scheduled for 9am might run at 11pm… add
   guardrails” ([CC Desktop scheduled tasks](https://code.claude.com/docs/en/desktop-scheduled-tasks)).
6. **should.** Hamulec przeglądu: jeśli poprzedni raport nie jest oznaczony jako przeczytany, nowy raport go
   nadpisuje albo dopisuje się do niego, zamiast mnożyć pliki. Źródło: Agno „The review queue is the brake”
   ([routines.py](https://github.com/agno-agi/agno/blob/main/libs/agno/agno/company/routines.py)).
7. **could.** W „Sprawdzone i odrzucone” dopisać chmurowe scheduled tasks Claude Code. Odpadają, bo:
   - działają na świeżym klonie, bez plików lokalnych i bez evali na tej maszynie;
   - domyślnie pushują gałęzie `claude/`;
   - minimum to 1 h.

   Mogą później obsłużyć samo „sprawdź nowe wersje”. Źródło: [CC routines](https://code.claude.com/docs/en/routines).
8. **could.** Sprawdzić `loginctl enable-linger` w `house setup`. Bez tego timery użytkownika nie działają po
   wylogowaniu. To wnioskowanie, nie sprawdziłem tego na tej maszynie.
