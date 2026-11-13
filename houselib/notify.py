"""Notifications: one Signal message to the owner's Note to Self and to the contest's team group.

Every sent message is remembered (`sent.jsonl`: timestamp → contest, question) so a quote-reply can be matched
to its question. No Signal configured → nothing happens, nothing fails.
"""
import json
import os
from datetime import datetime
from pathlib import Path

from . import questions, registry
from . import spec as specmod
from .signal import from_config


def data_dir() -> Path:
    d = Path(os.environ.get("HOUSE_DATA_DIR") or Path.home() / ".local/share/house")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _notify_spec(contest: Path) -> dict:
    value = specmod.get(specmod.load_spec(contest / ".house"), "notify")
    return value if isinstance(value, dict) else {}


def contest_name(contest: Path) -> str:
    return registry.name_for(contest)


def alias(contest: Path) -> str:
    return str(_notify_spec(contest).get("alias") or contest_name(contest))


def prefix(contest: Path) -> str:
    return f"🤖 [{alias(contest)}]"


def _append(name: str, record: dict) -> None:
    path = data_dir() / name
    with path.open("a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    path.chmod(0o600)


def resolve_group(sig, name: str) -> tuple[str | None, str | None]:
    """(group id, warning). Cached by name; refreshed from signal-cli on a miss."""
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
    """Send every open question that has not been sent yet (one message each)."""
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
