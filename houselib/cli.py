"""`house` command line.

Used by: the owner and every agent role through `bin/house <command>`. Commands: doctor, check, merge, guard,
notify, ask, inbox, signal, deadlines, night, cost, decisions, review, exp, out, sources, eval, new, adapt, export, sync,
setup, adopt, yc.
`<contest>` is the contest name (lowercase words with single hyphens), resolved to `contests/<name>` under the
house root.

Rules an agent should know:
- Task status flow: todo → doing → review → merged → done, or blocked. Coders set `review`; only `house merge`
  sets `merged`/`done`. Acceptance criteria are frozen (`house check --freeze`) and only changed through
  `--refreeze --reason`.
- Exit codes: 0 success, 1 failure or refusal (RED check, refused merge, guard violations, preflight problems,
  not a house contest, any ValueError/RuntimeError/OSError reported as `error: ...` on stderr), 2 usage error
  (also argparse's own), 3 inconclusive (`house eval run` only: a regression case was not measured).
  `cmd_*` docstrings give the exact codes per command.
"""
import argparse
import contextlib
import ctypes
import importlib.util
import os
import re
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from . import (
    HOUSE_ROOT,
    adapters,
    agentdocker,
    attempts,
    checks,
    context,
    cost,
    decisionlog,
    doctor,
    evidence,
    exp,
    export,
    githook,
    guard,
    inbox,
    locks,
    manifest,
    merge,
    night,
    notify,
    outlog,
    questions,
    registry,
    scaffold,
    setup,
    signalcli,
    sources,
    spec,
    transcript,
    yc,
)
# The eval harness is absent from the house export an eval agent works in (`evals.sandbox.EXPORT_EXCLUDE`): there
# every other command must still run, and `house eval` says it is not available (`cmd_eval`).
if importlib.util.find_spec(f"{__package__}.evals") is not None:
    from .evals import cases as evalcases
    from .evals import fixtures as evalfixtures
    from .evals import grade as evalgrade
    from .evals import results as evalresults
    from .evals import runner as evalrunner
    from .evals import sandbox as evalsandbox
else:  # pragma: no cover - only in an eval export (tested through its `bin/house`)
    evalcases = evalfixtures = evalgrade = evalresults = evalrunner = evalsandbox = None


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
        problems = githook.install(target, write=False).problems
        for problem in problems:
            print(f"! {problem}")
        if not problems:
            print("git hooks: pre-commit, pre-push installed")
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

    Every run that enters the gate (the task was in `review` and its worktree exists before the call) adds one
    `merge` row to the attempt register `.house/attempts.jsonl` (outcome `merged`, `reverted` or `merge-refused`;
    `base` = merge-base of the task branch with HEAD, `head` = the branch head, both read before merging; `tool` is
    `codex` for a `coder-codex` task, else `claude`; `questions` is `[]`; the number is allocated under the register
    lock) and commits that file alone, so other uncommitted `.house/` state stays as the merge left it. Refusals
    before the gate (a task in `todo`, `merged` or `done`, no worktree) record nothing. A recording failure is
    reported on stderr and never changes the exit code.

    Args:
        args: Parsed arguments: `name`, `task` and `worktree` (default `.worktrees/<branch without task/>`).

    Returns:
        0 when merged (the message names the final status, `merged` or `done`); 1 when refused or reverted; the
        message explains why.
    """
    target = contest_dir(args.name)
    worktree = Path(args.worktree).resolve() if args.worktree else None
    try:
        task = checks.load_task(target / ".house", args.task)
        agent, branch = str(task.meta.get("agent") or "coder"), str(task.meta.get("branch") or "")
        wt = worktree or (target / ".worktrees" / branch.removeprefix("task/") if branch else None)
        gate = task.meta.get("status") == "review" and wt is not None and wt.is_dir()
    except (ValueError, FileNotFoundError, OSError):
        agent, branch, gate = "", "", False  # no such task: the gate refuses it and there is nothing to register
    head = night.rev(target, branch) if branch else ""
    base = night.merge_base(target, branch) if head else ""
    start = night.now_iso()
    out = merge.merge_task(target, args.task, worktree, accept_review=args.accept_review)
    print(out.message)
    if agent and gate:
        try:
            row = attempts.Row(task=args.task, n=0, source="merge", agent=agent,
                               tool="codex" if agent == "coder-codex" else "claude", start=start,
                               end=night.now_iso(), base=base, head=head,
                               outcome="merged" if out.ok else "reverted" if out.reverted else "merge-refused",
                               message=out.message, questions=[], reason="conflict" if out.conflict else "")
            night.record_attempt(target, row)  # n=0: the next free number, allocated under the register lock
        except Exception as e:  # noqa: BLE001 — recording never changes the merge exit code
            print(f"house: attempt register not updated: {e!r}", file=sys.stderr)
    return 0 if out.ok else 1


def cmd_evidence(args) -> int:
    """Implement `house evidence <name> T### [--attempt N]` / `house evidence <name> --session ID`.

    Prints facts about one attempt (default: the task's last one in the register) or about an interactive session
    of the contest, to stdout only. Nothing is written or stored.

    Args:
        args: Parsed arguments: `name`, optional `task`, `attempt` (None = the last) and `session` (an id or a
            unique id prefix of an interactive session of the contest).

    Returns:
        0 with the facts printed; 1 when the task, attempt or session is unknown (or the prefix is ambiguous); 2 on
        bad arguments (a task and `--session` together, neither of them, `--attempt` without a task).
    """
    if bool(args.task) == bool(args.session) or (args.session and args.attempt is not None):
        print("error: give a task (with optional --attempt N) or --session ID, not both or neither", file=sys.stderr)
        return 2
    target = contest_dir(args.name)
    if args.session:
        found = [f for f in transcript.contest_sessions(target) if f.stem == args.session]
        found = found or [f for f in transcript.contest_sessions(target) if f.stem.startswith(args.session)]
        if not found:
            print(f"error: no interactive session '{args.session}' in contest {args.name}", file=sys.stderr)
            return 1
        if len(found) > 1:
            print(f"error: session id '{args.session}' is ambiguous ({len(found)} sessions)", file=sys.stderr)
            return 1
        facts = evidence.session_facts(transcript.claude_session(found[0]).events)
        print(f"session {guard.redact(found[0].stem)}")
        print(evidence.render_full(facts, None))
        return 0
    house = target / ".house"
    if args.attempt is None:
        row = max(attempts.rows(house, args.task), key=lambda r: r.n, default=None)
    else:
        row = attempts.find(house, args.task, args.attempt)
    if row is None:
        what = f"task {args.task}" if args.attempt is None else f"attempt {args.attempt} of task {args.task}"
        print(f"error: no {what} in the attempt register of contest {args.name}", file=sys.stderr)
        return 1
    print(evidence.render_full(evidence.log_facts(target, row, evidence.state_facts(target, row)), row))
    return 0


def cmd_cost(args) -> int:
    """Implement `house cost <name> [--since YYYY-MM-DD]`.

    Prints one row per session of the contest (interactive, night worker, Signal job) with calls, context sizes,
    token buckets, compactions, the tokens outside the record and a weighted cost, to stdout only. Weights come from
    `cost.weights` in the contest's `spec.yaml` (a warning line on stderr when a value is bad).

    Args:
        args: Parsed arguments: `name` and `since` (a `YYYY-MM-DD` string or None; argparse rejects a bad date).

    Returns:
        0 with the table, or `no sessions` when there is nothing to show; 1 when `name` is not a house contest;
        2 (argparse) for a bad `--since`.
    """
    target = contest_dir(args.name)
    if not (target / ".house").is_dir():
        print(f"error: {target} is not a house contest", file=sys.stderr)
        return 1
    weights, warning = cost.weights_from_spec(spec.load_spec(target / ".house"))
    if warning:
        print(warning, file=sys.stderr)
    print(cost.render(cost.rows(target, since=args.since, weights=weights), weights))
    return 0


def _since_date(text: str) -> str:
    """argparse `type` for `--since`: a `YYYY-MM-DD` date, else a usage error (exit 2)."""
    try:
        return cost.parse_since(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def cmd_decisions(args) -> int:
    """Implement `house decisions [--contest C] [--exclude C]... [--by human|agent] [--since YYYY-MM-DD] [--grep TEXT]
    [--all] [--full]`.

    Prints one line per decision of the contests in `contests.yaml` (`<contest> D-### <date> <who>: <title> —
    <path>:<line>`, path relative to the house root), to stdout only. Superseded entries are hidden unless `--all`;
    a human entry that only an agent entry names in `Zastępuje` stays listed with `[podważona przez D-### (agent)]`.
    `--exclude C` (repeatable) leaves out a contest, e.g. the current one.
    A contest with no directory or no `.house/DECISIONS.md` gives one `note:` line and is skipped. See
    `houselib/decisionlog.py`.

    Args:
        args: Parsed arguments: `contest`, `exclude`, `by`, `since` (argparse rejects a bad date), `grep`, `all`, `full`.

    Returns:
        0 (`no decisions` when nothing matches); 1 when `--contest` is not registered; 2 (argparse) for bad
        arguments.
    """
    notes: list[str] = []
    found = decisionlog.entries(HOUSE_ROOT, args.contest, notes)
    chosen = decisionlog.select(found, by=args.by, since=args.since, grep=args.grep, all=args.all,
                                exclude=args.exclude)
    text = decisionlog.render(chosen, full=args.full, superseded=decisionlog.superseded_by(found),
                              challenged=decisionlog.challenged_by(found))
    print("\n".join([*notes, text or "no decisions"]))
    return 0


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


def cmd_exp(args) -> int:
    """Implement `house exp new|run|status|prune <name> ...` (experiment loop for optimize contests).

    `new [--from REF] [--idea I] [--note T]` prints `E-### <worktree>`. `run [ID | --from REF] [--budget B]
    [--idea I] [--note T] -- CMD...` runs a created experiment (ID) or creates and runs one (`--from`); it prints
    `E-### <status> <score>`, then the parent score, the note and the log path. `status [-n N]` prints the
    tree summary. `prune [--keep N]` removes worktrees of keeps outside the top N and prints their ids.

    Args:
        args: Parsed arguments: `action`, `name`, `id`, `from_`, `idea`, `note`, `budget`, `n`, `keep`, and
            `exp_command` (everything after `--`, split off in `main`).

    Returns:
        `run`: 0 keep, 1 revert/crash/timeout. Others: 0. Any action: 2 on bad arguments (missing or doubled
        ID/--from, no command after `--`, invalid budget).
    """
    target = contest_dir(args.name)
    if args.action == "new":
        eid, wt = exp.new(target, args.from_ or "best", idea=args.idea or "", note=args.note or "")
        print(f"{eid} {wt}")
        return 0
    if args.action == "status":
        print(exp.status(target, last=args.n), end="")
        return 0
    if args.action == "prune":
        removed = exp.prune(target, keep=args.keep)
        print("removed: " + (", ".join(removed) if removed else "nothing"))
        return 0
    command = getattr(args, "exp_command", None) or []
    if bool(args.id) == bool(args.from_) or not command:
        print("error: give an experiment id or --from REF, and the command after `--`", file=sys.stderr)
        return 2
    try:
        budget = exp.parse_budget(args.budget) if args.budget else None
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.id:
        res = exp.run(target, args.id, command, budget)
    else:
        res = exp.run_new(target, args.from_, command, budget, idea=args.idea or "", note=args.note or "")
    print(f"{res.id} {res.status} {'-' if res.score is None else res.score}")
    print(f"parent score: {'-' if res.parent_score is None else res.parent_score}"
          + (f" · {res.note}" if res.note else ""))
    print(f"log: {res.log}")
    return 0 if res.status == "keep" else 1


def cmd_out(args) -> int:
    """Implement `house out -- CMD…`, `house out show <id|E-###> --grep P [--regex]` and `house out prune [--days N]`.

    `-- CMD…` runs the command with its whole output in `.house/tmp/out/<id>.log` and prints a short envelope (exit
    code, bytes, lines, duration, last lines, the `show` command to use). SIGTERM and SIGHUP act like Ctrl-C: the
    command's process group is stopped and the envelope is still printed. The log goes to the main contest
    (`HOUSE_DIR`, else the contest of the current directory, also from a worker worktree).

    Args:
        args: Parsed arguments: `action` (None, `show` or `prune`), `ref`, `grep`, `regex`, `days`, and
            `out_command` (everything after `--`, split off in `main`).

    Returns:
        `--`: the command's exit code (128+n on a signal, 128+n after an interrupt by signal n: 130 Ctrl-C, 143
        SIGTERM, 129 SIGHUP; 127 not found, 126 not executable).
        `show`/`prune`: 0. Any action: 2 on bad arguments (no command, bad id, unknown log, invalid regex, negative
        days), 1 when there is no house contest.
    """
    command = getattr(args, "out_command", None) or []
    if args.action is None and not command:
        print("error: give the command after `--` (house out -- CMD…), or use `out show` / `out prune`",
              file=sys.stderr)
        return 2
    try:
        contest = outlog.resolve_contest(Path.cwd())
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    try:
        if args.action == "show":
            print(outlog.show(contest, args.ref, args.grep, regex=args.regex))
            return 0
        if args.action == "prune":
            print(f"removed: {outlog.prune(contest, args.days)}")
            return 0
        code, envelope = outlog.run(command, contest)  # handles SIGINT/SIGTERM/SIGHUP itself
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(envelope)
    return code


def cmd_sources(args) -> int:
    """Implement `house sources <contest>` and `house sources add <contest> <file|-> [--name N] [--replace]`.

    The listing is the table of contents of `.house/sources/` (sizes, headings with line numbers, warnings), so
    agents read contest documents by line range. `add` copies a file (or stdin bytes for `-`, at most
    `sources.STDIN_MAX` = 1 MB, nothing is written beyond that) there byte for byte
    and records it in `index.json`; it prints one line (name, bytes, lines, short sha256) and does not commit.

    Args:
        args: Parsed arguments: `words` (`[contest]` or `["add", contest, file]`), `name` and `replace`.

    Returns:
        0 on success; 1 when the contest is unknown or not a house contest; 2 for bad arguments (bare `add` is a
        usage error unless `contests/add/.house` exists), stdin over the limit, a bad or taken name (without `--replace`), a symlink or missing source, or a corrupt index.
    """
    words = args.words
    if words == ["add"] and not (contest_dir("add") / ".house").is_dir():  # `add` alone is a usage error
        print("usage: house sources add <contest> <file|-> [--name N] [--replace]", file=sys.stderr)
        return 2
    adding = words[0] == "add" and len(words) != 1  # unless a contest named "add" exists, then it is listed
    expected = 3 if adding else 1
    if len(words) != expected:
        print("usage: house sources <contest>  |  house sources add <contest> <file|-> [--name N] [--replace]",
              file=sys.stderr)
        return 2
    try:
        target = contest_dir(words[1] if adding else words[0])
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not (target / ".house").is_dir():
        print(f"error: {target} is not a house contest", file=sys.stderr)
        return 1
    try:
        if not adding:
            print(sources.listing(target))
            return 0
        if words[2] == "-":
            data = sys.stdin.buffer.read(sources.STDIN_MAX + 1)
            if len(data) > sources.STDIN_MAX:
                print("error: stdin larger than 1 MB; save it to a file and run "
                      f"house sources add {words[1]} <file>", file=sys.stderr)
                return 2
            entry = sources.add(target, data=data, name=args.name, replace=args.replace)
        else:
            entry = sources.add(target, src=Path(words[2]), name=args.name, replace=args.replace)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"added {entry['name']}: {entry['bytes']} bytes, {entry['lines']} lines, sha256 {entry['sha256'][:12]}")
    print("note: not committed; .house/sources/ is committed with .house/ as usual")
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
    from .signalread import reader_name
    for line in inbox.process(everyone, from_config(), scope=scope, reader=reader_name(Path.cwd())) or \
            ["no new answers"]:
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
        owner is notified on Signal) or another night holds `night.lock` (see `locks`; nothing started).

    Night does not look at `eval.lock`, by design (spec): the exclusion is one-sided. `house eval run` takes
    `eval.lock` first and then refuses while `night.lock` is held or a contest is in progress, so an eval never
    starts over a running night; a night that starts while an eval runs is the owner's decision (`house eval run`
    has no way to stop it, and the eval's temp roots share nothing with the night's contest).
    """
    target = contest_dir(args.name)
    problems, warnings = night.preflight(target, args.hours)
    for w in warnings:
        print(f"! {w}")
    for p in problems:
        print(f"✗ {p}")
    if problems:
        print("night not started")
        notify.send(target, "🌙 noc nie wystartowała: " + "; ".join(problems)[:600], origin="night")
        return 1
    if args.dry_run:
        ready = night.pick_ready(target / ".house", set(), 99)
        print("preflight OK; ready now: " + (", ".join(ready) if ready else "none"))
        return 0
    try:
        with locks.hold("night"):  # one night at a time on this machine, whatever the contest
            rep = night.run_night(target, hours=args.hours, max_tasks=args.max_tasks)
    except locks.Busy as e:
        print(f"✗ {e}; refusing to start a second night", file=sys.stderr)
        return 1
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


def cmd_lb(args) -> int:
    """Implement `house lb poll [--force] [name]`, `house lb show <name>`, `house lb agorize <url> [--sort]`.

    `poll` fetches every due contest with `leaderboard.cmd` (or just `name`) and prints one line per fetch;
    `show` prints the latest stored board as a table; `agorize` prints an Agorize board as contract JSON.

    Returns:
        0; `poll` gives 1 when any fetch failed, `agorize` raises ValueError (→ 1) on a page without the API.
    """
    import json
    from datetime import datetime
    from . import leaderboard
    if not args.target and args.action != "poll":
        raise ValueError(f"podaj {'URL strony' if args.action == 'agorize' else 'konkurs'}: house lb {args.action} …")
    if args.action == "agorize":
        print(json.dumps(leaderboard.agorize_board(args.target, sort=args.sort), ensure_ascii=False))
        return 0
    if args.action == "show":
        print(leaderboard.summary(contest_dir(args.target), None, None, datetime.now().astimezone()).text)
        return 0
    contests = [contest_dir(args.target)] if args.target else _all_contests()
    now, failed = datetime.now().astimezone(), False
    for c in contests:
        try:
            line = leaderboard.poll(c, now, force=args.force)
        except Exception as e:  # noqa: BLE001 — one broken contest must not stop the others
            line = f"lb {c.name}: błąd: {e}"
        if line:
            print(line)
            failed = failed or ": błąd:" in line
    return 1 if failed else 0


def cmd_setup(args) -> int:
    """Implement `house setup`: install user-level skills, fetch the Remotion skill, check tools, print next steps.

    Args:
        args: Parsed arguments: `signal_hook` (only write the house repo's Signal session hook, then stop).

    Returns:
        0 always; missing tools and offline skips are reported as hints.
    """
    if getattr(args, "signal_timer", False):
        service, timer = setup.install_signal_timer(HOUSE_ROOT)
        print(f"✓ timer Signala: {timer.name} co 5 min (house signal pull + codzienny compact); {service}")
        return 0
    if getattr(args, "lb_timer", False):
        service, timer = setup.install_lb_timer(HOUSE_ROOT)
        print(f"✓ timer rankingów: {timer.name} co 5 min (house lb poll); {service}")
        return 0
    if getattr(args, "signal_hook", False):
        path = adapters.write_house_settings(HOUSE_ROOT)
        print(f"✓ hook Signala w repo house: {path} (podsumowanie nowych wiadomości na starcie sesji, "
              "blokada gołego signal-cli)")
        return 0
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
    from .signal import read_config
    if read_config():
        print("Signal: `house setup --signal-hook` pokaże nowe wiadomości na starcie sesji w repo house")
    print("\nthen: bin/house sync && bin/house adapt <contest>")
    return 0


EVAL_RESULTS = Path("evals/results.jsonl")
EVAL_CASES = Path("evals/cases")
EVAL_COHORT = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")  # `house eval run --cohort`


def _eval_commit(commit: str) -> str:
    """Resolve a commit-ish of the house repo to a full sha (`ValueError` when it names no commit)."""
    done = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}"], cwd=HOUSE_ROOT,
                          capture_output=True, text=True, check=False)
    if done.returncode != 0 or not done.stdout.strip():
        raise ValueError(f"no such commit in the house repo: {commit}")
    return done.stdout.strip()


def _eval_unknown_profile(selected) -> str | None:
    """Return an error text for the first case whose agent or skill cannot run, else None.

    An agent must be an installable profile (`adapters.load_profiles()` without `coordinator`, which is the main
    session and is never written to `.claude/agents/`); a skill must exist in `adapters/claude-code/skills/`.
    """
    installable = None
    for case in selected:
        if case.agent:
            if installable is None:
                try:
                    installable = set(adapters.load_profiles(HOUSE_ROOT)) - {"coordinator"}
                except (OSError, ValueError, KeyError) as exc:
                    return f"cannot read the agent profiles in core/agents/ ({type(exc).__name__})"
            if case.agent == "coordinator":
                return (f"case {case.name}: agent 'coordinator' is not an installable profile (it is the main "
                        f"session); installable: {', '.join(sorted(installable))}")
            if case.agent not in installable:
                return f"case {case.name}: unknown agent {case.agent!r} (no core/agents/{case.agent}.md)"
        if case.skill and not (HOUSE_ROOT / "adapters/claude-code/skills" / case.skill).is_dir():
            return f"case {case.name}: unknown skill {case.skill!r} (no adapters/claude-code/skills/{case.skill}/)"
    return None


def _eval_model_label(case, root, override: str | None) -> str:
    """The model a run used, for the results row: the override, the case's, else the profile's tier (best effort).

    `root` is None when the temp root could not be created (then there is no profile to resolve: `default`).
    """
    if case.tool == "codex":
        return "codex"
    if override or case.model:
        return override or case.model
    if root is None:
        return "default"
    try:
        return evalrunner.resolve_model(case, root, None)
    except Exception:  # noqa: BLE001 — a label only; the run itself reports a real problem as a setup failure
        return "default"


def _eval_line(case, i: int, k: int, g, variant: str | None = None, weighted: int | None = None) -> str:
    """One output line per run: `<case> <i>/<k> <result> <duration>s` plus what failed and a short note.

    With `variant` (a batch of several variants or `--parallel`, where lines arrive in finishing order) the line also
    names the variant, the score (`-` without one) and, when `weighted` is given, the weighted cost K-rounded like
    `house cost`: `<case> <i>/<k> <result> <duration>s variant=<v> score=<s> weighted=<n>K`.
    """
    parts = [f"{case.name} {i}/{k} {g.result} {g.duration:.0f}s"]
    if variant is not None:
        parts.append(f"variant={variant} score={'-' if g.score is None else f'{g.score:.2f}'}")
        if weighted is not None:
            parts.append(f"weighted={cost.format_k(weighted)}")
    for label, items in (("missing", g.missing), ("forbidden", g.forbidden), ("order", g.order_violations),
                         ("failed", g.failed_predicates)):
        if items:
            parts.append(f"{label}: {'; '.join(items)}")
    if g.note:
        parts.append(f"[{g.note}]")
    return " ".join(parts)


def _eval_unknown_fixture(selected) -> str | None:
    """Return an error text for the first case whose fixture builder does not exist, else None."""
    for case in selected:
        name = case.fixture.get("name")
        if name not in evalfixtures.BUILDERS:
            return (f"case {case.name}: unknown fixture {name!r}; known: "
                    f"{', '.join(sorted(evalfixtures.BUILDERS))}")
    return None


def _eval_dirty_warning() -> str | None:
    """A warning when the code directories have uncommitted changes (the eval tests a commit, not the tree)."""
    done = subprocess.run(["git", "status", "--porcelain", "--", "core", "adapters", "houselib", "bin"],
                          cwd=HOUSE_ROOT, capture_output=True, text=True, check=False)
    if done.returncode == 0 and done.stdout.strip():
        return "uncommitted changes are not tested; `--commit` defaults to HEAD (checked: core adapters houselib bin)"
    return None


def _eval_setup_grade(note: str, duration: float = 0.0) -> "evalgrade.Grade":
    """The verdict of a run that could not start because of the case or the commit: `fail`, note `setup: ...`."""
    return evalgrade.Grade("fail", note=guard.redact(f"setup: {note}")[:300], duration=duration)


def _eval_unknown_variant(selected, name: str) -> str | None:
    """Return an error text for the first selected case that has no variant `name` (`default` always exists)."""
    if name == evalcases.DEFAULT_VARIANT:
        return None
    for case in selected:
        if name not in case.variants:
            known = ", ".join([evalcases.DEFAULT_VARIANT, *sorted(case.variants)])
            return f"case {case.name}: unknown variant {name!r}; known: {known}"
    return None


def _eval_once(case, sha: str, model_override: str | None, variant=None) -> tuple["evalgrade.Grade", dict]:
    """Run and grade one case in a fresh temp root.

    Returns:
        `(grade, extra)`; `extra` holds the row fields `model`, `cli`, `eval_events`, `metrics` (the cost of the
        run's stream log, all zero when there is none), `turns_sent` and `turns_ok` (user turns written to the CLI
        and `success` results in its log, `RunResult`) and `audit` (`{"house_reads": RunResult.house_reads}`, 0
        when nothing ran). `variant` is the case's `Variant` to run, None for the
        default (no patch); a patch that cannot be applied is an `infra_error`. A temp root that cannot be
        built, or a run whose profile or model is wrong (`evalrunner.is_setup_error`), is a `fail` with the note
        `setup`, not `infra_error`: the case or the commit is at fault, not the environment. A run whose container
        could not be removed (`run.cleanup_failed`: it may still write the contest) is not graded: `infra_error`
        with the note `container not removed`, and its out-dir and temp root are kept (paths on stderr).
    """
    with contextlib.ExitStack() as stack:
        try:
            root = stack.enter_context(evalsandbox.TempRoot(sha, case.fixture))
        except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as e:
            extra = {"model": _eval_model_label(case, None, model_override),
                     "cli": evalresults.cli_version(Path(os.devnull), case.tool), "eval_events": {},
                     "metrics": evalresults.run_metrics(Path(os.devnull)), "turns_sent": 0, "turns_ok": 0,
                     "audit": {"house_reads": 0}}
            return _eval_setup_grade(str(e)), extra
        run = evalrunner.run_case(case, root, model_override, **({"variant": variant} if variant else {}))
        if run.cleanup_failed:  # the container may still run against the contest: no grading, keep everything
            stack.pop_all()
            print(f"house eval: {case.name}: container {run.leftover or '?'} not removed; kept out-dir {run.out_dir} "
                  f"and temp root {root.tmp} (remove them once the container is gone)", file=sys.stderr)
            extra = {"model": _eval_model_label(case, root, model_override),
                     "cli": evalresults.cli_version(run.log, case.tool), "eval_events": {},
                     "metrics": evalresults.run_metrics(run.log), "turns_sent": run.turns_sent,
                     "turns_ok": run.turns_ok, "audit": {"house_reads": run.house_reads}}
            return evalgrade.Grade("infra_error", note="container not removed", duration=run.duration), extra
        try:
            if evalrunner.is_setup_error(run.infra):
                g = _eval_setup_grade(run.infra, run.duration)
            else:
                g = evalgrade.grade(case, run, root)
            extra = {"model": _eval_model_label(case, root, model_override),
                     "cli": evalresults.cli_version(run.log, case.tool),
                     "eval_events": evalresults.event_counts(Path(root.env()["HOUSE_EVAL_LOG"])),
                     "metrics": evalresults.run_metrics(run.log), "turns_sent": run.turns_sent,
                     "turns_ok": run.turns_ok, "audit": {"house_reads": run.house_reads}}
        finally:
            if run.out_dir:
                evalsandbox.remove_tree(run.out_dir)
    return g, extra


EVAL_MAX_PARALLEL = 4  # `house eval run --parallel`: more concurrent agents mostly queue on the API and the disk


def _eval_prepare_docker() -> None:
    """Create the internal network and start the proxy once, before the parallel runs start.

    `agentdocker.ensure_proxy` is check-then-act (`docker rm -f` of a stopped proxy, then `docker run`), so several
    runs doing it at the same moment could remove a proxy another one just started. Does nothing when Docker or
    the image is missing: each run then reports that itself (`infra_error`).
    """
    if agentdocker.docker_usable() and agentdocker.image_present():
        agentdocker.ensure_networks()
        agentdocker.ensure_proxy()


def _eval_interrupt_thread(thread: threading.Thread) -> None:
    """Raise `KeyboardInterrupt` inside a worker thread, which unwinds through `run_case`'s abort path.

    `run_case` waits in `Popen.wait(timeout)`, which polls, so the exception is delivered within a fraction of a
    second; it then kills the agent's process group, removes the container and the temp root is cleaned up. A
    thread that already finished is left alone.
    """
    if thread.ident is not None and thread.is_alive():
        ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(thread.ident), ctypes.py_object(KeyboardInterrupt))


def _eval_run_batch(jobs: list, parallel: int, work, on_crash=None) -> list[tuple]:
    """Run `work(job)` for every job, up to `parallel` at the same time; return the `(job, exception)` of crashes.

    With `parallel == 1` the jobs run one after another in the calling thread and an exception propagates, exactly
    as before `--parallel`. With more, worker threads take jobs in list order; an `Exception` of one job is
    recorded, reported through `on_crash` as it happens and never stops the others. On an interrupt of the calling thread (Ctrl-C, SIGTERM) no new
    job starts, every running job is interrupted (its own abort path removes its container) and the interrupt is
    re-raised once all workers have unwound and `evalrunner.sweep_stale` has removed any `house-eval-*` leftover; a
    further interrupt at any point of that cleanup does not abandon it.

    Args:
        jobs: The run list.
        parallel: Number of concurrent jobs, at least 1.
        work: Called with one job; it does its own output.
        on_crash: Called with `(job, exception)` in the worker thread when `work` raised (parallel only).

    Returns:
        The crashed jobs (always empty for `parallel == 1`, which raises instead).
    """
    if parallel == 1:
        for job in jobs:
            work(job)
        return []
    todo, mutex, stop = iter(jobs), threading.Lock(), threading.Event()
    crashed: list[tuple] = []

    def take():
        with mutex:
            return None if stop.is_set() else next(todo, None)

    def worker():
        try:
            while (job := take()) is not None:
                try:
                    work(job)
                except Exception as e:  # noqa: BLE001 — one crashed run must not stop the others
                    with mutex:
                        crashed.append((job, e))
                    if on_crash:
                        on_crash(job, e)
        except KeyboardInterrupt:
            pass  # the interrupt of the calling thread: `run_case` has aborted, nothing more to do

    threads = [threading.Thread(target=worker, name=f"eval-run-{n}", daemon=True) for n in range(parallel)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    except BaseException:
        interrupted: set[threading.Thread] = set()

        def interrupt_all() -> None:
            stop.set()
            for t in threads:
                if t not in interrupted:  # never twice: a second exception would cut the abort path short
                    _eval_interrupt_thread(t)
                    interrupted.add(t)

        def join_all() -> None:
            for t in threads:
                while t.is_alive():
                    t.join()

        def sweep() -> None:  # belt and braces: the lock is held and no run is alive any more
            for line in evalrunner.sweep_stale():
                print(f"house eval: {line}", file=sys.stderr)

        # Every step is retried after a further interrupt, so a second Ctrl-C/SIGTERM cannot leave a worker
        # running. An async interrupt can also cut a finishing worker's temp-dir cleanup short (swept later).
        for step in (interrupt_all, join_all, sweep):
            while True:
                try:
                    step()
                    break
                except KeyboardInterrupt:
                    pass
        raise
    return crashed


def _eval_busy(args) -> list[str]:
    """Reasons not to start now (a night is running, a contest is in progress); empty when clear or `--force`."""
    busy = []
    if not args.force:
        if locks.held("night"):
            busy.append("a night is running (night.lock is held)")
        live = registry.contests_in_progress(HOUSE_ROOT)
        if live:
            busy.append(f"contest in progress: {', '.join(live)}")
    return busy


def _eval_run(args) -> int:
    """The body of `house eval run` (see `cmd_eval`)."""
    if args.repeat < 1 or (args.max_runs is not None and args.max_runs < 1):
        print("error: --repeat and --max-runs must be at least 1", file=sys.stderr)
        return 2
    if not 1 <= args.parallel <= EVAL_MAX_PARALLEL:
        print(f"error: --parallel must be between 1 and {EVAL_MAX_PARALLEL}", file=sys.stderr)
        return 2
    if args.cohort is not None and not EVAL_COHORT.fullmatch(args.cohort):
        print("error: --cohort must match [a-z0-9][a-z0-9._-]{0,63}", file=sys.stderr)
        return 2
    selected = evalcases.load_all(HOUSE_ROOT / EVAL_CASES, args.tag, args.name)
    if not selected:
        what = f"named {args.name!r}" if args.name else f"tagged {args.tag!r}" if args.tag else "found"
        print(f"error: no eval cases {what} in {EVAL_CASES}/", file=sys.stderr)
        return 2
    problem = _eval_unknown_profile(selected) or _eval_unknown_fixture(selected)
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2
    codex = [c for c in selected if c.tool == "codex"]
    if codex and not args.allow_codex:
        if args.name:
            print(f"error: case {args.name} runs Codex, which can read the whole disk under its workspace-write "
                  "sandbox; pass --allow-codex to run it", file=sys.stderr)
            return 2
        for c in codex:
            print(f"skipped {c.name}: Codex can read the whole disk under its workspace-write sandbox "
                  "(--allow-codex runs it)", file=sys.stderr)
        selected = [c for c in selected if c.tool != "codex"]
    skipped_regression = [c.name for c in codex if c.kind == "regression" and c not in selected]
    variant_names = list(dict.fromkeys(args.variant or [evalcases.DEFAULT_VARIANT]))
    for name in variant_names:
        problem = _eval_unknown_variant(selected, name)
        if problem:
            print(f"error: {problem}", file=sys.stderr)
            return 2
    several = len(variant_names) > 1
    detailed = several or args.parallel > 1  # lines arrive in finishing order: they name their variant
    sha = _eval_commit(args.commit or "HEAD")
    warning = _eval_dirty_warning()
    if warning:
        print(f"warning: {warning}", file=sys.stderr)
    path = HOUSE_ROOT / EVAL_RESULTS
    outcomes: dict[tuple[str, str], list[str]] = {}
    # the run list: per case, per repeat, one run of each variant in turn, so a stopped batch stays balanced
    jobs = [(case, i, v) for case in selected for i in range(1, args.repeat + 1) for v in variant_names]
    if args.max_runs is not None:
        jobs = jobs[:args.max_runs]
    digests = {case.name: evalcases.case_hash(case.path) for case in selected}
    shown = threading.Lock()

    def work(job) -> None:
        case, i, variant_name = job
        g, extra = _eval_once(case, sha, args.model, case.variants.get(variant_name))
        evalresults.append(path, {
            "date": datetime.now().astimezone().isoformat(timespec="seconds"), "commit": sha,
            "cli": extra["cli"], "model": extra["model"], "case": case.name, "case_hash": digests[case.name],
            "variant": variant_name, "cohort": args.cohort, "metrics": extra["metrics"],
            "kind": case.kind,
            "result": g.result, "score": g.score, "hidden": g.hidden,
            "predicates": g.predicates, "missing": g.missing,
            "forbidden": g.forbidden,
            "order_violations": g.order_violations, "failed_predicates": g.failed_predicates,
            "turns": g.turns, "subagent_turns": g.subagent_turns, "tokens": g.tokens,
            "duration": round(g.duration, 1), "note": g.note, "eval_events": extra["eval_events"],
            "turns_sent": extra.get("turns_sent", 0), "turns_ok": extra.get("turns_ok", 0),
            "audit": extra.get("audit", {"house_reads": 0})})
        with shown:
            outcomes.setdefault((case.name, variant_name), []).append(g.result)
            print(_eval_line(case, i, args.repeat, g, variant_name if detailed else None,
                             extra["metrics"].get("weighted")), flush=True)

    def report_crash(job, e) -> None:
        case, i, variant_name = job
        with shown:
            print(f"error: run {case.name} {i}/{args.repeat} variant {variant_name} crashed: "
                  f"{type(e).__name__}: {e}", file=sys.stderr, flush=True)

    crashed: list[tuple] = []
    try:
        with locks.hold("eval"):  # first, so the checks below cannot change before the run starts
            busy = _eval_busy(args)
            if busy:
                print(f"error: refusing to run evals: {'; '.join(busy)} (--force to run anyway)", file=sys.stderr)
                return 1
            for line in evalrunner.sweep_stale():
                print(f"house eval: {line}", file=sys.stderr)
            with evalrunner.term_as_interrupt():
                if args.parallel > 1 and any(c.tool == "claude" for c, _, _ in jobs):
                    _eval_prepare_docker()
                crashed = _eval_run_batch(jobs, args.parallel, work, report_crash)
    except locks.Busy as e:
        print(f"error: {e}; refusing to start a second eval run", file=sys.stderr)
        return 1
    failing, inconclusive = [], [f"{name} (skipped: needs --allow-codex)" for name in skipped_regression]
    for (case, _, variant_name), _ in crashed:  # a run that raised counts as not measured, like an infra_error
        outcomes.setdefault((case.name, variant_name), []).append(evalresults.INFRA)
    for case in selected:
        for variant_name in variant_names:
            got = outcomes.get((case.name, variant_name), [])
            label = f"{case.name} [{variant_name}]" if several else case.name
            real = [r for r in got if r != evalresults.INFRA]
            passed = sum(1 for r in real if r == "pass")
            infra = len(got) - len(real)
            if got:
                print(f"summary: {label} {case.kind} {passed}/{len(real)} passed"
                      + (f" ({infra} infra_error not counted)" if infra else ""))
            if infra:
                print(f"warning: {label}: {infra} infra_error run(s) not counted (the environment, not the agent)",
                      file=sys.stderr)
            if case.kind == "regression":
                if evalresults.is_pass_k(got, len(real)) is False:
                    failing.append(label)
                elif len(real) < args.repeat:
                    inconclusive.append(f"{label} ({len(real)} of {args.repeat} runs counted)")
    if inconclusive:
        print(f"inconclusive: regression case(s) not measured {args.repeat} times: {', '.join(inconclusive)}",
              file=sys.stderr)
    if failing:
        print(f"regression case(s) not pass^k: {', '.join(failing)}", file=sys.stderr)
        return 1
    if crashed:
        return 1
    return 3 if inconclusive else 0


def _eval_status(args) -> int:
    """The body of `house eval status` (see `cmd_eval`)."""
    if args.k < 1:
        print("error: --k must be at least 1", file=sys.stderr)
        return 2
    rows = evalresults.read(HOUSE_ROOT / EVAL_RESULTS)
    groups = [g for g in evalresults.status(rows, args.k)
              if (args.all or args.all_hashes or g["latest"])
              and (args.all or args.commit or g["latest_commit_of_hash" if args.all_hashes else "latest_commit"])
              and (not args.commit or g["commit"].startswith(args.commit))
              and (args.model is None or g["model"] == args.model)
              and (args.name is None or g["case"] == args.name)]
    if not groups:
        print("no results yet (run `house eval run`)" if not rows else "no results for that selection")
        return 0
    width = max(len(g["case"]) for g in groups + [{"case": "case"}])
    mwidth = max(len(g["model"]) for g in groups + [{"model": "model"}])
    vwidth = max(len(g["variant"]) for g in groups + [{"variant": "variant"}])
    print(f"{'case':<{width}}  {'kind':<10}  {'hash':<8}  {'model':<{mwidth}}  {'variant':<{vwidth}}  "
          f"{'runs':>4}  {'pass^1':>6}  "
          f"{f'pass^{args.k}':>6}  {'streak':>6}  {'flags':<8}  last")
    for g in groups:
        pass1 = "-" if g["pass1"] is None else f"{round(g['pass1'] * 100)}%"
        flags = ["flaky"] if g["flaky"] else []
        if g["zero"]:
            flags.append(f"0/{g['runs']}")
        last = " ".join(x for x in (g["last_result"], g["commit"][:7], g["last_date"]) if x)
        print(f"{g['case']:<{width}}  {g['kind']:<10}  {g['case_hash'][:8]:<8}  {g['model']:<{mwidth}}  "
              f"{g['variant']:<{vwidth}}  "
              f"{g['runs']:>4}  {pass1:>6}  {'yes' if g['passk'] else 'no':>6}  {g['streak']:>6}  "
              f"{' '.join(flags):<8}  {last}")
    return 0


def _eval_compare(args) -> int:
    """The body of `house eval compare` (see `cmd_eval`)."""
    if not args.case:
        print("error: house eval compare needs a case name", file=sys.stderr)
        return 2
    try:
        found = evalcases.load_all(HOUSE_ROOT / EVAL_CASES, name=args.case)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not found:
        print(f"error: no eval case named {args.case!r} in {EVAL_CASES}/", file=sys.stderr)
        return 2
    rows = evalresults.read(HOUSE_ROOT / EVAL_RESULTS)
    groups = evalresults.compare(rows, args.case, evalcases.case_hash(found[0].path), args.since, args.cohort)
    if not groups:
        print(f"no rows for {args.case} (current case version{f', since {args.since}' if args.since else ''}"
              f"{f', cohort {args.cohort}' if args.cohort else ''})")
        return 0
    print("\n".join(evalresults.render_compare(groups)))
    return 0


def cmd_eval(args) -> int:
    """Implement `house eval run [--tag T | --name N] [--model M] [--variant V]... [--repeat K] [--commit C]
    [--max-runs N] [--parallel N] [--cohort NAME] [--force] [--allow-codex]` and
    `house eval status [--name N] [--model M] [--commit C] [--all] [--all-hashes] [--k K]` and
    `house eval compare CASE [--since YYYY-MM-DD] [--cohort NAME]`.

    `run` runs the selected cases of `evals/cases/` against a commit in isolated temp roots, one line per run,
    appends one row per run to `evals/results.jsonl` and removes each run's out-dir after grading. It takes
    `eval.lock` first and, holding it, refuses while a night runs or a contest is in progress unless `--force`
    (night does not check `eval.lock`: the exclusion is one-sided by design), then removes what a killed eval left
    behind (`evalrunner.sweep_stale`). SIGTERM and SIGHUP are handled like Ctrl-C. Codex cases are skipped unless
    `--allow-codex` (Codex can read the whole disk under workspace-write). A temp root that cannot be built, a
    profile that is not installed and an unresolvable model are `fail` runs with the note `setup`.
    `--variant NAME` runs the case's variant of that name (a patch of the run's temp root, `cases.Variant`); absent or
    `default` means no patch, and every selected case must have the variant. `--repeat K` runs the case K times one
    after another, each in a fresh temp root. `--variant` is repeatable: the run list is each case x each repeat x each
    variant in turn (interleaved, so a stopped batch stays balanced), cut to `--max-runs`; regression pass^k and the
    summary are per case and variant. `--parallel N` (1-4) runs up to N of them at once in worker threads of this
    process (`_eval_run_batch`; 1 is the plain serial loop): rows are appended under one lock, a line is printed as each
    run finishes, a run that raises is reported and never stops the others (exit 1), and an interrupt aborts every
    running run through its own `run_case` cleanup and re-raises `KeyboardInterrupt`. Every row has `variant`,
    `metrics` (`evalresults.run_metrics`), `audit` (`{"house_reads": N}`, see `evalresults.house_reads`) and
    `cohort` (`--cohort NAME`, a name for the batch of runs an experiment compares; null without).
    `status` prints, per `(case, case_hash, commit, model, variant)`, pass^1, pass^k, the streak of passes and the
    `flaky` / `0/n` flags from the results file; by default only the latest case hash and, per case, its latest commit
    (`--commit` takes a sha prefix, `--model` an exact model, `--all` every hash and commit, `--all-hashes` every
    hash, each at its own latest commit).
    `compare` prints one line per variant of CASE from the rows of the case's current `case_hash` (and local date
    >= `--since`): `n` and `pass` (gradable runs), `infra` (`infra_error`, `incomplete` and `setup` runs, left out of
    every mean), mean and min-max `score`, mean `weighted` cost and min-max (K-rounded like `house cost`), median
    `peak` context, `compactions` per run and `flags` (`control compacted`, `n<3`, `house-read`: a run's tool call
    named the agent's read-only house export, row `audit.house_reads`), then pass counts per predicate
    and variant. Rows of different `(commit, model, cli)` are separate groups flagged `mixed`; `--cohort NAME` takes
    only the rows of that cohort. Numbers only (`evalresults.compare`, `evalresults.render_compare`).

    Args:
        args: Parsed arguments (`action`, `case`, `since`, `tag`, `name`, `model`, `variant`, `repeat`, `commit`,
            `max_runs`, `parallel`, `cohort`, `force`, `allow_codex`, `all`, `all_hashes`, `k`).

    Returns:
        `run`: 0; 1 when refused, `eval.lock` is held, a run raised (parallel batches only), or a `regression` case
        is not pass^k in this run (a counted
        run that did not pass; `infra_error` runs never count); 3 (`inconclusive`, printed) when no regression case
        failed but one was counted fewer times than `--repeat` (infra runs, `--max-runs`) or was skipped for lack
        of `--allow-codex`, so it was not measured; 2 for an unknown or empty selection, an unknown or non-installable agent (`coordinator`), skill, fixture or variant, a Codex case
        named without `--allow-codex`, or bad numbers; 2 for every action in an eval export, which has no
        `houselib/evals`. `status`: 0 (2 for bad numbers). `compare`: 0 (also with
        "no rows"); 2 for an unknown or invalid case or a missing case name. A case name given to `run` or `status`
        is an error (2).
    """
    if evalrunner is None:
        print("error: the eval harness (houselib/evals) is not part of this house; run evals from the house repo",
              file=sys.stderr)
        return 2
    if args.case and args.action != "compare":
        print(f"error: house eval {args.action} takes no case argument (use --name)", file=sys.stderr)
        return 2
    return {"run": _eval_run, "status": _eval_status, "compare": _eval_compare}[args.action](args)


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

    ev = sub.add_parser("evidence", help="facts about an attempt at a task or an interactive session (stdout only)")
    ev.add_argument("name")
    ev.add_argument("task", nargs="?", help="task id, e.g. T001 (default attempt: the last one)")
    ev.add_argument("--attempt", type=int, metavar="N", help="attempt number of the task")
    ev.add_argument("--session", metavar="ID", help="an interactive session of the contest (id or unique prefix)")
    ev.set_defaults(func=cmd_evidence)

    cs = sub.add_parser("cost", help="one row per session of a contest: calls, context, tokens, compactions, cost")
    cs.add_argument("name")
    cs.add_argument("--since", type=_since_date, metavar="YYYY-MM-DD",
                    help="only sessions that began on this date or later")
    cs.set_defaults(func=cmd_cost)

    dc = sub.add_parser("decisions", help="decisions of every contest (DECISIONS.md), one line each, filterable")
    dc.add_argument("--contest", metavar="C", help="only this registered contest")
    dc.add_argument("--exclude", action="append", default=[], metavar="C",
                    help="leave out this contest (repeatable), e.g. the current one")
    dc.add_argument("--by", choices=["human", "agent"], help="human: Kto starts with człowiek; agent: judge/coordinator")
    dc.add_argument("--since", type=_since_date, metavar="YYYY-MM-DD", help="only entries dated this day or later")
    dc.add_argument("--grep", metavar="TEXT", help="literal, case-insensitive text in the title or a field")
    dc.add_argument("--all", action="store_true", help="also superseded entries (marked)")
    dc.add_argument("--full", action="store_true", help="print the whole entry text under each line")
    dc.set_defaults(func=cmd_decisions)

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

    signalcli.register(sub)

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

    ex = sub.add_parser("exp", help="optimize: budgeted experiments, scored and kept/reverted (`exp run ... -- CMD`)")
    ex.add_argument("action", choices=["new", "run", "status", "prune"])
    ex.add_argument("name")
    ex.add_argument("id", nargs="?", help="run: experiment id from `exp new` (E-###)")
    ex.add_argument("--from", dest="from_", metavar="REF", help="parent: best (default for new), E-### or a commit")
    ex.add_argument("--budget", help="run: time limit of the command, e.g. 90s, 5m, 1h (default: spec exp.budget)")
    ex.add_argument("--idea", help="idea id from ideas.md")
    ex.add_argument("--note", help="one-line description of the change")
    ex.add_argument("-n", type=int, default=10, help="status: how many latest rows to list")
    ex.add_argument("--keep", type=int, default=3, help="prune: how many best keep worktrees to leave")
    ex.set_defaults(func=cmd_exp)

    ou = sub.add_parser("out", help="run a long command into a log and see a short envelope (`out -- CMD`), "
                                    "search it (`out show`), delete old logs (`out prune`)")
    ou_sub = ou.add_subparsers(dest="action")
    osw = ou_sub.add_parser("show", help="search a log: matching lines with context, bounded")
    osw.add_argument("ref", help="log id from the envelope, or E-### for an experiment's log")
    osw.add_argument("--grep", required=True, metavar="PATTERN", help="text to find (literal)")
    osw.add_argument("--regex", action="store_true",
                     help="PATTERN is a regular expression (Python re); the match runs unbounded, so avoid "
                          "patterns that backtrack heavily on long lines")
    opr = ou_sub.add_parser("prune", help="delete `house out` logs older than N days")
    opr.add_argument("--days", type=float, default=outlog.PRUNE_DAYS, help="age limit in days (default 7)")
    ou.set_defaults(func=cmd_out)

    so = sub.add_parser("sources", help="contest documents byte for byte in .house/sources/: table of contents "
                                        "(`sources <contest>`) and `sources add <contest> <file|->`")
    so.add_argument("words", nargs="+", metavar="ARG", help="`<contest>` or `add <contest> <file|->` "
                                                              "(`-` reads stdin and needs --name)")
    so.add_argument("--name", help="add: stored file name ([a-z0-9][a-z0-9._-]*; default: the file's base name, "
                                   "lower-cased)")
    so.add_argument("--replace", action="store_true", help="add: overwrite a document of the same name")
    so.set_defaults(func=cmd_sources)

    ev2 = sub.add_parser("eval", help="behaviour evals of agent profiles and skills: `eval run`, `eval status`, "
                                                   "`eval compare`")
    ev2.add_argument("action", choices=["run", "status", "compare"])
    ev2.add_argument("case", nargs="?", help="compare: the case to compare variants of")
    ev2.add_argument("--since", type=_since_date, metavar="YYYY-MM-DD",
                     help="compare: only runs made on this date or later")
    pick = ev2.add_mutually_exclusive_group()
    pick.add_argument("--tag", help="run: only cases with this tag")
    pick.add_argument("--name", help="run/status: only this case")
    ev2.add_argument("--model", help="run: model for Claude cases (default: the case's, then the profile's tier); "
                                     "status: only this model")
    ev2.add_argument("--repeat", type=int, default=1, metavar="K", help="run: runs per case (default 1)")
    ev2.add_argument("--variant", metavar="NAME", action="append",
                     help="run: variant of the case to run (default: `default`, no patch); every selected case "
                          "must have it. Repeatable: each case x each variant x --repeat is the run list, "
                          "interleaved variant by variant")
    ev2.add_argument("--parallel", type=int, default=1, metavar="N",
                     help=f"run: up to N runs at the same time in this process (1-{EVAL_MAX_PARALLEL}, default 1)")
    ev2.add_argument("--commit", help="run: commit of the house repo to test (default HEAD); status: only commits "
                                      "starting with this (default: each case's latest)")
    ev2.add_argument("--max-runs", type=int, metavar="N", help="run: stop after N runs in total")
    ev2.add_argument("--cohort", metavar="NAME", help="run: name of this batch of runs, written into every row "
                                                      "([a-z0-9][a-z0-9._-]{0,63}); compare: only rows of this cohort")
    ev2.add_argument("--force", action="store_true", help="run: also while a night runs or a contest is in progress")
    ev2.add_argument("--allow-codex", action="store_true",
                     help="run: also run Codex cases (Codex can read the whole disk under workspace-write)")
    ev2.add_argument("--all", action="store_true", help="status: every case version and commit, not only the latest")
    ev2.add_argument("--all-hashes", action="store_true", help="status: also older versions of each case")
    ev2.add_argument("--k", type=int, default=3, help="status: window of pass^k and flaky (default 3)")
    ev2.set_defaults(func=cmd_eval)

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
    st.add_argument("--signal-hook", action="store_true",
                    help="only: add the Signal session hook to this repo's .claude/settings.local.json")
    st.add_argument("--signal-timer", action="store_true",
                    help="only: install the systemd user timer that pulls Signal every 5 minutes")
    st.add_argument("--lb-timer", action="store_true",
                    help="only: install the systemd user timer that runs `house lb poll` every 5 minutes")
    st.set_defaults(func=cmd_setup)

    lbp = sub.add_parser("lb", help="official leaderboards: poll (background timer), show, agorize fetcher")
    lbp.add_argument("action", choices=["poll", "show", "agorize"])
    lbp.add_argument("target", nargs="?", help="contest name (poll/show) or the leaderboard page URL (agorize)")
    lbp.add_argument("--force", action="store_true", help="poll: ignore leaderboard.every")
    lbp.add_argument("--sort", choices=["max", "min"], help="agorize: sort by score (default: API order)")
    lbp.set_defaults(func=cmd_lb)

    ad = sub.add_parser("adopt", help="attach an existing contest repo in place (symlink in contests/, adds .house/)")
    ad.add_argument("name")
    ad.add_argument("path")
    ad.set_defaults(func=cmd_adopt)

    y = sub.add_parser("yc", help="search the Y Combinator company directory (prior art / market check)")
    y.add_argument("query", nargs="+", help="what the idea does, in plain words")
    y.add_argument("--limit", type=int, default=15)
    y.set_defaults(func=cmd_yc)
    return p


def subcommands() -> list[str]:
    """Names of the top-level `house` subcommands, in parser order (the registry the permission rules come from).

    Returns:
        Every subcommand name `build_parser` registers, e.g. `["doctor", "check", ...]`.
    """
    parser = build_parser()
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return list(action.choices)


def main(argv: list[str]) -> int:
    """Run the `house` command line.

    Args:
        argv: Arguments without the program name, e.g. `["check", "my-contest", "T001"]`.

    Returns:
        The exit code of the command. ValueError, RuntimeError and OSError from a command are printed as
        `error: ...` on stderr and give 1; invalid arguments exit with 2 via argparse.
    """
    command = None
    if argv[:1] == ["exp"] and "--" in argv:  # everything after `--` is the experiment's command, verbatim
        cut = argv.index("--")
        argv, command = argv[:cut], argv[cut + 1:]
    out_command = None
    if argv[:2] == ["out", "--"]:  # everything after `out --` is the command, verbatim
        argv, out_command = ["out"], argv[2:]
    args = build_parser().parse_args(argv)
    if command is not None:
        args.exp_command = command
    if out_command is not None:
        args.out_command = out_command
    try:
        return args.func(args)
    except (ValueError, RuntimeError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
