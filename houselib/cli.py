"""`house` command line."""
import argparse
import sys
from pathlib import Path

from . import HOUSE_ROOT, adapters, context, doctor, export, registry, scaffold, setup, yc


def contest_dir(name: str) -> Path:
    scaffold.validate_name(name)  # raises ValueError: never resolve "..", "/x", etc.
    return HOUSE_ROOT / "contests" / name


def cmd_doctor(args) -> int:
    if args.context and not args.name:
        print("error: --context needs a contest name (house doctor --context <name>)", file=sys.stderr)
        return 1
    tools = doctor.detect()
    print(doctor.summary(tools))
    if args.name:
        target = contest_dir(args.name)
        if not (target / ".house").is_dir():
            print(f"error: {target} is not a house contest", file=sys.stderr)
            return 1
        print(f"wrote {doctor.write(target, tools)}")
        if args.context:
            print("\n" + context.format_report(context.measure(target.resolve(), Path.home())))
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
    for path in adapters.adapt(target):
        print(f"wrote {path.relative_to(target)}")
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
