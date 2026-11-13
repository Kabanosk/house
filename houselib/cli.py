"""`house` command line."""
import argparse
import sys
from pathlib import Path

from . import (
    HOUSE_ROOT,
    adapters,
    checks,
    context,
    doctor,
    export,
    guard,
    inbox,
    manifest,
    merge,
    notify,
    questions,
    registry,
    scaffold,
    setup,
    yc,
)


def contest_dir(name: str) -> Path:
    scaffold.validate_name(name)  # raises ValueError: never resolve "..", "/x", etc.
    return HOUSE_ROOT / "contests" / name


def cmd_doctor(args) -> int:
    if args.context and not args.name:
        print("error: --context needs a contest name (house doctor --context <name>)", file=sys.stderr)
        return 1
    tools = doctor.detect()
    print(doctor.summary(tools))
    for line in doctor.signal_status():
        print(line)
    for alias, names in doctor.alias_collisions(_all_contests()).items():
        print(f"! alias `{alias}` used by several running contests: {', '.join(names)} (spec notify.alias)")
    if args.name:
        target = contest_dir(args.name)
        if not (target / ".house").is_dir():
            print(f"error: {target} is not a house contest", file=sys.stderr)
            return 1
        print(f"wrote {doctor.write(target, tools)}")
        changed = manifest.drift(target)
        for rel, state in changed:
            print(f"drift: {rel} ({state}); `house adapt {args.name}` keeps your edit as {rel}.orig")
        if not changed and manifest.load(target) is not None:
            print("generated files: in sync")
        if args.context:
            print("\n" + context.format_report(context.measure(target.resolve(), Path.home())))
    return 0


def _print_report(rep: checks.CheckReport) -> None:
    for r in rep.results:
        if rep.kinds[r.id] == "manual":
            print(f"MANUAL {r.id} {'ok' if r.passes else 'pending'}")
        elif r.passes:
            print(f"PASS {r.id}")
        else:
            detail = "timeout" if r.exit is None else f"exit {r.exit}"
            print(f"FAIL {r.id} ({detail})")
            for line in r.output_tail.splitlines()[-5:]:
                print(f"    {line}")
    if not rep.init_ok:
        print("FAIL init.sh")
    if rep.mismatch:
        print("criteria changed since freeze (house check --refreeze --reason ...)")


def cmd_check(args) -> int:
    target = contest_dir(args.name)
    house = target / ".house"
    if not house.is_dir():
        print(f"error: {target} is not a house contest", file=sys.stderr)
        return 1
    if args.all:
        ok = True
        for path in sorted((house / "tasks").glob("T*-*.md")):
            tid = path.name.split("-", 1)[0]
            if checks.load_task(house, tid).meta.get("status") not in ("merged", "done"):
                continue
            rep = checks.run_checks(house, tid, target)
            print(f"{tid} {'GREEN' if rep.green else 'RED'}")
            if not rep.green:
                _print_report(rep)
            ok = ok and rep.green
        return 0 if ok else 1
    if not args.task:
        print("error: give a task id (T###) or --all", file=sys.stderr)
        return 2
    if (args.refreeze and not args.reason) or (args.manual and not args.evidence):
        print("error: --refreeze needs --reason, --manual needs --evidence", file=sys.stderr)
        return 2
    if args.freeze:
        print(f"froze {checks.freeze(house, args.task)}")
        return 0
    if args.refreeze:
        print(f"refroze {args.task}, logged {checks.refreeze(house, args.task, args.reason)}")
        return 0
    if args.manual:
        checks.record_manual(house, args.task, args.manual, args.evidence)
        print(f"recorded {args.task} {args.manual}: {args.evidence}")
        return 0
    rep = checks.run_checks(house, args.task, Path(args.worktree).resolve() if args.worktree else target)
    _print_report(rep)
    print("GREEN" if rep.green else "RED")
    return 0 if rep.green else 1


def cmd_merge(args) -> int:
    target = contest_dir(args.name)
    out = merge.merge_task(target, args.task, Path(args.worktree).resolve() if args.worktree else None)
    print(out.message)
    return 0 if out.ok else 1


def cmd_guard(args) -> int:
    target = contest_dir(args.name)
    house = target / ".house"
    task = checks.load_task(house, args.task)
    branch = str(task.meta.get("branch") or "")
    wt = Path(args.worktree).resolve() if args.worktree else target / ".worktrees" / branch.removeprefix("task/")
    if not wt.is_dir():
        print(f"error: no worktree at {wt} (pass --worktree)", file=sys.stderr)
        return 2
    found = guard.diff_violations(target, args.task, wt)
    for v in found:
        print(f"VIOLATION {v}")
    print("CLEAN" if not found else f"{len(found)} violation(s)")
    return 1 if found else 0


def cmd_notify(args) -> int:
    target = contest_dir(args.name)
    sent, warnings = notify.send(target, " ".join(args.text))
    for w in warnings:
        print(f"! {w}")
    print(f"sent {len(sent)} message(s)" if sent else "nothing sent (Signal not configured or unreachable)")
    return 0


def cmd_ask(args) -> int:
    target = contest_dir(args.name)
    if args.question:
        text = (target / ".house/QUESTIONS.md").read_text()
        q = next((q for q in questions.parse(text) if q.id == args.question), None)
        if q is None:
            print(f"error: no {args.question} in QUESTIONS.md", file=sys.stderr)
            return 1
        print(f"sent {args.question}" if notify.ask(target, q) else "nothing sent")
        return 0
    asked = notify.ask_new(target)
    print("sent " + ", ".join(asked) if asked else "no new open questions")
    return 0


def _all_contests() -> list[Path]:
    names = registry.load(HOUSE_ROOT / "contests.yaml")
    return [HOUSE_ROOT / "contests" / n for n in names if (HOUSE_ROOT / "contests" / n / ".house").is_dir()]


def cmd_inbox(args) -> int:
    from .signal import from_config
    everyone = _all_contests()
    scope = [contest_dir(args.name)] if args.name else None
    if scope and scope[0].resolve() not in {c.resolve() for c in everyone}:
        everyone.append(scope[0])
    for line in inbox.process(everyone, from_config(), scope=scope) or ["no new answers"]:
        print(line)
    return 0


def cmd_deadlines(args) -> int:
    from .signal import from_config
    sig = from_config()
    contests = [contest_dir(args.name)] if args.name else inbox.running(_all_contests())
    lines = [line for c in contests for line in inbox.deadlines(c, sig)]
    for line in lines or ["nothing due"]:
        print(line)
    return 0


def cmd_new(args) -> int:
    try:
        path = scaffold.new_contest(args.name, args.repo)
    except (ValueError, FileExistsError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"created {path}")
    print(f"next: bin/house adapt {args.name}")
    return 0


def cmd_adapt(args) -> int:
    target = contest_dir(args.name)
    if not (target / ".house").is_dir():
        print(f"error: {target} is not a house contest (run: bin/house new {args.name})", file=sys.stderr)
        return 1
    res = adapters.adapt(target)
    for path in res.written:
        print(f"wrote {path.relative_to(target)}")
    for warning in res.warnings:
        print(f"! {warning}")
    return 0


def cmd_sync(args) -> int:
    entries = registry.load(HOUSE_ROOT / "contests.yaml")
    for name in registry.sync(HOUSE_ROOT):
        print(f"cloned {name}")
    for name, url in entries.items():
        if not url:
            print(f"local only (no URL in contests.yaml): {name}")
    return 0


def cmd_export(args) -> int:
    target = contest_dir(args.name)
    if not (target / ".git").exists():
        print(f"error: {target} is not a contest repo", file=sys.stderr)
        return 1
    _, excluded = export.plan(target)
    try:
        files = export.export(target, Path(args.dest).resolve(), force=args.force)
    except export.ExportBlocked as e:
        print(f"blocked: {e}\nfix the files or re-run with --force if these are false positives", file=sys.stderr)
        return 1
    except FileExistsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"exported {len(files)} files to {args.dest}")
    if excluded:
        print("excluded: " + ", ".join(excluded))
    return 0


def cmd_yc(args) -> int:
    hits = yc.search(yc.load(), " ".join(args.query), limit=args.limit)
    if not hits:
        print("no YC companies match")
    for c in hits:
        print(yc.format_hit(c))
    return 0


def cmd_setup(args) -> int:
    for name in setup.install_user_skills():
        print(f"✓ skill {name} → ~/.claude/skills/{name}")
    remotion = setup.fetch_remotion_skill()
    if remotion:
        print(f"✓ skill {remotion} → ~/.claude/skills/{remotion} (fetched from {setup.REMOTION_REPO})")
        print(f"  note: {setup.REMOTION_LICENSE_NOTE}")
    else:
        print(f"! skill {setup.REMOTION_SKILL} skipped (offline and no cache); re-run `house setup` when online")
    tools = doctor.detect()
    print(doctor.summary(tools))
    try:
        from .gitutil import git
        git(HOUSE_ROOT, "config", "--local", "user.email")
    except RuntimeError:
        print("! set your git identity in this repo: git config user.name \"…\"; git config user.email \"…\"")
    todo = setup.hints(tools)
    if todo:
        print("\nnext steps:")
        for line in todo:
            print(f"  - {line}")
    print("\nthen: bin/house sync && bin/house adapt <contest>")
    return 0


def cmd_adopt(args) -> int:
    link = scaffold.adopt(args.name, Path(args.path))
    print(f"adopted {link.resolve()} as {link}")
    print("added .house/ (nothing committed). Choose the branch, then:")
    print(f"  bin/house adapt {args.name}   # generates AGENTS.md, CLAUDE.md, .claude/ and commits them on the CURRENT branch")
    print("  note: teammates' tools read AGENTS.md/CLAUDE.md automatically if these land on a shared branch")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="house", description="AI Software House toolkit")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("doctor", help="detect available tools (claude, codex, ollama, gpu, ...)")
    d.add_argument("name", nargs="?", help="contest name: also write .house/tools.json")
    d.add_argument("--context", action="store_true",
                   help="estimate the always-loaded context of the contest (AGENTS.md, skills, agents)")
    d.set_defaults(func=cmd_doctor)

    ck = sub.add_parser("check", help="run a task's frozen acceptance checks (or freeze / refreeze / record manual)")
    ck.add_argument("name")
    ck.add_argument("task", nargs="?", help="task id, e.g. T001")
    ck.add_argument("--worktree", help="run in this checkout (default: the contest checkout)")
    ck.add_argument("--all", action="store_true", help="every merged/done task, in the contest checkout")
    mode = ck.add_mutually_exclusive_group()
    mode.add_argument("--freeze", action="store_true", help="freeze the task's acceptance criteria")
    mode.add_argument("--refreeze", action="store_true", help="re-freeze changed criteria (needs --reason)")
    mode.add_argument("--manual", metavar="AC-n", help="record a manual criterion as passed (needs --evidence)")
    ck.add_argument("--reason")
    ck.add_argument("--evidence")
    ck.set_defaults(func=cmd_check)

    mg = sub.add_parser("merge", help="gate: check a task branch, merge it, re-check main, revert on regression")
    mg.add_argument("name")
    mg.add_argument("task", help="task id, e.g. T001")
    mg.add_argument("--worktree", help="the task's worktree (default: .worktrees/<branch without task/>)")
    mg.set_defaults(func=cmd_merge)

    gd = sub.add_parser("guard", help="guard rules after the fact: `guard diff <contest> T###` checks a task branch")
    gd.add_argument("action", choices=["diff"])
    gd.add_argument("name")
    gd.add_argument("task", help="task id, e.g. T001")
    gd.add_argument("--worktree", help="the task's worktree (default: .worktrees/<branch without task/>)")
    gd.set_defaults(func=cmd_guard)

    nt = sub.add_parser("notify", help="send a message to the owner (Signal Note to Self) and the contest group")
    nt.add_argument("name")
    nt.add_argument("text", nargs="+")
    nt.set_defaults(func=cmd_notify)

    ak = sub.add_parser("ask", help="send open questions from QUESTIONS.md (new ones, or one by id) to Signal")
    ak.add_argument("name")
    ak.add_argument("question", nargs="?", help="Q-### to (re)send")
    ak.set_defaults(func=cmd_ask)

    ib = sub.add_parser("inbox", help="read answers from Signal (quote-replies) into QUESTIONS.md / DECISIONS.md")
    ib.add_argument("name", nargs="?", help="one contest (default: every running contest)")
    ib.set_defaults(func=cmd_inbox)

    dl = sub.add_parser("deadlines", help="remind about / apply defaults of critical questions past their deadline")
    dl.add_argument("name", nargs="?")
    dl.set_defaults(func=cmd_deadlines)

    n = sub.add_parser("new", help="scaffold a contest repo in contests/<name>")
    n.add_argument("name")
    n.add_argument("--repo", help="clone this git URL instead of git init")
    n.set_defaults(func=cmd_new)

    a = sub.add_parser("adapt", help="generate Claude Code / Codex files for a contest")
    a.add_argument("name")
    a.set_defaults(func=cmd_adapt)

    e = sub.add_parser("export", help="clean public copy of a contest (no .house, agent configs, secrets)")
    e.add_argument("name")
    e.add_argument("dest")
    e.add_argument("--force", action="store_true", help="export even if secret scan finds hits")
    e.set_defaults(func=cmd_export)

    s = sub.add_parser("sync", help="clone contests listed in contests.yaml that are missing locally")
    s.set_defaults(func=cmd_sync)

    st = sub.add_parser("setup", help="prepare this machine: user skills, tool check, next steps")
    st.set_defaults(func=cmd_setup)

    ad = sub.add_parser("adopt", help="attach an existing contest repo in place (symlink in contests/, adds .house/)")
    ad.add_argument("name")
    ad.add_argument("path")
    ad.set_defaults(func=cmd_adopt)

    y = sub.add_parser("yc", help="search the Y Combinator company directory (prior art / market check)")
    y.add_argument("query", nargs="+", help="what the idea does, in plain words")
    y.add_argument("--limit", type=int, default=15)
    y.set_defaults(func=cmd_yc)
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, RuntimeError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
