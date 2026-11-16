"""Turn one `signal-cli -o json receive` object into one store event, dropping private conversations first.

Used only by `signalstore` (`pull` and later `reparse`). Rules:

* Privacy filter first: content from a DM or from a group that is not allowed returns `(None, True)` and nothing of
  it (text, number, name, group id) leaves this function. Receipts, typing, read markers and empty messages return
  `(None, False)` and are not stored either.
* Event kinds: `message` (text and/or attachments), `reaction`, `edit`, `delete`, `other` (sticker, poll, group
  change, timer change; shown as a `label`). Directions: `in` (someone else), `own` (the owner, seen as a sync
  message from the linked device). `out` events are written by `signalstore.record_out`, never here.
* `key = chat:author:ts` (author = uuid, else number): Signal identifies a message by author + timestamp.
* The full object is kept as `raw`, so a later parser fix can rebuild events without receiving again.
"""
from datetime import datetime

OBJECT = "￼"  # signal-cli puts this in the text where a mention is


def _author(uuid, number, fallback=None) -> str:
    """Pick the stable author id: uuid, else number, else the fallback, else "?"."""
    return str(uuid or number or fallback or "?")


def _iso(ts_ms: int) -> str:
    """Signal timestamp in milliseconds → local ISO time with offset, second precision."""
    return datetime.fromtimestamp(ts_ms / 1000).astimezone().isoformat(timespec="seconds")


def _container(env: dict) -> tuple[dict | None, bool, int | None]:
    """Find the message part of an envelope.

    Returns:
        (message dict, own, edit target ts): the sync `sentMessage` (own) or the `dataMessage`, or for an edit the
        edit's inner `dataMessage` and the edited message's timestamp. (None, False, None) when there is none.
    """
    sent = (env.get("syncMessage") or {}).get("sentMessage")
    if isinstance(sent, dict):
        edit = sent.get("editMessage")
        if isinstance(edit, dict):
            return edit.get("dataMessage") or {}, True, int(edit["targetSentTimestamp"])
        return sent, True, None
    data = env.get("dataMessage")
    if isinstance(data, dict):
        return data, False, None
    edit = env.get("editMessage")
    if isinstance(edit, dict):
        return edit.get("dataMessage") or {}, False, int(edit["targetSentTimestamp"])
    return None, False, None


def _kind(msg: dict, edit_target: int | None) -> tuple[str | None, str | None]:
    """Classify a message part; returns (kind, label for `other`) or (None, None) when it carries nothing."""
    if msg.get("reaction"):
        return "reaction", None
    if msg.get("remoteDelete"):
        return "delete", None
    if edit_target is not None:
        return "edit", None
    if msg.get("message") or msg.get("attachments"):
        return "message", None
    if msg.get("sticker"):
        return "other", "[naklejka]"
    if any(k.startswith("poll") and msg[k] for k in msg):
        return "other", "[ankieta]"
    if (msg.get("groupInfo") or {}).get("type") == "UPDATE":
        return "other", "[zmiana grupy]"
    if msg.get("isExpirationUpdate"):
        return "other", "[zmiana czasu znikania]"
    return None, None


def _chat(env: dict, msg: dict, own_flag: bool, own: str) -> str | None:
    """Chat id of a message part: the group id, "self" for Note to Self, None for a private conversation."""
    gid = (msg.get("groupInfo") or {}).get("groupId")
    if gid:
        return str(gid)
    if own_flag:
        sent = (env.get("syncMessage") or {}).get("sentMessage") or {}
        dests = {sent.get("destinationNumber"), sent.get("destination")}
        if own in dests or (sent.get("destinationUuid") and sent.get("destinationUuid") == env.get("sourceUuid")):
            return "self"
    return None


def _text(msg: dict) -> tuple[str, list[dict]]:
    """Message text with each mention placeholder replaced by `@name`, and the mentions in text order."""
    text = str(msg.get("message") or "")
    mentions = sorted((m for m in msg.get("mentions") or [] if isinstance(m, dict)),
                      key=lambda m: int(m.get("start") or 0))
    out = [{"name": m.get("name"), "number": m.get("number"), "uuid": m.get("uuid"),
            "start": m.get("start"), "length": m.get("length")} for m in mentions]
    for m in out:
        if OBJECT not in text:
            break
        text = text.replace(OBJECT, f"@{m['name'] or m['number'] or m['uuid'] or '?'}", 1)
    return text, out


def extract(obj: dict, own: str, chats: dict[str, str], now: datetime | None = None) -> tuple[dict | None, bool]:
    """Build the store event for one signal-cli JSON object, applying the privacy filter.

    Args:
        obj: One parsed line of `signal-cli -o json receive` (a dict with an "envelope" key).
        own: The account's own number (`Signal.account`), to recognise Note to Self.
        chats: Allowed chats: chat id ("self" or a group id) → alias. Everything else is dropped.
        now: Time of receiving, for `received_at`. Defaults to the current time.

    Returns:
        (event, dropped). event is a dict in store format without `seq` (see the module docstring), or None when
        nothing is kept. dropped is True only when content from a DM or a non-allowed group was discarded.

    Raises:
        TypeError, ValueError, AttributeError, KeyError: On malformed field types; `signalstore.pull` counts and
            skips that object.
    """
    env = obj.get("envelope")
    if not isinstance(env, dict):
        return None, False
    msg, own_flag, edit_target = _container(env)
    if msg is None:
        return None, False
    kind, label = _kind(msg, edit_target)
    if kind is None:
        return None, False
    chat = _chat(env, msg, own_flag, own)
    if chat is None or chat not in chats:
        return None, True
    author = _author(env.get("sourceUuid"), env.get("sourceNumber"), env.get("source"))
    ts = int(msg.get("timestamp") or env.get("timestamp"))
    text, mentions = _text(msg)
    quote = None
    q = msg.get("quote")
    if isinstance(q, dict) and q.get("id") is not None:
        quote = {"ts": int(q["id"]), "author": _author(q.get("authorUuid"), q.get("authorNumber"), q.get("author")),
                 "text": q.get("text")}
    attachments = [{"content_type": a.get("contentType"), "filename": a.get("filename"), "size": a.get("size"),
                    "id": a.get("id"), "path": None} for a in msg.get("attachments") or [] if isinstance(a, dict)]
    target = None
    event_extra: dict = {}
    if kind == "reaction":
        r = msg["reaction"]
        target = {"ts": int(r["targetSentTimestamp"]),
                  "author": _author(r.get("targetAuthorUuid"), r.get("targetAuthorNumber"), r.get("targetAuthor"))}
        event_extra["reaction"] = {"emoji": str(r.get("emoji") or ""), "remove": bool(r.get("isRemove"))}
        text = ""
    elif kind == "delete":
        target = {"ts": int(msg["remoteDelete"]["timestamp"]), "author": author}
        text = ""
    elif kind == "edit":
        target = {"ts": edit_target, "author": author}
    elif kind == "other":
        event_extra["label"] = label
    return {
        "v": 1, "key": f"{chat}:{author}:{ts}", "kind": kind, "dir": "own" if own_flag else "in",
        "chat": chat, "chat_alias": chats[chat], "ts": ts, "time": _iso(ts),
        "received_at": (now or datetime.now()).astimezone().isoformat(timespec="seconds"),
        "sender": {"name": env.get("sourceName") or env.get("sourceNumber") or "?",
                   "number": env.get("sourceNumber"), "uuid": env.get("sourceUuid")},
        "text": text, "quote": quote, "mentions": mentions, "attachments": attachments,
        "expires_in": int(msg.get("expiresInSeconds") or 0), "target": target, "meta": None, "status": "ok",
        **event_extra, "raw": obj,
    }, False
