"""`house` command line."""
import argparse
import sys
from pathlib import Path

from . import HOUSE_ROOT, doctor, scaffold


def _todo(args) -> int:
    print(f"house {args.command}: not implemented yet")
    return 2


def contest_dir(name: str) -> Path:
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
    a.set_defaults(func=_todo)

    e = sub.add_parser("export", help="clean public copy of a contest (no .house, agent configs, secrets)")
    e.add_argument("name")
    e.add_argument("dest")
    e.add_argument("--force", action="store_true", help="export even if secret scan finds hits")
    e.set_defaults(func=_todo)

    s = sub.add_parser("sync", help="clone contests listed in contests.yaml that are missing locally")
    s.set_defaults(func=_todo)
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
