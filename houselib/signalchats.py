"""Which Signal chats house keeps and lets agents read, and their short aliases.

The allowed set is the union of: Note to Self (always, alias `self`); the groups named by `notify.signal_group` of
**running** contests (alias = `notify.alias` or the contest name); the groups listed in
`~/.config/house/signal.toml` (S2). Everything else is dropped by `signalparse.extract` before it is written.
Only a human widens the set (`house signal allow`, which needs a terminal).
"""
import json
import os
import re
import tomllib
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from . import notify

SELF = "self"
RESERVED = {"self", "all"}
ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
HEADER = ("# Grupy Signala, które house zapisuje i pozwala czytać agentom. "
          "Zmieniaj przez `house signal allow/deny`.\n")
_problems: list[str] = []


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


def config_path() -> Path:
    """The allowed-groups file: $HOUSE_SIGNAL_CONFIG, else ~/.config/house/signal.toml (outside every repo)."""
    return Path(os.environ.get("HOUSE_SIGNAL_CONFIG") or Path.home() / ".config/house/signal.toml")


def load_config() -> dict[str, dict]:
    """Read `signal.toml`: alias → {id, name, added, [retention_days], [contests]}.

    A missing file is an empty config. An unreadable file, an entry without a string `id` or with an invalid
    alias is skipped and described in `config_problems()`; nothing raises.
    """
    _problems.clear()
    path = config_path()
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        _problems.append(f"{path}: nie da się odczytać ({e})")
        return {}
    groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}
    out = {}
    for alias, entry in groups.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]:
            _problems.append(f"{path}: grupa `{alias}` bez `id`, pominięta")
        elif not ALIAS_RE.match(alias) or alias in RESERVED:
            _problems.append(f"{path}: niepoprawny alias `{alias}`, pominięty")
        else:
            out[alias] = dict(entry)
    return out


def config_problems() -> list[str]:
    """Problems found while reading `signal.toml` (for `house doctor`)."""
    load_config()
    return list(_problems)


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)  # JSON string escapes are valid TOML basic strings


def save_config(groups: dict[str, dict]) -> None:
    """Write `signal.toml` atomically (mode 0600; a newly created directory gets 0700).

    Args:
        groups: alias → entry, as returned by `load_config`. Keys of each entry are written in order.
    """
    path = config_path()
    if not path.parent.exists():
        path.parent.mkdir(parents=True)
        path.parent.chmod(0o700)
    body = HEADER + "".join(f"\n[groups.{alias}]\n" + "".join(f"{k} = {_toml_value(v)}\n" for k, v in entry.items())
                            for alias, entry in groups.items())
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, body.encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def slug(name: str) -> str:
    """Default alias for a group title: ASCII lowercase letters, digits and `-`, at most 20 characters."""
    text = unicodedata.normalize("NFKD", name.replace("ł", "l").replace("Ł", "L"))
    text = text.encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:20].strip("-") or "grupa"


def validate_alias(alias: str, gid: str, chats: dict[str, Chat]) -> str | None:
    """Check a new alias for group `gid` against the rules of spec §5; None when fine, else the reason (Polish).

    Args:
        alias: Proposed alias.
        gid: Group id it would name.
        chats: Currently allowed chats (`allowed()`), used for uniqueness and contest collisions.
    """
    if not ALIAS_RE.match(alias or ""):
        return f"alias `{alias}`: tylko małe litery, cyfry i `-` (np. techarena)"
    if alias in RESERVED:
        return f"alias `{alias}` jest zarezerwowany"
    for chat in chats.values():
        if chat.alias == alias and chat.id != gid:
            return f"alias `{alias}` jest już użyty ({chat.source}: {chat.name})"
    for other, entry in load_config().items():
        if other == alias and entry["id"] != gid:
            return f"alias `{alias}` jest już w signal.toml dla innej grupy"
    return None


def collisions() -> list[str]:
    """Entries of `signal.toml` skipped because their alias is a running contest's alias for another group."""
    contest_chats = [c for c in _allowed_contests(None, None).values() if c.id != SELF]
    out = []
    for alias, entry in load_config().items():
        for c in contest_chats:
            if c.alias == alias and c.id != entry["id"]:
                out.append(f"alias `{alias}` z signal.toml koliduje z {c.source} (inna grupa); wpis pominięty")
    return out + list(_problems)


def allowed(sig=None, contests: list[Path] | None = None) -> dict[str, Chat]:
    """Return the allowed chats keyed by chat id ("self" or group id).

    Note to Self, the groups of running contests and the groups of `signal.toml`. A `signal.toml` group that is
    also a contest group keeps the contest's alias (its own alias still resolves); a `signal.toml` alias equal
    to a contest alias of another group is skipped (`collisions()` reports it).

    Args:
        sig: A `Signal` client used to resolve group names it has not cached yet. None resolves only from the
            `groups.json` cache and never runs signal-cli.
        contests: Contest paths to consider; ended ones are skipped. Defaults to None, meaning every registered
            running contest (`inbox.all_running()`).

    Returns:
        Chat id → `Chat`. Always contains "self".
    """
    out = _allowed_contests(sig, contests)
    for alias, entry in load_config().items():
        gid = entry["id"]
        if gid in out or any(c.alias == alias for c in out.values()):
            continue
        out[gid] = Chat(gid, alias, str(entry.get("name") or alias), "signal.toml")
    return out


def _allowed_contests(sig, contests: list[Path] | None) -> dict[str, Chat]:
    """Note to Self plus the groups of running contests (see `allowed`)."""
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


PHONE_RE = re.compile(r"^\+?\d[\d\s-]{5,}$")


def resolve(name: str, sig=None) -> Chat:
    """Find an allowed chat by alias (case-insensitive) or "self".

    Args:
        name: Alias as shown by `house signal groups`, or "self".
        sig: A `Signal` client to resolve contest groups not cached yet; None uses the cache only.

    Returns:
        The `Chat`.

    Raises:
        ValueError: (Polish message) for `all`, a phone number (private chats are not supported) or an unknown
            alias.
    """
    if name.lower() == "all":
        raise ValueError("`all` to nie jedna rozmowa: podaj alias albo self")
    if PHONE_RE.match(name.strip()):
        raise ValueError("wiadomości prywatne (DM) nie są obsługiwane: podaj alias grupy albo self")
    chats = allowed(sig)
    for chat in chats.values():
        if chat.alias.lower() == name.lower():
            return chat
    for alias, entry in load_config().items():
        if alias == name.lower() and entry["id"] in chats:
            return chats[entry["id"]]
    names = ", ".join(sorted(c.alias for c in chats.values()))
    raise ValueError(f"nieznana rozmowa `{name}`; dozwolone: {names} (lista: house signal groups)")
