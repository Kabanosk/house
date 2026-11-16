"""Reading the Signal store: fold reactions/edits/deletes onto messages, filter, format, track what is new.

Used by `house signal read|unread|show|status`, the session hook and `house inbox` (unread count). Reads only the
store (`signalstore.iter_events`), never signal-cli. Output is plain text for agents and humans (Polish labels).
"""
import re

HOUSE_PREFIX = re.compile(r"^🤖 \[[^\]]*\]\s*")


def snippet(text: str | None, width: int) -> str:
    """Short one-line form of a message: without a leading `🤖 [..]`, whitespace collapsed, cut at a word.

    Args:
        text: Message text (None counts as empty).
        width: Maximum length before the ellipsis.

    Returns:
        The text when it fits; otherwise the longest prefix ending at a space within `width + 1` characters (or
        exactly `width` characters when there is no space), trailing `,.;:!?` removed, plus "…".
    """
    t = " ".join(HOUSE_PREFIX.sub("", text or "").split())
    if len(t) <= width:
        return t
    cut = t[:width + 1]
    space = cut.rfind(" ")
    cut = cut[:space] if space > 0 else t[:width]
    return cut.rstrip(",.;:!? ") + "…"


def fmt_size(n: int | None) -> str:
    """Human file size: `500 B`, `84 KB`, `3.0 MB`; `? B` when unknown."""
    if n is None:
        return "? B"
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{round(n / 1024)} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def display_name(ev: dict) -> str:
    """Name shown for an event's author: `🤖 house` for messages house sent, else the sender's name."""
    if ev.get("dir") == "out":
        return "🤖 house"
    return str((ev.get("sender") or {}).get("name") or "?")
