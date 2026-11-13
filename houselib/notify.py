"""Notifications: one Signal message to the owner's Note to Self and to the contest's team group.

Every sent message is remembered (`sent.jsonl`: timestamp → contest, question) so a quote-reply can be matched
to its question. No Signal configured → nothing happens, nothing fails.

Used by `house notify "<text>"` (free text), `house ask [Q-###]` (questions from .house/QUESTIONS.md), the
PostToolUse hook (sends new open questions right after QUESTIONS.md is edited) and `inbox` (replies to answers).
Agents normally just add an `open` entry to QUESTIONS.md and let the hook send it. Rules:

* Message format: `🤖 [<alias>] <text>`, where alias is spec `notify.alias` or the contest's registry name.
  Question messages start with the id and title (`Q-007 Title`) and are written in Polish, like the file.
* Recipients: the owner's Note to Self always, plus the Signal group named by spec `notify.signal_group`
  (when set and found). A missing or ambiguous group only produces a warning.
* Files in the data dir (~/.local/share/house, or $HOUSE_DATA_DIR; created on demand, JSON lines chmod 600):
  `sent.jsonl` (ts, contest, question, chat), `groups.json` (cache: group name → id).
* Per contest, `.house/state.json` records `sent_questions` (question id → ISO time of sending) so a question
  is sent once and `questions.due` can compute half-time reminders.
* Nothing here raises when Signal is off or unreachable; check the returned lists instead.
"""
import json
import os
from datetime import datetime
from pathlib import Path

from . import questions, registry
from . import spec as specmod
from .signal import from_config


def data_dir() -> Path:
    """Return the per-user state directory for house (inbox, sent log, caches), creating it if missing.

    Returns:
        $HOUSE_DATA_DIR if set, else ~/.local/share/house.
    """
    d = Path(os.environ.get("HOUSE_DATA_DIR") or Path.home() / ".local/share/house")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _notify_spec(contest: Path) -> dict:
    """Return the `notify` mapping of the contest's .house spec, or {} if absent or not a mapping."""
    value = specmod.get(specmod.load_spec(contest / ".house"), "notify")
    return value if isinstance(value, dict) else {}


def contest_name(contest: Path) -> str:
    """Return the contest's registry name (also for resolved paths of adopted repos), else its folder name.

    Args:
        contest: Path to the contest repository (the directory that holds `.house`).

    Returns:
        Registry name, the key stored in `sent.jsonl` and used to route quote-replies.
    """
    return registry.name_for(contest)


def alias(contest: Path) -> str:
    """Return the short name shown in messages and typed answers (`<alias> Q-007: B`).

    Args:
        contest: Path to the contest repository.

    Returns:
        Spec value `notify.alias` if set, else the contest's registry name.
    """
    return str(_notify_spec(contest).get("alias") or contest_name(contest))


def prefix(contest: Path) -> str:
    """Return the message prefix `🤖 [<alias>]` that marks a message as coming from house for this contest."""
    return f"🤖 [{alias(contest)}]"


def _append(name: str, record: dict) -> None:
    """Append `record` as one JSON line to `name` in the data dir and restrict the file to the owner (0600)."""
    path = data_dir() / name
    with path.open("a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    path.chmod(0o600)


def resolve_group(sig, name: str) -> tuple[str | None, str | None]:
    """Find the Signal group id for a group name, using `groups.json` as a cache.

    A cache hit does not call signal-cli; a miss asks `sig.list_groups()` and caches a unique match.

    Args:
        sig: A `Signal` client (or any object with `list_groups()` returning dicts with "id" and "name").
        name: Exact group title, as set in spec `notify.signal_group`.

    Returns:
        (group id, None) when found, else (None, warning text) when no group or several groups have that name.
    """
    cache_path = data_dir() / "groups.json"
    try:
        cache = json.loads(cache_path.read_text())
    except (OSError, ValueError):
        cache = {}
    if name in cache:
        return cache[name], None
    matches = [g["id"] for g in sig.list_groups() if g.get("name") == name and g.get("id")]
    if len(matches) != 1:
        why = "not found" if not matches else "ambiguous (several groups with this name)"
        return None, f"Signal group {name!r} {why}; sent to Note to Self only"
    cache[name] = matches[0]
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n")
    return matches[0], None


def send(contest: Path, text: str, question: str | None = None, sig=None,
         group: bool = True) -> tuple[list[int], list[str]]:
    """Send `text` for a contest to the owner's Note to Self and, if configured, to the contest's group.

    The body is `prefix(contest) + " " + text`. Each delivered message is logged in `sent.jsonl` so a later
    quote-reply can be routed back to `question`.

    Args:
        contest: Path to the contest repository; selects alias and group from its spec.
        text: Message text, without the prefix.
        question: Question id (e.g. "Q-007") this message asks, stored for reply routing. Defaults to None.
        sig: A `Signal` client. Defaults to None, meaning `from_config()` (the machine's Signal config).
        group: False to skip the team group and write to Note to Self only. Defaults to True.

    Returns:
        (timestamps, warnings): Signal timestamps of delivered messages (empty if nothing was sent) and
        human-readable warnings (unknown/ambiguous group, failed delivery). ([], []) when Signal is not
        configured.
    """
    sig = sig or from_config()
    if sig is None:
        return [], []
    body = f"{prefix(contest)} {text}"
    targets: list[tuple[str, str | None]] = [("self", None)]
    warnings = []
    group_name = _notify_spec(contest).get("signal_group")
    if group and group_name:
        gid, warning = resolve_group(sig, str(group_name))
        if gid:
            targets.append((gid, gid))
        if warning:
            warnings.append(warning)
    sent = []
    for chat, gid in targets:
        ts = sig.send(body, group_id=gid)
        if ts is None:
            warnings.append(f"sending to {chat} failed")
            continue
        sent.append(ts)
        _append("sent.jsonl", {"ts": ts, "contest": contest_name(contest), "question": question, "chat": chat})
    return sent, warnings


def question_message(q: questions.Question) -> str:
    """Format a question for Signal (Polish): id and title, options, recommendation, default and reply hint.

    Args:
        q: Parsed question from QUESTIONS.md. The default line appears only when both default and deadline are set.

    Returns:
        Multi-line message text without the `🤖 [alias]` prefix.
    """
    lines = [f"{q.id} {q.title}"]
    if q.options:
        lines.append(q.options)
    if q.recommendation:
        lines.append(f"Rekomendacja: {q.recommendation}")
    if q.default and q.deadline:
        lines.append(f"Domyślnie {q.default} o {q.deadline:%Y-%m-%d %H:%M}, jeśli nikt nie odpowie.")
    lines.append("↩ odpowiedz cytatem: " + ("A / B / własny tekst" if q.options else "krótka odpowiedź"))
    return "\n".join(lines)


def ask(contest: Path, q: questions.Question, sig=None) -> list[int]:
    """Send one question and, if it was delivered, mark it as sent in `.house/state.json`.

    Args:
        contest: Path to the contest repository.
        q: Question to send, as returned by `questions.parse`.
        sig: A `Signal` client. Defaults to None, meaning `from_config()`.

    Returns:
        Signal timestamps of the delivered messages; empty when nothing was sent (state is left untouched).
    """
    sent, _ = send(contest, question_message(q), question=q.id, sig=sig)
    if sent:
        state_path = contest / ".house/state.json"
        try:
            state = json.loads(state_path.read_text())
        except (OSError, ValueError):
            state = {}
        state.setdefault("sent_questions", {})[q.id] = datetime.now().isoformat(timespec="seconds")
        state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")
    return sent


def ask_new(contest: Path, sig=None) -> list[str]:
    """Send every open question that has not been sent yet (one message each).

    Args:
        contest: Path to the contest repository; reads `.house/QUESTIONS.md` and `.house/state.json`.
        sig: A `Signal` client. Defaults to None, meaning `from_config()`.

    Returns:
        Ids of the questions that were delivered; empty when there is no QUESTIONS.md, Signal is not configured
        or nothing new is open.
    """
    path = contest / ".house/QUESTIONS.md"
    if not path.exists():
        return []
    sig = sig or from_config()
    if sig is None:
        return []
    try:
        already = json.loads((contest / ".house/state.json").read_text()).get("sent_questions", {})
    except (OSError, ValueError):
        already = {}
    out = []
    for q in questions.parse(path.read_text()):
        if q.status == "open" and q.id not in already and ask(contest, q, sig=sig):
            out.append(q.id)
    return out
