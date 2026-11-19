"""Eval mode: with `HOUSE_EVAL=1` every outward side effect becomes a no-op that is logged instead.

Eval runs start real agent sessions in a sandboxed copy of house, and house's hooks would otherwise message the
owner's phone. In eval mode nothing is sent to or received from Signal, `inbox` processes nothing, and `notify`
(`send`, `ask_new`) reports "nothing sent"; each skipped action appends one JSON line to the eval log. Rules:

* The guards live at the choke points, not in the callers: `Signal._call`, `Signal.send` and `Signal.receive` (the only
  code that starts `signal-cli`), `signalstore.pull`, `inbox.process`, `notify.send` and `notify.ask_new`, plus the
  SessionStart Signal lines and `signalagent.tick`. A new caller of those inherits the guard.
* Fail-safe: eval mode is on for any `HOUSE_EVAL` value except unset, "" and "0" ("1", "true", "yes", a typo ...);
  a spelling slip must never let an eval session message the owner. Only unset/""/"0" leave behaviour unchanged.
* The log is `$HOUSE_EVAL_LOG`, default `<data dir>/eval.log`, one `{"time", "event", ...}` object per line. Message
  text is never logged, only its length; field values are cut to 300 characters and passed through `guard.redact`.
* `log` never raises: a broken log must not turn a no-op into a failure.
"""
import json
import os
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

FIELD_MAX = 300


def active(env: Mapping[str, str] = os.environ) -> bool:
    """Tell whether eval mode is on.

    Args:
        env: Environment to read. Defaults to `os.environ`.

    Returns:
        False when `HOUSE_EVAL` is unset, "" or "0"; True for every other value (fail-safe).
    """
    return env.get("HOUSE_EVAL", "") not in ("", "0")


def log_path() -> Path:
    """The eval log: $HOUSE_EVAL_LOG, else `eval.log` in $HOUSE_DATA_DIR (default ~/.local/share/house)."""
    explicit = os.environ.get("HOUSE_EVAL_LOG")
    if explicit:
        return Path(explicit)
    return Path(os.environ.get("HOUSE_DATA_DIR") or Path.home() / ".local/share/house") / "eval.log"


def _clean(value) -> object:
    """A JSON-safe field value: numbers and booleans stay, everything else becomes redacted text cut to 300."""
    if value is None or isinstance(value, bool | int | float):
        return value
    from . import guard
    return guard.redact(str(value))[:FIELD_MAX]


def log(event: str, **fields) -> None:
    """Append one line `{"time", "event", **fields}` to the eval log; never raises.

    Args:
        event: Dotted event name, for example "signal.send" or "inbox.process".
        **fields: Extra values (chat, chars, origin ...). Strings are redacted and cut to 300 characters; pass
            lengths, never message bodies.
    """
    try:
        record = {"time": datetime.now().astimezone().isoformat(timespec="seconds"), "event": event,
                  **{k: _clean(v) for k, v in fields.items()}}
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001, S110 — see the module docstring
        pass
