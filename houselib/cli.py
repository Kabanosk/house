"""`house` command line."""
import argparse
import sys
from pathlib import Path

from . import HOUSE_ROOT, adapters, doctor, export, registry, scaffold, yc


def contest_dir(name: str) -> Path:
    scaffold.validate_name(name)  # raises ValueError: never resolve "..", "/x", etc.
    return HOUSE_ROOT / "contests" / name


def cmd_doctor(args) -> int:
    tools = doctor.detect()
    print(doctor.summary(tools))
    if args.name:
        target = contest_dir(args.name)
        if not (target / ".house").is_dir():
            print(f"error: {target} is not a house contest", file=sys.stderr)
            return 1
        print(f"wrote {doctor.write(target, tools)}")
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="house", description="AI Software House toolkit")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("doctor", help="detect available tools (claude, codex, ollama, gpu, ...)")
    d.add_argument("name", nargs="?", help="contest name: also write .house/tools.json")
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
