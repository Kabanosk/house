"""`house` command line.

Used by: the owner and every agent role through `bin/house <command>`. Commands: doctor, check, merge, guard,
notify, ask, inbox, deadlines, night, new, adapt, export, sync, setup, adopt, yc. `<contest>` is the contest
name (lowercase words with single hyphens), resolved to `contests/<name>` under the house root.

Rules an agent should know:
- Task status flow: todo → doing → review → merged → done, or blocked. Coders set `review`; only `house merge`
  sets `merged`/`done`. Acceptance criteria are frozen (`house check --freeze`) and only changed through
  `--refreeze --reason`.
- Exit codes: 0 success, 1 failure or refusal (RED check, refused merge, guard violations, preflight problems,
  not a house contest, any ValueError/RuntimeError/OSError reported as `error: ...` on stderr), 2 usage error
  (also argparse's own). `cmd_*` docstrings give the exact codes per command.
"""
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
    night,
    notify,
    questions,
    registry,
    scaffold,
    setup,
    yc,
)


def contest_dir(name: str) -> Path:
    """Resolve a contest name to its directory.

    Args:
        name: Contest name, lowercase words/numbers separated by single hyphens.

    Returns:
        `<house root>/contests/<name>`.

    Raises:
        ValueError: The name is not valid (e.g. contains "..", "/" or uppercase letters).
    """
    scaffold.validate_name(name)  # raises ValueError: never resolve "..", "/x", etc.
    return HOUSE_ROOT / "contests" / name


def cmd_doctor(args) -> int:
    """Implement `house doctor [name] [--context]`: detect tools; with a contest name also write `.house/tools.json`.

    Args:
        args: Parsed arguments: `name` (contest name or None) and `context` (bool, needs `name`).

    Returns:
        0 on success; 1 for `--context` without a name, or when `name` is not a house contest. Also prints Signal
        status, alias collisions among running contests, and drift of generated files.
    """
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
    """Print one line per criterion (PASS, FAIL with the last output lines, MANUAL) and init/freeze problems."""
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
    """Implement `house check <name> [T###] [--worktree DIR] [--all] [--freeze | --refreeze --reason R |
    --manual AC-n --evidence E]`.

    Args:
        args: Parsed arguments: `name`, `task`, `worktree`, `all`, `freeze`, `refreeze`, `manual`, `reason`,
            `evidence`. Plain form runs the frozen criteria (in `--worktree`, default the contest checkout).
            `--all` re-checks every merged/done task in the contest checkout.

    Returns:
        0 GREEN, or the freeze/refreeze/manual action succeeded; 1 RED (any criterion, init.sh or changed
        criteria) or the directory is not a house contest; 2 usage error (no task id and no `--all`;
        `--refreeze` without `--reason`; `--manual` without `--evidence`). Errors such as a missing or already
        frozen task are raised and reported by `main` with exit code 1.
    """
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
    """Implement `house merge <name> T### [--worktree DIR]`: run the merge gate for a task in `review`.

    Args:
        args: Parsed arguments: `name`, `task` and `worktree` (default `.worktrees/<branch without task/>`).

    Returns:
        0 when merged (the message names the final status, `merged` or `done`); 1 when refused or reverted; the
        message explains why.
    """
    target = contest_dir(args.name)
    out = merge.merge_task(target, args.task, Path(args.worktree).resolve() if args.worktree else None,
                           accept_review=args.accept_review)
    print(out.message)
    return 0 if out.ok else 1


def cmd_review(args) -> int:
    """Implement `house review <name> T###` / `house review <name> --since REF|last`.

    Args:
        args: Parsed arguments: `name`, optional `task`, optional `since`.

    Returns:
        0 when a review came back (findings are in the report, BLOCKERs included); 1 when the review failed
        (timeout, empty answer, no VERDICT line); 2 on bad arguments.
    """
    from . import review
    target = contest_dir(args.name)
    if bool(args.task) == bool(args.since):
        print("error: give a task id or --since REF|last", file=sys.stderr)
        return 2
    res = review.review_task(target, args.task) if args.task else review.review_since(target, args.since)
    f = res.findings
    print(f"reviewer: {res.reviewer} ({res.note})")
    if not res.ok:
        print(f"review failed: {res.why}")
        return 1
    print(f"BLOCKER {f.blockers} · MAJOR {f.majors} · MINOR {f.minors} · NIT {f.nits} · verdict: {f.verdict}")
    print(f"report: {res.report}")
    return 0


def cmd_guard(args) -> int:
    """Implement `house guard diff <name> T### [--worktree DIR]`: check a task branch against the guard rules.

    Args:
        args: Parsed arguments: `action` (only "diff"), `name`, `task`, `worktree` (default
            `.worktrees/<branch without task/>`).

    Returns:
        0 when CLEAN; 1 when violations were printed as `VIOLATION ...`; 2 when the worktree does not exist.
    """
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
    """Implement `house notify <name> <text...>`: send a message to the owner and the contest's Signal group.

    Args:
        args: Parsed arguments: `name` and `text` (list of words, joined with spaces).

    Returns:
        0 always, also when Signal is not configured (then "nothing sent" is printed).
    """
    target = contest_dir(args.name)
    sent, warnings = notify.send(target, " ".join(args.text))
    for w in warnings:
        print(f"! {w}")
    print(f"sent {len(sent)} message(s)" if sent else "nothing sent (Signal not configured or unreachable)")
    return 0


def cmd_ask(args) -> int:
    """Implement `house ask <name> [Q-###]`: send open questions from `.house/QUESTIONS.md` to Signal.

    Args:
        args: Parsed arguments: `name` and `question`. With an id that question is (re)sent; without one all new
            open questions are sent.

    Returns:
        0 on success (including nothing to send); 1 when the given question id is not in QUESTIONS.md.
    """
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
    """List contests registered in contests.yaml that exist locally and have a `.house/` directory."""
    names = registry.load(HOUSE_ROOT / "contests.yaml")
    return [HOUSE_ROOT / "contests" / n for n in names if (HOUSE_ROOT / "contests" / n / ".house").is_dir()]


def cmd_inbox(args) -> int:
    """Implement `house inbox [name]`: read Signal quote-replies into QUESTIONS.md / DECISIONS.md.

    Args:
        args: Parsed arguments: `name`, a single contest to process; None means every running contest.

    Returns:
        0 always; prints what was applied, or "no new answers".
    """
    from .signal import from_config
    everyone = _all_contests()
    scope = [contest_dir(args.name)] if args.name else None
    if scope and scope[0].resolve() not in {c.resolve() for c in everyone}:
        everyone.append(scope[0])
    for line in inbox.process(everyone, from_config(), scope=scope) or ["no new answers"]:
        print(line)
    return 0


def cmd_deadlines(args) -> int:
    """Implement `house deadlines [name]`: remind about, or apply defaults of, critical questions past their deadline.

    Args:
        args: Parsed arguments: `name`, one contest; None means every running contest.

    Returns:
        0 always; prints what was done, or "nothing due".
    """
    from .signal import from_config
    sig = from_config()
    contests = [contest_dir(args.name)] if args.name else inbox.running(_all_contests())
    lines = [line for c in contests for line in inbox.deadlines(c, sig)]
    for line in lines or ["nothing due"]:
        print(line)
    return 0


def cmd_night(args) -> int:
    """Implement `house night <name> [--hours H] [--max-tasks N] [--dry-run]`: unattended run over ready tasks.

    Args:
        args: Parsed arguments: `name`, `hours` (float, default 8), `max_tasks` (int or None), `dry_run`
            (preflight and list ready tasks only).

    Returns:
        0 when the night ran (or the dry run passed); 1 when preflight found problems (nothing started, the
        owner is notified on Signal).
    """
    target = contest_dir(args.name)
    problems, warnings = night.preflight(target, args.hours)
    for w in warnings:
        print(f"! {w}")
    for p in problems:
        print(f"✗ {p}")
    if problems:
        print("night not started")
        notify.send(target, "🌙 noc nie wystartowała: " + "; ".join(problems)[:600])
        return 1
    if args.dry_run:
        ready = night.pick_ready(target / ".house", set(), 99)
        print("preflight OK; ready now: " + (", ".join(ready) if ready else "none"))
        return 0
    rep = night.run_night(target, hours=args.hours, max_tasks=args.max_tasks)
    merged = sum(a.outcome == "merged" for a in rep.attempts)
    print(f"night over ({rep.stopped_because}): {merged} merged, {len(rep.attempts) - merged} failed attempts")
    print(f"report: {rep.report_path}")
    return 0


def cmd_new(args) -> int:
    """Implement `house new <name> [--repo URL]`: scaffold a contest repo in `contests/<name>`.

    Args:
        args: Parsed arguments: `name` and `repo` (git URL to clone instead of `git init`, or None).

    Returns:
        0 when created; 1 on an invalid name, an existing contest or a git failure.
    """
    try:
        path = scaffold.new_contest(args.name, args.repo)
    except (ValueError, FileExistsError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"created {path}")
    print(f"next: bin/house adapt {args.name}")
    return 0


def cmd_adapt(args) -> int:
    """Implement `house adapt <name>`: generate the Claude Code and Codex files for a contest.

    Args:
        args: Parsed arguments: `name`.

    Returns:
        0 on success; 1 when the contest has no `.house/` (run `house new`/`house adopt` first).
    """
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
    """Implement `house sync`: clone contests listed in contests.yaml that are missing locally.

    Args:
        args: Parsed arguments (none used).

    Returns:
        0 always; contests without a URL are reported as local only.
    """
    entries = registry.load(HOUSE_ROOT / "contests.yaml")
    for name in registry.sync(HOUSE_ROOT):
        print(f"cloned {name}")
    for name, url in entries.items():
        if not url:
            print(f"local only (no URL in contests.yaml): {name}")
    return 0


def cmd_export(args) -> int:
    """Implement `house export <name> <dest> [--force]`: write a clean public copy of a contest.

    Args:
        args: Parsed arguments: `name`, `dest` (empty or missing directory) and `force` (export even when the
            secret scan has hits).

    Returns:
        0 when exported; 1 when the contest is not a repo, `dest` is not empty, or the secret scan blocked it.
    """
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
    """Implement `house yc <query...> [--limit N]`: search the Y Combinator company directory.

    Args:
        args: Parsed arguments: `query` (list of words) and `limit` (default 15).

    Returns:
        0 always; prints matching companies, or "no YC companies match". Network failures with no cache are
        reported by `main` with exit code 1.
    """
    hits = yc.search(yc.load(), " ".join(args.query), limit=args.limit)
    if not hits:
        print("no YC companies match")
    for c in hits:
        print(yc.format_hit(c))
    return 0


def cmd_setup(args) -> int:
    """Implement `house setup`: install user-level skills, fetch the Remotion skill, check tools, print next steps.

    Args:
        args: Parsed arguments (none used).

    Returns:
        0 always; missing tools and offline skips are reported as hints.
    """
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
    """Implement `house adopt <name> <path>`: attach an existing repo as a contest (symlink, adds `.house/`).

    Args:
        args: Parsed arguments: `name` and `path` (root of an existing git repository). Nothing is committed.

    Returns:
        0 when adopted; invalid names, non-repos and existing links raise and `main` reports them with exit code 1.
    """
    link = scaffold.adopt(args.name, Path(args.path))
    print(f"adopted {link.resolve()} as {link}")
    print("added .house/ (nothing committed). Choose the branch, then:")
    print(f"  bin/house adapt {args.name}   # generates AGENTS.md, CLAUDE.md, .claude/ and commits them on the CURRENT branch")
    print("  note: teammates' tools read AGENTS.md/CLAUDE.md automatically if these land on a shared branch")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with one subcommand per `cmd_*` function.

    Returns:
        The `argparse.ArgumentParser` for `house`; each subparser sets `func` to its handler.
    """
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
    mg.add_argument("--accept-review", metavar="REASON",
                    help="merge a review: always task despite BLOCKER findings (reason logged as D-###)")
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

    nn = sub.add_parser("night", help="work through ready tasks unattended (fresh worker session per task)")
    nn.add_argument("name")
    nn.add_argument("--hours", type=float, default=8)
    nn.add_argument("--max-tasks", type=int)
    nn.add_argument("--dry-run", action="store_true", help="preflight and list ready tasks only")
    nn.set_defaults(func=cmd_night)

    rv = sub.add_parser("review", help="independent review by another model family (Codex ↔ Claude)")
    rv.add_argument("name")
    rv.add_argument("task", nargs="?", help="task id: review its branch")
    rv.add_argument("--since", metavar="REF", help="milestone review of main since REF (`last` = previous review)")
    rv.set_defaults(func=cmd_review)

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
    """Run the `house` command line.

    Args:
        argv: Arguments without the program name, e.g. `["check", "my-contest", "T001"]`.

    Returns:
        The exit code of the command. ValueError, RuntimeError and OSError from a command are printed as
        `error: ...` on stderr and give 1; invalid arguments exit with 2 via argparse.
    """
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, RuntimeError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
