"""Claude Code hooks: `house-hook pre-tool` (block what guard forbids) and `house-hook session-start` (state).

Hooks must never break a session: anything unexpected → exit 0 with a one-line warning (fail open). Only a
guard rule blocks (exit 2, reason on stderr, which Claude Code shows to the model).

Callers and contract:
  * `bin/house-hook` reads the JSON event Claude Code sends on stdin and calls `pre_tool` (PreToolUse, matcher
    for Edit/Write/MultiEdit/NotebookEdit/Bash) or `session_start` (SessionStart); `house adapt` registers both in
    the contest's `settings.local.json`.
  * Outside a house contest both hooks do nothing (exit 0, no output).
  * Exit code 2 from `pre_tool` blocks the tool call; the message goes to stderr and the model reads it. Any
    other outcome allows the call. `HOUSE_GUARD=off` in the environment disables blocking (the first use per
    session is logged to `.house/PROGRESS.md`).
  * `session_start` prints a short state summary (at most 25 lines) that Claude Code adds to the session context.
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path

from . import guard
from . import spec as specmod
from .fm import parse_frontmatter

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def pre_tool(event: dict, env: dict) -> tuple[int, str]:
    """Decide whether a tool call may run (PreToolUse hook), applying the guard rules.

    Edit-type tools (`EDIT_TOOLS`) are checked with `guard.check_edit` on their `file_path` / `notebook_path`;
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


def session_start(event: dict) -> str:
    """Build the state summary Claude Code adds to a new session (SessionStart hook).

    Lines: contest name, kind and mode; the goal (cut to 120 characters); open questions from
    `.house/QUESTIONS.md` (first 6 ids); task counts per status and which `todo` tasks are ready (all
    `depends_on` merged or done); red checks (first 8); hours since the last briefing; a reminder to start with
    `protocols/briefing.md` when the human is returning. Missing or malformed files are skipped.

    Args:
        event: Decoded hook event from Claude Code. Key used: `cwd` (default: process cwd).

    Returns:
        Text of at most 25 lines ending in a newline, to be printed to stdout as is; "" when `cwd` is not inside
        a house contest.
    """
    contest = guard.contest_root(Path(event.get("cwd") or os.getcwd()))
    if contest is None:
        return ""
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
    red = []
    for path in sorted((house / "checks").glob("*.json")):
        try:
            results = json.loads(path.read_text()).get("results", {})
        except ValueError:
            continue
        bad = [cid for cid, r in results.items() if r.get("passes") is False]
        if bad:
            red.append(f"{path.stem} ({', '.join(bad)})")
    if red:
        lines.append("red checks: " + "; ".join(red[:8]))
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
    lines.append("If the human is just returning, start with protocols/briefing.md; answers: QUESTIONS.md.")
    return "\n".join(lines[:25]) + "\n"
