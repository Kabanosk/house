"""`house signal …`: read and write the team's Signal chats through the store (never through raw signal-cli).

Subcommands: `pull`, `migrate` (more come with reading and sending). Exit codes: 0 success (also an empty
result or Signal not configured for reading commands), 1 failure (Signal not configured for sending, failed send),
2 usage error (unknown alias, bad argument).
"""
import json
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
        quoted = signalread.snippet(target.get("text"), 28)
        notes.append(f"cytat: {signalread.display_name(target)} {when} „{quoted}”")
    notes += [f"📎 {p.name}, {signalread.fmt_size(p.stat().st_size)}" for p in files]
    print(f"✓ {chat.alias} {ts}" + (f" ({'; '.join(notes)})" if notes else ""))
    return 0


def _tty() -> bool:
    """True when a human runs the command in a terminal (agents' Bash has no TTY on stdin)."""
    return sys.stdin.isatty()


def _ask(prompt: str) -> str:
    return input(prompt)


def _human_only(command: str) -> int | None:
    """Exit code 2 with the refusal when there is no terminal; None when a human is at the keyboard."""
    if _tty():
        return None
    print(f"error: dodanie lub usunięcie grupy to decyzja człowieka: poproś Wojtka o `house signal {command}` "
          "w terminalu", file=sys.stderr)
    return 2


def _yes(prompt: str) -> bool:
    try:
        return _ask(prompt).strip().lower() in ("t", "tak", "y", "yes")
    except EOFError:
        return False


def cmd_groups(args) -> int:
    """`house signal groups [--all]`: allowed chats with message counts; `--all` also the account's other groups.

    Without a terminal `--all` prints only how many other groups there are, never their names. Exit 0.
    """
    now = datetime.now().astimezone()
    sig = _client()
    chats = signalchats.allowed(sig)
    counts: dict[str, int] = {}
    last: dict[str, datetime] = {}
    for e in signalstore.latest(signalstore.iter_events(raw=False)):
        if e.get("kind") in ("message", "other"):
            chat = str(e.get("chat"))
            counts[chat] = counts.get(chat, 0) + 1
            t = signalread.parse_iso(e.get("time"))
            if t and (chat not in last or t > last[chat]):
                last[chat] = t
    print(f"{'alias':<12}{'grupa':<22}{'źródło':<24}{'wiadomości':>10}  ostatnia")
    for chat in chats.values():
        print(f"{chat.alias:<12}{chat.name[:21]:<22}{chat.source[:23]:<24}{counts.get(chat.id, 0):>10}  "
              f"{signalread.fmt_time(last.get(chat.id), now) if chat.id in last else '—'}")
    if args.all:
        if sig is None:
            print("Signal nie jest skonfigurowany: nie widzę innych grup konta")
            return 0
        others = [g for g in sig.list_groups() if g.get("id") not in chats]
        if not _tty():
            print(f"+ {len(others)} innych grup konta (nazwy tylko w terminalu)")
            return 0
        print("\ngrupy konta:")
        for g in sig.list_groups():
            mark = "[dozwolona]" if g.get("id") in chats else "[ ]"
            print(f"  {mark:<12}{g.get('name') or '?'}  ({str(g.get('id'))[:8]}…)")
    return 0


def cmd_allow(args) -> int:
    """`house signal allow NAME [--alias A] [--id ID]`: let house keep and agents read a group (human only).

    Exit 0 added (or already allowed), 1 declined or Signal not configured, 2 no terminal, unknown or ambiguous
    group, bad alias.
    """
    refused = _human_only(f"allow {args.name!r}")
    if refused:
        return refused
    sig = _client()
    if sig is None:
        print("error: Signal nie jest skonfigurowany (~/.config/house/notify.env)", file=sys.stderr)
        return 1
    groups = [g for g in sig.list_groups() if g.get("id")]
    if args.id:
        found = [g for g in groups if g["id"] == args.id]
    else:
        found = [g for g in groups if g.get("name") == args.name] or \
                [g for g in groups if str(g.get("name") or "").lower() == args.name.lower()]
    if not found:
        return _usage(f"nie ma grupy {args.name!r} na koncie (lista: house signal groups --all)")
    if len(found) > 1:
        ids = "\n".join(f"  {g['id']}  {g.get('name')}" for g in found)
        return _usage(f"kilka grup o nazwie {args.name!r}:\n{ids}\nwybierz jedną: --id ID")
    group = found[0]
    name = str(group.get("name") or args.name)
    config = signalchats.load_config()
    for alias, entry in config.items():
        if entry["id"] == group["id"]:
            print(f"już dozwolona jako `{alias}`")
            return 0
    alias = args.alias or signalchats.slug(name)
    why = signalchats.validate_alias(alias, group["id"], signalchats.allowed(sig))
    if why:
        return _usage(why)
    short = f"{group['id'][:8]}…"
    if not _yes(f"Dodać „{name}” ({short}) jako `{alias}`? Agenci będą czytać tę rozmowę i mogą do niej "
                "pisać. [t/N] "):
        print("nic nie zmieniono")
        return 1
    config[alias] = {"id": group["id"], "name": name,
                     "added": datetime.now().astimezone().isoformat(timespec="seconds")}
    signalchats.save_config(config)
    print(f"✓ {alias} → {name} ({short}). Od teraz zapisywane i czytelne dla agentów.")
    return 0


def cmd_deny(args) -> int:
    """`house signal deny ALIAS`: stop keeping a `signal.toml` group (human only); its history stays.

    Exit 0 removed, 1 declined, 2 no terminal, unknown alias or a contest's group.
    """
    refused = _human_only(f"deny {args.alias}")
    if refused:
        return refused
    config = signalchats.load_config()
    if args.alias not in config:
        chat = next((c for c in signalchats.allowed(None).values() if c.alias == args.alias), None)
        if chat is not None and chat.source.startswith("konkurs"):
            return _usage(f"`{args.alias}` to grupa konkursu ({chat.source}): zmień notify.signal_group w specu "
                          "albo zakończ konkurs")
        return _usage(f"nie ma `{args.alias}` w signal.toml (lista: house signal groups)")
    if not _yes(f"Przestać zapisywać `{args.alias}` ({config[args.alias].get('name')})? Historia zostaje. [t/N] "):
        print("nic nie zmieniono")
        return 1
    del config[args.alias]
    signalchats.save_config(config)
    print(f"✓ `{args.alias}` usunięta z dozwolonych; zapisana historia zostaje w magazynie")
    return 0


def _view(now: datetime):
    """Fold the whole store for reading; aliases come from the current configuration."""
    chats = signalchats.allowed(None)
    return signalread.View(signalstore.iter_events(raw=False), now, {c.id: c.alias for c in chats.values()}), chats


def cmd_read(args) -> int:
    """`house signal read [CHAT] [-n 20] [--since] [--until] [--from] [--grep] [--thread] [--json] [--offline]`.

    Quick receive first (unless --offline or received < 60 s ago), then only the store. Exit 0 (also when empty
    or when receiving failed: the header says why), 2 unknown chat or bad time.
    """
    now = datetime.now().astimezone()
    try:
        chat = None if args.chat == "all" else signalchats.resolve(args.chat)
        since = signalread.parse_when(args.since, now) if args.since else None
        until = signalread.parse_when(args.until, now) if args.until else None
    except ValueError as e:
        return _usage(str(e))
    note = signalread.maybe_pull(args.offline, now)
    view, chats = _view(now)
    pool = [m for m in view.msgs if (m.chat == chat.id if chat else m.chat in chats)]
    selected = signalread.select(pool, since=since, until=until, sender=args.from_, grep=args.grep,
                                 thread=args.thread, n=args.n)
    if args.json:
        for m in selected:
            print(json.dumps(view.as_json(m), ensure_ascii=False))
        return 0
    last = signalread.parse_iso(signalstore.read_state().get("last_pull_ok"))
    print(view.render_read(selected, title=(chat.alias, chat.name) if chat else None, total=len(pool),
                           last_pull=last, note=note), end="")
    return 0


def cmd_show(args) -> int:
    """`house signal show TS [--chat CHAT] [--from NAME] [--raw]`: one message in full.

    Exit 0 shown, 2 unknown chat, no such message, or several messages with this ts (narrow with --chat/--from).
    """
    now = datetime.now().astimezone()
    try:
        chat = signalchats.resolve(args.chat) if args.chat else None
    except ValueError as e:
        return _usage(str(e))
    signalread.maybe_pull(args.offline, now)
    view, chats = _view(now)
    found = [m for m in view.find(args.ts) if (m.chat == chat.id if chat else m.chat in chats)]
    if args.from_:
        found = signalread.select(found, sender=args.from_, n=0)
    if not found:
        return _usage(f"nie ma wiadomości {args.ts} w zapisanych rozmowach (house signal read)")
    if len(found) > 1:
        listing = "\n".join(view.line(m, all_chats=True)[0] for m in found)
        return _usage(f"kilka wiadomości o ts {args.ts}:\n{listing}\nzawęź: --chat CHAT albo --from NAZWA")
    print(view.render_show(found[0]), end="")
    if args.raw:
        raw = next((e.get("raw") for e in signalstore.iter_events() if e.get("key") == found[0].ev.get("key")), None)
        print(json.dumps(raw or found[0].ev, ensure_ascii=False, indent=2))
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
    gr = ss.add_parser("groups", help="allowed chats (alias, source, counts); --all: other groups of the account")
    gr.add_argument("--all", action="store_true")
    gr.set_defaults(func=cmd_groups)
    al = ss.add_parser("allow", help="human only (terminal): let house keep and agents read a group")
    al.add_argument("name", help="group title as in Signal")
    al.add_argument("--alias", help="short name (default: from the title)")
    al.add_argument("--id", help="group id, when several groups share the title")
    al.set_defaults(func=cmd_allow)
    dn = ss.add_parser("deny", help="human only (terminal): stop keeping a signal.toml group (history stays)")
    dn.add_argument("alias")
    dn.set_defaults(func=cmd_deny)
    rd = ss.add_parser("read", help="history of a chat (default: all allowed), newest last")
    rd.add_argument("chat", nargs="?", default="all", help="alias, self or all (default)")
    rd.add_argument("-n", type=int, default=20, help="last N after filters (0 = all)")
    rd.add_argument("--since", help="30m, 2h, 1d, 10:00, 2026-11-16, 2026-11-16T10:00")
    rd.add_argument("--until")
    rd.add_argument("--from", dest="from_", metavar="NAME", help="part of the sender's name or number, or me")
    rd.add_argument("--grep", help="text to find (also in the quoted text)")
    rd.add_argument("--thread", type=int, metavar="TS", help="the message, what it quotes and all replies")
    rd.add_argument("--json", action="store_true", help="one store event per line (no raw)")
    rd.add_argument("--offline", action="store_true", help="do not receive first")
    rd.set_defaults(func=cmd_read)
    sh = ss.add_parser("show", help="one message in full: quote text, reactions, replies, files")
    sh.add_argument("ts", type=int)
    sh.add_argument("--chat")
    sh.add_argument("--from", dest="from_", metavar="NAME")
    sh.add_argument("--raw", action="store_true", help="also print the signal-cli envelope")
    sh.add_argument("--offline", action="store_true", help="do not receive first")
    sh.set_defaults(func=cmd_show)
