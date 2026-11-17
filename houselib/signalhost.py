"""Which computer house runs on, when one Signal account is linked to several of them.

Every linked device receives every message, so each house sees every `@claude`, `/status` and `/agent on`. Every
house message names its host (`🤖 [<alias>@<host>]`) and the S7 dispatcher handles only events addressed to its
host: `@claude@<host>` / `/cmd@<host>`, a reply to that host's message, else the shared default `agent_host`.

Config (top-level keys of `signal.toml`, per computer): `host = "dom"`, `agent_host = "dom"`. Spec:
`docs/specs/2026-11-17-signal-host-routing-design.md`. No imports from the rest of house (`notify` uses it).
"""
import os
import re
import socket
import tomllib
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9-]{1,16}$")
PREFIX_RE = re.compile(r"^🤖 \[([^\]@]*)(?:@([a-z0-9-]{1,16}))?\]")


def config_path() -> Path:
    """The Signal config file: $HOUSE_SIGNAL_CONFIG, else ~/.config/house/signal.toml (outside every repo)."""
    return Path(os.environ.get("HOUSE_SIGNAL_CONFIG") or Path.home() / ".config/house/signal.toml")


def top_keys() -> dict:
    """Top-level keys of `signal.toml`; {} when the file is missing or unreadable (signalchats reports that)."""
    try:
        data = tomllib.loads(config_path().read_text())
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return {}
    return {k: v for k, v in data.items() if k in ("host", "agent_host")}


def _valid(value) -> str | None:
    return value if isinstance(value, str) and NAME_RE.match(value) else None


def _from_hostname() -> str:
    raw = socket.gethostname().split(".")[0].lower()
    name = re.sub(r"[^a-z0-9-]+", "-", raw).strip("-")[:16].strip("-")
    return name or "host"


def this_host() -> str:
    """This computer's name: $HOUSE_HOST, else `host` in signal.toml, else the hostname (lower case, `[a-z0-9-]`,
    up to the first dot, at most 16 characters). An invalid configured name falls back to the next source."""
    return _valid(os.environ.get("HOUSE_HOST")) or _valid(top_keys().get("host")) or _from_hostname()


def agent_host() -> str | None:
    """The host that handles events with no host named (`agent_host` in signal.toml); None when unset or invalid."""
    return _valid(top_keys().get("agent_host"))


def problems() -> list[str]:
    """Invalid `host` / `agent_host` values in signal.toml (for `house doctor`)."""
    top = top_keys()
    return [f"{config_path()}: `{key}` = {top[key]!r} nie pasuje do [a-z0-9-]{{1,16}}, pomijam"
            for key in ("host", "agent_host") if key in top and not _valid(top[key])]


def prefix(alias: str) -> str:
    """Message prefix `🤖 [<alias>@<host>]` that marks a message as coming from house on this computer."""
    return f"🤖 [{alias}@{this_host()}]"


def parse_prefix(text: str | None) -> tuple[str, str | None] | None:
    """(alias, host) of a house message (`🤖 [alias@host] …`); host None for the old prefix without a host;
    None when the text does not start with a house prefix."""
    m = PREFIX_RE.match(text or "")
    return (m[1], m[2]) if m else None


def addressed_to_me(target: str | None) -> bool:
    """Whether this host handles an event addressed to `target` (None: no host named or implied).

    No host named goes to `agent_host`; with no `agent_host` configured every host handles it (single-device
    behavior, `house doctor` warns).
    """
    target = target or agent_host()
    return target is None or target == this_host()
