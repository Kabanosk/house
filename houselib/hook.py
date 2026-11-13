"""Claude Code hooks: `house-hook pre-tool` (block what guard forbids) and `house-hook session-start` (state).

Hooks must never break a session: anything unexpected → exit 0 with a one-line warning (fail open). Only a
guard rule blocks (exit 2, reason on stderr, which Claude Code shows to the model).
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
    marker = contest / ".house/tmp" / f"guard-off-{re.sub(r'[^A-Za-z0-9_-]', '_', session)}"
    if marker.exists():
        return
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("")
    with (contest / ".house/PROGRESS.md").open("a") as f:
        f.write(f"- {datetime.now():%Y-%m-%d %H:%M} · house · sesja {session} działa z HOUSE_GUARD=off "
                "(ochrona plików wyłączona)\n")


def _tasks(house: Path) -> dict[str, dict]:
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
