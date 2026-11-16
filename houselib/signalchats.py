"""Which Signal chats house keeps and lets agents read, and their short aliases.

The allowed set is the union of: Note to Self (always, alias `self`); the groups named by `notify.signal_group` of
**running** contests (alias = `notify.alias` or the contest name); the groups listed in
`~/.config/house/signal.toml` (S2). Everything else is dropped by `signalparse.extract` before it is written.
Only a human widens the set (`house signal allow`, which needs a terminal).
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import notify

SELF = "self"


@dataclass
class Chat:
    """One allowed conversation.

    Attributes:
        id: "self" or the Signal group id (base64).
        alias: Short name used in commands (`house signal read <alias>`) and stored as `chat_alias`.
        name: Group title (as configured or cached), "Notatka do siebie" for self.
        source: Why it is allowed: "zawsze", "konkurs <name>" or "signal.toml".
        contests: Running contests that use this group (several contests may share one group).
    """

    id: str
    alias: str
    name: str
    source: str
    contests: tuple[Path, ...] = field(default_factory=tuple)


def _group_id(sig, name: str) -> str | None:
    """Group id for a group title: through `notify.resolve_group` with a client, else only from `groups.json`."""
    if sig is not None:
        return notify.resolve_group(sig, name)[0]
    try:
        cache = json.loads((notify.data_dir() / "groups.json").read_text())
    except (OSError, ValueError):
        return None
    return cache.get(name) if isinstance(cache, dict) else None


def allowed(sig=None, contests: list[Path] | None = None) -> dict[str, Chat]:
    """Return the allowed chats keyed by chat id ("self" or group id).

    Args:
        sig: A `Signal` client used to resolve group names it has not cached yet. None resolves only from the
            `groups.json` cache and never runs signal-cli.
        contests: Contest paths to consider; ended ones are skipped. Defaults to None, meaning every registered
            running contest (`inbox.all_running()`).

    Returns:
        Chat id → `Chat`. Always contains "self".
    """
    from . import inbox
    from . import spec as specmod
    running = inbox.all_running() if contests is None else inbox.running(contests)
    out = {SELF: Chat(SELF, SELF, "Notatka do siebie", "zawsze")}
    for c in running:
        name = (specmod.get(specmod.load_spec(c / ".house"), "notify") or {}).get("signal_group")
        gid = _group_id(sig, str(name)) if name else None
        if not gid:
            continue
        if gid in out:
            out[gid].contests = (*out[gid].contests, c)
        else:
            out[gid] = Chat(gid, notify.alias(c), str(name), f"konkurs {notify.contest_name(c)}", (c,))
    return out


def alias_of(chat_id: str) -> str | None:
    """Alias of an allowed chat, from configuration and cache only (never runs signal-cli); None if not allowed."""
    chat = allowed(None).get(chat_id)
    return chat.alias if chat else None
