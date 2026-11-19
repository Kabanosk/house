"""Claude Code hooks: `house-hook pre-tool` (block what guard forbids), `post-tool` and `session-start` (state).

Hooks must never break a session: anything unexpected → exit 0 with a one-line warning (fail open). Only a
guard rule blocks (exit 2, reason on stderr, which Claude Code shows to the model).

Callers and contract:
  * `bin/house-hook` reads the JSON event Claude Code sends on stdin and calls `pre_tool` (PreToolUse, matcher
    for Edit/Write/MultiEdit/NotebookEdit/Bash) or `session_start` (SessionStart); `house adapt` registers both in
    the contest's `settings.local.json`.
  * Outside a house contest both hooks do nothing (exit 0, no output), except `session_start` in the house repo
    itself, which prints the Signal summary (registered there by `house setup --signal-hook`).
  * Exit code 2 from `pre_tool` blocks the tool call; the message goes to stderr and the model reads it. Any
    other outcome allows the call. `HOUSE_GUARD=off` in the environment disables blocking (the first use per
    session is logged to `.house/PROGRESS.md`).
  * `session_start` prints a short state summary (at most 25 lines) that Claude Code adds to the session context.
  * After a compaction (contest sessions only) Claude Code summarises with its own instructions (house adds none,
    K5) and then runs `session_start` with `source: "compact"`, which prints `compact_state` instead of the normal
    summary: every active human decision verbatim, answered and open questions, unfinished tasks and red checks,
    no Signal receive and no briefing line.
  * Output contract: `session_start` in `compact` mode prints at most 10 000 characters (Claude
    Code truncates longer hook output); every other `session_start` source is unchanged (at most 25 lines).
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path

from . import checks, decisions, evalmode, exp, guard, questions
from . import spec as specmod
from .fm import parse_frontmatter

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def pre_tool(event: dict, env: dict) -> tuple[int, str]:
    """Decide whether a tool call may run (PreToolUse hook), applying the guard rules.

    Signal rules (`guard.check_signal`) apply wherever the hook is installed, also in the house repo. Edit-type
    tools (`EDIT_TOOLS`) are checked with `guard.check_edit` on their `file_path` / `notebook_path`;
    `Bash` with `guard.check_bash` on its `command`. Other tools are always allowed.

    Args:
        event: Decoded hook event from Claude Code. Keys used: `cwd` (default: process cwd), `session_id`,
            `tool_name`, `tool_input` (dict with `file_path`, `notebook_path` or `command`).
        env: Environment variables (a plain dict, normally `dict(os.environ)`). `HOUSE_GUARD=off` disables
            blocking and notes the session in `.house/PROGRESS.md`.

    Returns:
        (exit code, stderr message). `(0, "")` allows the call. `(2, "house guard: <reason>. ...")` blocks it;
        Claude Code shows the message to the model, so agents depend on its wording (it points to asking the
        coordinator or `house check --refreeze --reason`).
    """
    cwd = Path(event.get("cwd") or os.getcwd())
    if env.get("HOUSE_AGENT_JOB"):  # a job started from Signal: stricter rules, fail closed, no HOUSE_GUARD=off
        try:
            reason = guard.check_job(str(event.get("tool_name") or ""), event.get("tool_input") or {}, cwd,
                                     Path(env.get("HOUSE_AGENT_WORKTREE") or cwd))
        except Exception as e:  # noqa: BLE001
            return 2, f"house guard (job mode): refused, the check failed ({type(e).__name__})."
        return (2, f"house guard: {reason}.") if reason else (0, "")
    if env.get("HOUSE_GUARD") != "off":
        reason = guard.check_signal(str(event.get("tool_name") or ""), event.get("tool_input") or {}, cwd)
        if reason:
            return 2, f"house guard: {reason}."
    contest = guard.contest_root(cwd)
    if contest is None:
        return 0, ""
    if env.get("HOUSE_GUARD") == "off":
        _note_guard_off(contest, str(event.get("session_id") or "?"))
        return 0, ""
    tool, args = event.get("tool_name"), event.get("tool_input") or {}
    reason = None
    if tool in EDIT_TOOLS:
        path = args.get("file_path") or args.get("notebook_path")
        rel = guard.repo_relative(path, cwd, contest) if path else None
        reason = guard.check_edit(rel, contest) if rel else None
    elif tool == "Bash" and args.get("command"):
        reason = guard.check_bash(args["command"], cwd, contest)
    if reason:
        return 2, (f"house guard: {reason}. If this change is really needed, ask the coordinator / human "
                   "(criteria change via `house check --refreeze --reason`).")
    return 0, ""


def post_tool(event: dict) -> str:
    """After an edit of QUESTIONS.md (or a shell command touching it): send new open questions to Signal."""
    cwd = Path(event.get("cwd") or os.getcwd())
    contest = guard.contest_root(cwd)
    if contest is None:
        return ""
    args = event.get("tool_input") or {}
    path = args.get("file_path") or args.get("notebook_path") or ""
    touched = (Path(path).name == "QUESTIONS.md") if path else "QUESTIONS.md" in str(args.get("command", ""))
    if not touched:
        return ""
    from . import notify
    sent = notify.ask_new(contest)
    return f"house: sent {', '.join(sent)} to Signal" if sent else ""


def house_root(cwd: Path) -> Path | None:
    """The main checkout of the house repo when `cwd` is inside it or one of its worktrees, else None."""
    import subprocess
    try:
        r = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=cwd,
                           capture_output=True, text=True, check=False, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    main = Path(r.stdout.strip()).parent
    return main if (main / "bin/house-hook").is_file() and (main / "core").is_dir() else None


SIGNAL_WIDTH = 100


def _fit(line: str) -> str:
    return line if len(line) <= SIGNAL_WIDTH else line[:SIGNAL_WIDTH - 1] + "…"


def signal_summary(reader: str, now: datetime | None = None) -> list[str]:
    """1–4 lines about unread Signal messages for `reader`, each at most 100 characters; the cursor is not moved.

    Line 1: how many are new per chat and the command to read them (or "brak nowych"), with the last receive
    time. Then at most the 3 newest, one line each.
    """
    from . import signalread, signalstore
    now = now or datetime.now().astimezone()
    last = signalread.fmt_time(signalread.parse_iso(signalstore.read_state().get("last_pull_ok")), now)
    msgs, _ = signalread.unread(reader, peek=True, now=now)
    if not msgs:
        return [f"signal: brak nowych · ostatni odbiór {last}"]
    from . import signalchats
    aliases = {c.id: c.alias for c in signalchats.allowed(None).values()}

    def alias_of(m) -> str:
        return aliases.get(m.chat) or str(m.ev.get("chat_alias") or f"{m.chat[:8]}…")
    counts: dict[str, int] = {}
    for m in msgs:
        counts[alias_of(m)] = counts.get(alias_of(m), 0) + 1
    per_chat = ", ".join(f"{a} {n}" for a, n in sorted(counts.items(), key=lambda kv: -kv[1]))
    lines = [_fit(f"signal: {signalread.nowe(len(msgs))} ({per_chat}) · `house signal unread` · "
                  f"ostatni odbiór {last}")]
    view = signalread.View(signalstore.iter_events(raw=False), now)
    for m in sorted(msgs, key=lambda m: m.ts or 0, reverse=True)[:3]:
        quoted = ""
        if m.ev.get("quote"):
            quoted = " ↩ " + view.quote_ref(m, with_time=False).split(" ↩ ", 1)[1].split(" „", 1)[0].replace("🤖 ", "")
        text = " ".join(m.text.split()) or view._files_inline(m)
        lines.append(_fit(f"signal:   {alias_of(m)} [{signalread.fmt_time(m.time, now)}] "
                          f"{signalread.display_name(m.ev)}{quoted}: {text}"))
    return lines


def _signal_lines(contest: Path | None, cwd: Path) -> list[str]:
    """Receive, apply answers (and deadlines in a contest), then summarise unread messages; fail open.

    Eval mode (`HOUSE_EVAL=1`): no lines at all, the owner's messages never reach an eval session; one
    `signal.pull` line goes to the eval log.
    """
    if evalmode.active():
        evalmode.log("signal.pull", via="hook.session_start")
        return []
    try:
        from . import inbox
        from .signal import from_config
        from .signalread import reader_name
        sig = from_config()
        if sig is None:
            return []
        sig.timeout = 10  # a session start must stay quick even if signal-cli waits on its lock
        everyone = inbox.all_running()
        if contest is not None and contest.resolve() not in {c.resolve() for c in everyone}:
            everyone.append(contest)
        log = inbox.process(everyone, sig, scope=[contest] if contest is not None else [], timeout=1,
                            max_messages=200)
        if contest is not None:
            log += inbox.deadlines(contest, sig)
        return [_fit(f"signal: {line}") for line in log[:2]] + signal_summary(reader_name(cwd))
    except Exception as e:  # noqa: BLE001
        return [f"signal: pominięto ({type(e).__name__})"]


def _note_guard_off(contest: Path, session: str) -> None:
    """Append a one-time-per-session line to `.house/PROGRESS.md` saying the guard is off (marker in `.house/tmp`)."""
    marker = contest / ".house/tmp" / f"guard-off-{re.sub(r'[^A-Za-z0-9_-]', '_', session)}"
    if marker.exists():
        return
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("")
    with (contest / ".house/PROGRESS.md").open("a") as f:
        f.write(f"- {datetime.now():%Y-%m-%d %H:%M} · house · sesja {session} działa z HOUSE_GUARD=off "
                "(ochrona plików wyłączona)\n")


def _tasks(house: Path) -> dict[str, dict]:
    """Load front matter of `.house/tasks/T*-*.md` as {task id: meta}, skipping templates and unreadable files."""
    out = {}
    for path in sorted((house / "tasks").glob("T*-*.md")):
        try:
            meta = parse_frontmatter(path.read_text())[0]
        except ValueError:
            continue
        if meta.get("status") != "template":
            out[path.name.split("-", 1)[0]] = meta
    return out


def _failing(path: Path) -> list[str] | None:
    """Ids of the criteria whose last result failed in a `.house/checks/T###.json`; None when unreadable."""
    try:
        results = json.loads(path.read_text()).get("results", {})
        return [cid for cid, r in results.items() if r.get("passes") is False]
    except (OSError, ValueError, AttributeError):
        return None


def _red_checks(house: Path) -> list[str]:
    """`T### (AC-1, AC-2)` for every frozen task with a failing criterion, sorted by task id."""
    red = []
    for path in sorted((house / "checks").glob("*.json")):
        bad = _failing(path)
        if bad:
            red.append(f"{path.stem} ({', '.join(bad)})")
    return red


def session_start(event: dict) -> str:
    """Build the state summary Claude Code adds to a new session (SessionStart hook).

    Lines: contest name, kind and mode; the goal (cut to 120 characters); open questions from
    `.house/QUESTIONS.md` (first 6 ids); task counts per status and which `todo` tasks are ready (all
    `depends_on` merged or done); red checks (first 8); the experiments line (`exp.summary_line`: best and
    stagnation, optimize contests); hours since the last briefing; a reminder to start with
    `protocols/briefing.md` when the human is returning; Signal lines (answers applied, unread messages, see
    `signal_summary`). In the house repo itself (not a contest) only the Signal lines are printed. Missing or
    malformed files are skipped.

    After a compaction (`source == "compact"`) it prints `compact_state` instead (no Signal, no briefing line).

    Args:
        event: Decoded hook event from Claude Code. Keys used: `cwd` (default: process cwd), `source`.

    Returns:
        Text of at most 25 lines ending in a newline, to be printed to stdout as is; "" when `cwd` is neither in a
        house contest nor in the house repo (or, in the house repo, when Signal is not configured). For
        `source == "compact"`: the `compact_state` text (at most 10 000 characters), "" outside a contest.
    """
    cwd = Path(event.get("cwd") or os.getcwd())
    contest = guard.contest_root(cwd)
    if event.get("source") == "compact":
        return compact_state(contest) if contest is not None else ""
    if contest is None:
        if house_root(cwd) is None:
            return ""
        signal_lines = _signal_lines(None, cwd)
        return "\n".join(signal_lines) + "\n" if signal_lines else ""
    signal_lines = _signal_lines(contest, cwd)  # first, so the summary below already shows recorded answers
    house = contest / ".house"
    spec = specmod.load_spec(house)
    lines = [f"house: contest {contest.name} · kind {spec.get('kind')} · mode {spec.get('mode')}"]
    goal = spec.get("goal")
    if goal:
        lines.append(f"goal: {str(goal)[:120]}")
    qtext = (house / "QUESTIONS.md").read_text() if (house / "QUESTIONS.md").exists() else ""
    open_q = re.findall(r"^## (Q-\d+) \[open\] (.*)$", qtext, re.MULTILINE)
    lines.append(f"open questions: {len(open_q)}" + (" (" + ", ".join(q for q, _ in open_q[:6]) + ")" if open_q else ""))
    tasks = _tasks(house)
    counts: dict[str, int] = {}
    for meta in tasks.values():
        counts[str(meta.get("status"))] = counts.get(str(meta.get("status")), 0) + 1
    finished = {t for t, m in tasks.items() if m.get("status") in ("merged", "done")}
    ready = [t for t, m in tasks.items()
             if m.get("status") == "todo" and set(m.get("depends_on") or []) <= finished]
    if tasks:
        lines.append("tasks: " + ", ".join(f"{s} {n}" for s, n in sorted(counts.items()))
                     + (f"; ready: {', '.join(ready)}" if ready else "; ready: none"))
    red = _red_checks(house)
    if red:
        lines.append("red checks: " + "; ".join(red[:8]))
    try:
        exp_line = exp.summary_line(contest)
    except Exception:  # noqa: BLE001 — the summary must never break a session
        exp_line = None
    if exp_line:
        lines.append(exp_line)
    try:
        last = json.loads((house / "state.json").read_text()).get("last_briefing")
    except (OSError, ValueError):
        last = None
    if last:
        try:
            hours = (datetime.now().astimezone() - datetime.fromisoformat(last)).total_seconds() / 3600
            lines.append(f"last briefing: {hours:.0f} h ago")
        except ValueError:
            pass
    else:
        lines.append("last briefing: never")
    lines += signal_lines[:6]
    lines.append("If the human is just returning, start with protocols/briefing.md; answers: QUESTIONS.md.")
    return "\n".join(lines[:25]) + "\n"


MIN_CUT = 200  # a cut item is worth showing only when this many characters of budget remain
MORE_LINE = "More: .house/DECISIONS.md, .house/QUESTIONS.md, .house/tasks/"


def _read(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def _qnum(qid: str) -> str:
    """Return the digits of a `Q-###` id, so `Q-3` and `Q-003` compare equal."""
    m = re.search(r"\d+", qid)
    return str(int(m.group())) if m else qid


def _task_items(house: Path) -> list[str]:
    """One line per task that is `doing`, `review` or `blocked`, with its last check result."""
    out = []
    for tid, meta in _tasks(house).items():
        if meta.get("status") not in ("doing", "review", "blocked"):
            continue
        check = checks.last_state(house / "checks" / f"{tid}.json")
        out.append(f"- {tid} [{meta.get('status')}] {meta.get('title')} — last check: {check}")
    return out


def compact_state(contest: Path, budget: int = 10_000) -> str:
    """Build what `session_start` prints after a compaction: the state the summary may have lost.

    Items, in this order: (1) `## Human decisions (verbatim)`: the full block of every active human decision
    (`decisions.active_human`), then `Q-### <title> — answer: <answer>` for every answered question that has no
    `D-###` entry (any author, superseded or not) titled `Q-###: …`: `questions.answer()` logs such an entry for
    every answer, so only answers edited into QUESTIONS.md by hand are listed here; (2) open questions (id,
    title, `Blokuje`); (3) tasks `doing` / `review` / `blocked` with their last check result; (4) red checks and the experiments line; (5) `MORE_LINE` when
    anything was left out. Items are kept in that order until the first one that does not fit the remaining
    budget; then everything after it (later items of the group and all lower groups) is left out and `MORE_LINE`
    follows, so a lower-priority item never outlives a dropped higher one. Items are shown whole, except that an
    item of group (1) that did not fit is shown cut with `…` instead of vanishing when at least `MIN_CUT`
    characters of budget remain. A section heading is printed only when an item of its group is.

    Args:
        contest: Contest root.
        budget: Maximum characters of the whole text, trailing newline included.

    Returns:
        Text starting with `house: state restored after compaction (contest <name>)` and ending in a newline.
    """
    house = contest / ".house"
    first = f"house: state restored after compaction (contest {contest.name})"
    qs = questions.parse(_read(house / "QUESTIONS.md"))
    logged = decisions.parse(_read(house / "DECISIONS.md"))
    # questions whose answer questions.answer() already logged as "D-### Q-###: <title>"
    logged_q = {_qnum(m.group(0)) for d in logged if (m := re.match(r"Q-\d+(?=:)", d.title))}
    try:
        exp_line = exp.summary_line(contest)
    except Exception:  # noqa: BLE001 — the restored state must never break a session
        exp_line = None
    red = _red_checks(house)
    groups: list[tuple[str, list[str]]] = [
        ("## Human decisions (verbatim)",
         [d.text for d in decisions.active_human(logged)]
         + [f"{q.id} {q.title} — answer: {q.answer}" for q in qs
            if q.status == "answered" and q.answer and _qnum(q.id) not in logged_q]),
        ("## Open questions",
         [f"{q.id} {q.title}" + (f" (Blokuje: {', '.join(q.blocks)})" if q.blocks else "")
          for q in qs if q.status == "open"]),
        ("## Unfinished tasks", _task_items(house)),
        ("## Checks and experiments",
         (["red checks: " + "; ".join(red[:8])] if red else []) + ([exp_line] if exp_line else [])),
    ]
    left = budget - len(first) - len(MORE_LINE) - 4  # newlines of the first and last line, plus a blank line
    parts, dropped = [first], False
    for gi, (heading, items) in enumerate(groups):
        shown = 0
        for item in items:
            extra = 0 if shown else len(heading) + 2  # the heading is charged to the first item shown
            if len(item) + 1 + extra > left:
                if gi == 0 and left >= MIN_CUT:  # a human decision or answer is cut, never dropped silently
                    if not shown:
                        parts += ["", heading]
                    parts.append(item[:left - extra - 2] + "…")
                dropped = True
                break
            if not shown:
                parts += ["", heading]
            parts.append(item)
            left -= len(item) + 1 + extra
            shown += 1
        if dropped:
            break
    if dropped:
        parts += ["", MORE_LINE]
    return "\n".join(parts) + "\n"
