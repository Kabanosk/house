"""`house` command line."""
import argparse


def _todo(args) -> int:
    print(f"house {args.command}: not implemented yet")
    return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="house", description="AI Software House toolkit")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("doctor", help="detect available tools (claude, codex, ollama, gpu, ...)")
    d.add_argument("name", nargs="?", help="contest name: also write .house/tools.json")
    d.set_defaults(func=_todo)

    n = sub.add_parser("new", help="scaffold a contest repo in contests/<name>")
    n.add_argument("name")
    n.add_argument("--repo", help="clone this git URL instead of git init")
    n.set_defaults(func=_todo)

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
