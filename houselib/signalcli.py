"""`house signal …`: read and write the team's Signal chats through the store (never through raw signal-cli).

Subcommands: `pull`, `migrate` (more come with reading and sending). Exit codes: 0 success (also an empty
result or Signal not configured for reading commands), 1 failure (Signal not configured for sending, failed send),
2 usage error (unknown alias, bad argument).
"""
import sys
from datetime import datetime
from pathlib import Path

from . import signalchats, signalread, signalstore

MAX_FILE = 100 * 1024 * 1024
FORBIDDEN_DIRS = (".ssh", ".config", ".local/share/signal-cli")
SECRET_SUFFIXES = (".pem", ".key")


def _usage(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 2


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


def _check_file(path: str) -> tuple[Path | None, str | None]:
    """Resolve a file to send; (path, None) when allowed, else (None, Polish reason)."""
    p = Path(path).expanduser()
    if not p.is_file():
        return None, f"nie ma pliku {path}"
    p = p.resolve()
    home = Path.home().resolve()
    for d in FORBIDDEN_DIRS:
        if p.is_relative_to(home / d):
            return None, f"{path}: pliki z ~/{d} nie są wysyłane (sekrety, konfiguracja, cudze czaty)"
    name = p.name.lower()
    if name.startswith(".env") or name.endswith(".env") or name.endswith(SECRET_SUFFIXES):
        return None, f"{path}: wygląda na sekret (.env/.pem/.key), nie wysyłam"
    if p.stat().st_size > MAX_FILE:
        return None, f"{path}: większy niż 100 MB (limit Signala)"
    return p, None


def _quote_target(chat, ts: int, who: str | None) -> tuple[dict | None, str | None]:
    """The stored message `ts` in `chat` to quote, narrowed by `--from`; (event, None) or (None, reason)."""
    found = [e for e in signalstore.find(chat.id, ts) if e.get("kind") in ("message", "other")]
    if who:
        w = who.lower()
        found = [e for e in found if (w == "me" and e.get("dir") in ("own", "out"))
                 or w in str((e.get("sender") or {}).get("name") or "").lower()
                 or w in str((e.get("sender") or {}).get("number") or "")]
    if not found:
        return None, f"nie znam wiadomości {ts} w `{chat.alias}` (sprawdź: house signal read {chat.alias})"
    if len(found) > 1:
        names = ", ".join(signalread.display_name(e) for e in found)
        return None, f"kilka wiadomości {ts} w `{chat.alias}` ({names}); wskaż autora: --from NAZWA"
    return found[0], None


def cmd_send(args) -> int:
    """`house signal send CHAT [TEXT...] [--reply-to TS] [--file PATH]...`: send to an allowed chat, logged.

    The text gets the prefix `🤖 [house]` (or `🤖 [<alias>]` inside a contest repo). Exit 0 sent, 1 Signal not
    configured or sending failed, 2 bad chat / unknown or ambiguous ts / refused file / nothing to send.
    """
    from . import guard, notify
    sig = _client()
    try:
        chat = signalchats.resolve(args.chat, sig)
    except ValueError as e:
        return _usage(str(e))
    files = []
    for f in args.file or []:
        p, why = _check_file(f)
        if why:
            return _usage(why)
        files.append(p)
    text = " ".join(args.text).strip()
    if not text and not files:
        return _usage("pusta wiadomość: podaj tekst albo --file")
    quote = target = None
    if args.reply_to is not None:
        target, why = _quote_target(chat, args.reply_to, args.from_)
        if why:
            return _usage(why)
        s = target.get("sender") or {}
        author = s.get("number") if target.get("dir") == "out" else (s.get("uuid") or s.get("number"))
        if not author:
            return _usage(f"wiadomość {args.reply_to} nie ma autora (odebrana przed migracją); "
                          "wyślij bez --reply-to")
        quote = (args.reply_to, author)
    if sig is None:
        print("error: Signal nie jest skonfigurowany (~/.config/house/notify.env)", file=sys.stderr)
        return 1
    contest = guard.contest_root(Path.cwd())
    prefix = notify.prefix(contest) if contest else "🤖 [house]"
    body = text if text.startswith(prefix) else f"{prefix} {text}".strip()
    meta = {"origin": "cli", "contest": notify.contest_name(contest) if contest else None}
    group = None if chat.id == signalchats.SELF else chat.id
    ts = sig.send(body, group_id=group, attachments=[str(p) for p in files], quote=quote, meta=meta)
    if ts is None:
        print(f"✗ nie wysłano do {chat.alias} (signal-cli: błąd albo brak połączenia; `house signal status`)")
        return 1
    notes = []
    if target is not None:
        when = datetime.fromisoformat(target["time"]).astimezone().strftime("%H:%M") if target.get("time") else "?"
        notes.append(f"cytat: {signalread.display_name(target)} {when} „{signalread.snippet(target.get('text'), 28)}”")
    notes += [f"📎 {p.name}, {signalread.fmt_size(p.stat().st_size)}" for p in files]
    print(f"✓ {chat.alias} {ts}" + (f" ({'; '.join(notes)})" if notes else ""))
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
    sd = ss.add_parser("send", help="send to an allowed chat (prefix 🤖, logged): send CHAT TEXT [--reply-to TS]")
    sd.add_argument("chat", help="alias from `house signal groups`, or self")
    sd.add_argument("text", nargs="*")
    sd.add_argument("--reply-to", type=int, metavar="TS", help="quote this message (timestamp from `read`)")
    sd.add_argument("--from", dest="from_", metavar="NAME", help="author of --reply-to when the ts is ambiguous")
    sd.add_argument("--file", action="append", metavar="PATH", help="attach a file (repeatable)")
    sd.set_defaults(func=cmd_send)
