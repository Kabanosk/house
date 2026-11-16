"""`house signal …`: read and write the team's Signal chats through the store (never through raw signal-cli).

Subcommands: `pull`, `migrate` (more come with reading and sending). Exit codes: 0 success (also an empty
result or Signal not configured for reading commands), 1 failure (Signal not configured for sending, failed send),
2 usage error (unknown alias, bad argument).
"""
from . import signalstore


def _client():
    """The configured `Signal` client or None (looked up at call time so tests can patch it)."""
    from . import signal
    return signal.from_config()


def cmd_pull(args) -> int:
    """`house signal pull [--timeout 5]`: receive now and store every allowed message; always exit 0."""
    for line in signalstore.ensure_migrated():
        print(line)
    res = signalstore.pull(_client(), timeout=args.timeout)
    if res.skipped:
        print(f"pominięto odbiór: {res.skipped}")
        return 0
    line = f"odebrano {res.kept}, odrzucono {res.dropped} (spoza dozwolonych)"
    if res.bad_lines:
        line += f", złe linie {res.bad_lines}"
    print(line + (f" · błąd: {res.error}" if res.error else ""))
    return 0


def cmd_migrate(args) -> int:
    """`house signal migrate`: move inbox.jsonl / inbox_done.json / sent.jsonl into the store; always exit 0."""
    for line in signalstore.migrate() or ["nic do migracji"]:
        print(line)
    return 0


def register(sub) -> None:
    """Add the `signal` command with its subcommands to the top-level subparsers of `house`."""
    sg = sub.add_parser("signal", help="team chats on Signal: read, unread, show, send, groups, allow, pull")
    ss = sg.add_subparsers(dest="signal_command", required=True)
    p = ss.add_parser("pull", help="receive now and store every allowed message")
    p.add_argument("--timeout", type=int, default=5)
    p.set_defaults(func=cmd_pull)
    m = ss.add_parser("migrate", help="move the old inbox.jsonl / sent.jsonl into the store (idempotent)")
    m.set_defaults(func=cmd_migrate)
