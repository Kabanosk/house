"""S7: a coding agent started from Signal (`@claude …` in an allowed chat), safely.

Reads only the message store (after `signalstore.pull`), never `receive`. Design and threat model: spec
`docs/specs/2026-11-16-signal-groups-design.md` §15 and `docs/research/2026-11-16-signal-claude-agent.md`.
Rules a reader of this module must keep:

* The trigger is the literal text `@claude` in the message as sent (`raw`, where mentions are still U+FFFC),
  never the quoted text, an edit, a reaction or anything house itself sent (`dir: out`, text starting with 🤖).
* Authorisation uses the sender's ACI (`sender.uuid`), never the display name; owner commands only as `dir: own`
  in Note to Self.
* The agent runs sandboxed in its own worktree and never commits, pushes or sends; house does that afterwards,
  after filtering what goes back to the chat.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

TRIGGER_RE = re.compile(r"(?<![\w@.])@claude\b", re.IGNORECASE)
ALIAS_HINT_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9-]*)\s*:\s*(.+)$", re.DOTALL)
COMMANDS = {"/status": "status", "/lb": "lb", "/stop": "stop"}
SELF = "self"


@dataclass
class Request:
    """A request to start the agent (`@claude …`).

    Attributes:
        key: Store key `chat:author:ts` (idempotency key of the job).
        chat: "self" or the group id. chat_alias: alias at the time of receiving.
        ts: Signal timestamp of the message (quoted by every reply).
        author: Sender ACI (uuid), else number; the only identity used for decisions.
        author_name: Display name, for messages only.
        own: Sent by the owner's own account (sync message).
        text: The request without `@claude` and without an alias hint.
        full_text: The request without `@claude` (alias hint kept).
        alias_hint: `fr` for `@claude fr: …`, lower-case, else None; routing decides whether it names a contest.
        quote_ts: Timestamp of the quoted message, if any.
        received_ms: `serverReceivedTimestamp` of the envelope (age check), None when unknown.
    """

    key: str
    chat: str
    chat_alias: str
    ts: int
    author: str
    author_name: str
    own: bool
    text: str
    full_text: str
    alias_hint: str | None
    quote_ts: int | None
    received_ms: int | None


@dataclass
class Command:
    """A chat command handled by house without a model: status, lb, stop, agent-off, agent-on."""

    name: str
    key: str
    chat: str
    author: str
    own: bool
    quote_ts: int | None


def _sent_text(event: dict) -> str:
    """The text exactly as sent (mentions still U+FFFC), from `raw`; the stored text when there is no envelope."""
    env = (event.get("raw") or {}).get("envelope") or {}
    msg = ((env.get("syncMessage") or {}).get("sentMessage") or env.get("dataMessage") or {})
    text = msg.get("message") if isinstance(msg, dict) else None
    return unicodedata.normalize("NFKC", str(text if text is not None else event.get("text") or ""))


def parse(event: dict) -> "Request | Command | None":
    """Classify one store event as a request, a command, or nothing.

    Args:
        event: A store event (`signalstore.iter_events`).

    Returns:
        `Request` for `@claude …`, `Command` for `/status`, `/lb`, `/stop` (any chat) and `/agent off|on` (owner,
        Note to Self only), None for everything else (house's own messages, edits, reactions, quotes, chatter).
    """
    if event.get("kind") != "message" or event.get("dir") not in ("in", "own"):
        return None
    sent = _sent_text(event)
    if sent.lstrip().startswith("🤖"):
        return None
    sender = event.get("sender") or {}
    author = str(sender.get("uuid") or sender.get("number") or "?")
    own = event.get("dir") == "own"
    chat = str(event.get("chat"))
    quote_ts = (event.get("quote") or {}).get("ts")
    words = sent.strip().lower().split()
    if words and words[0] in COMMANDS:
        return Command(COMMANDS[words[0]], str(event["key"]), chat, author, own, quote_ts)
    if words[:1] == ["/agent"] and words[1:2] in (["off"], ["on"]):
        if own and chat == SELF:
            return Command(f"agent-{words[1]}", str(event["key"]), chat, author, own, quote_ts)
        return None
    if not TRIGGER_RE.search(sent):
        return None
    shown = str(event.get("text") or sent)
    full = " ".join(TRIGGER_RE.sub(" ", shown, count=1).split()).strip(" ,:")
    hint = ALIAS_HINT_RE.match(full)
    env = (event.get("raw") or {}).get("envelope") or {}
    received = env.get("serverReceivedTimestamp")
    return Request(
        key=str(event["key"]), chat=chat, chat_alias=str(event.get("chat_alias") or chat), ts=int(event["ts"]),
        author=author, author_name=str(sender.get("name") or author), own=own,
        text=hint[2].strip() if hint else full, full_text=full, alias_hint=hint[1].lower() if hint else None,
        quote_ts=int(quote_ts) if quote_ts is not None else None,
        received_ms=int(received) if str(received or "").isdigit() else None)


# --- routing ----------------------------------------------------------------------------------------------------

@dataclass
class Route:
    """Where a request runs.

    Attributes:
        repo: Repository to work in (a contest checkout or the house repo); None when house must ask first.
        contest: The contest, when the repo is one.
        text: The request text to give the agent (alias hint removed only when it named a contest).
        asks: (contest, message) pairs to send when the contest is ambiguous; nothing starts then.
    """

    repo: Path | None
    contest: Path | None
    text: str
    asks: list[tuple[Path, str]] = field(default_factory=list)


def route(req: Request, chats: dict, out_events: dict[int, dict]) -> Route:
    """Pick the repo for a request (spec §15 F5).

    Order: a quote of a house message tagged with a contest of this chat → that contest; an alias hint naming a
    contest of this chat → it; a chat with exactly one contest → it; several → ask in each contest's name (a quote
    on one of those questions decides later); Note to Self and `signal.toml` groups → the house repo.

    Args:
        req: The parsed request.
        chats: Allowed chats (`signalchats.allowed`), giving each group's contests.
        out_events: House's sent messages by Signal timestamp (their `meta.contest` names the contest).
    """
    from . import HOUSE_ROOT, notify
    chat = chats.get(req.chat)
    contests = list(chat.contests) if chat is not None else []
    if not contests:
        return Route(HOUSE_ROOT, None, req.full_text)
    by_name = {notify.contest_name(c): c for c in contests}
    quoted = ((out_events.get(req.quote_ts) or {}).get("meta") or {}).get("contest") if req.quote_ts else None
    if quoted in by_name:
        return Route(by_name[quoted], by_name[quoted], req.full_text)
    if req.alias_hint:
        named = [c for c in contests if notify.alias(c).lower() == req.alias_hint]
        if named:
            return Route(named[0], named[0], req.text)
    if len(contests) == 1:
        return Route(contests[0], contests[0], req.full_text)
    short = " ".join(req.full_text.split())[:60]
    return Route(None, None, req.full_text,
                 [(c, f"odpowiedz cytatem na tę wiadomość, jeśli zlecenie „{short}” jest do {notify.alias(c)}")
                  for c in contests])
