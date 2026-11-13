"""Thin wrapper around `signal-cli` (linked device on the owner's number).

Every call degrades to "nothing happened" (None / []) on failure: notifications must never break a run.

Used by `house notify`, `house ask`, `house inbox`, `house deadlines` and the session hooks, always through
`notify` (sending) and `inbox` (receiving); agents do not call this module directly. Rules:

* Signal is optional. `from_config()` returns None unless ~/.config/house/notify.env (or the file named by
  HOUSE_NOTIFY_CONFIG) sets HOUSE_NOTIFY_CHANNEL=signal and HOUSE_SIGNAL_ACCOUNT=+<number>. None means
  "notifications are off": callers must treat it as a normal state, not an error.
* The account is a linked device of the owner's own number, so messages to "Note to Self" notify the owner.
* Never run `signal-cli` by hand or from tests against the real account; pass a fake `run` to `Signal`, or set
  HOUSE_NOTIFY_CONFIG to a nonexistent path so no client is created.
* `receive` consumes messages on the signal-cli side: whatever it returns must be persisted before use
  (see `inbox.pull`).
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

CONFIG = Path.home() / ".config/house/notify.env"
FALLBACK_BINARY = Path.home() / ".local/bin/signal-cli"
TIMEOUT_S = 60


class Signal:
    """Client for one Signal account driven through the `signal-cli` binary.

    Attributes:
        account: Account phone number in international form (e.g. "+48123456789"), passed as `-a`.
        binary: Path or name of the `signal-cli` executable.
        timeout: Default per-call timeout in seconds (hooks lower it so a session start stays quick).
    """

    def __init__(self, account: str, binary: str, run=subprocess.run, timeout: int = TIMEOUT_S):
        """Store the account and the way to run `signal-cli`; nothing is executed here.

        Args:
            account: Account phone number in international form, e.g. "+48123456789".
            binary: Path or name of the `signal-cli` executable.
            run: Callable with the signature of `subprocess.run`; replace it in tests with a fake. Defaults to
                `subprocess.run`.
            timeout: Default timeout in seconds for one `signal-cli` call. Defaults to TIMEOUT_S (60).
        """
        self.account, self.binary, self._run, self.timeout = account, binary, run, timeout

    def _call(self, *args: str, timeout: int | None = None) -> subprocess.CompletedProcess | None:
        """Run `signal-cli -a <account> <args>`; None if it cannot start, rc -1 with partial stdout on timeout."""
        argv = [self.binary, "-a", self.account, *args]
        try:
            return self._run(argv, capture_output=True, text=True, timeout=timeout or self.timeout, check=False)
        except subprocess.TimeoutExpired as e:  # keep what was already printed: those messages are acknowledged
            out = e.output.decode() if isinstance(e.output, bytes) else (e.output or "")
            return subprocess.CompletedProcess(argv, -1, out, "")
        except OSError:
            return None

    def send(self, text: str, group_id: str | None = None) -> int | None:
        """Send a text message to Note to Self (with a notification) or to a group.

        Args:
            text: Message body, sent verbatim.
            group_id: Signal group id (base64, as returned by `list_groups`). When None or empty the message goes
                to the owner's Note to Self instead. Defaults to None.

        Returns:
            Signal's message timestamp (milliseconds since the epoch), which is also the id a quote-reply refers
            to. None when `signal-cli` is missing, exits non-zero, times out or prints no timestamp.
        """
        target = ["-g", group_id] if group_id else ["--note-to-self", "--notify-self"]
        r = self._call("send", *target, "-m", text)
        if r is None or r.returncode != 0:
            return None
        last = (r.stdout.strip().splitlines() or [""])[-1].strip()
        return int(last) if last.isdigit() else None

    def receive(self, timeout: int = 1) -> list[dict]:
        """Fetch pending messages from the account; this consumes them on the signal-cli side.

        Args:
            timeout: Seconds `signal-cli` waits for new messages before returning. Defaults to 1. The process
                itself may run `timeout` + `self.timeout` seconds; on expiry the output printed so far is kept.

        Returns:
            One parsed JSON object (an envelope wrapper) per output line; unparsable and non-object lines are
            skipped. An empty list when `signal-cli` cannot be started or prints nothing.
        """
        r = self._call("-o", "json", "receive", "--timeout", str(timeout), timeout=timeout + self.timeout)
        if r is None:
            return []
        out = []
        for line in r.stdout.splitlines():
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
        return out

    def list_groups(self) -> list[dict]:
        """List the Signal groups the account belongs to.

        Returns:
            Dicts with keys "id" (group id) and "name" (group title, may be None) for every group; an empty list
            if `signal-cli` fails, cannot be started or prints invalid JSON.
        """
        r = self._call("-o", "json", "listGroups")
        try:
            groups = json.loads(r.stdout) if r is not None and r.returncode == 0 else []
        except ValueError:
            return []
        return [{"id": g.get("id"), "name": g.get("name")} for g in groups if isinstance(g, dict)]


def _config_path(path: Path | None) -> Path:
    """Pick the config file: explicit `path`, else $HOUSE_NOTIFY_CONFIG, else CONFIG."""
    return path or Path(os.environ.get("HOUSE_NOTIFY_CONFIG") or CONFIG)


def read_config(path: Path | None = None) -> dict[str, str]:
    """Parse the notify config file, a shell-like list of `KEY=value` lines.

    Blank lines, lines starting with `#` and lines without `=` are ignored; surrounding single or double quotes
    around a value are stripped.

    Args:
        path: Config file to read. Defaults to $HOUSE_NOTIFY_CONFIG if set, else ~/.config/house/notify.env.

    Returns:
        Mapping of key to value; an empty dict when the file does not exist.
    """
    path = _config_path(path)
    if not path.is_file():
        return {}
    out = {}
    for line in path.read_text().splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and not key.startswith("#"):
            out[key.strip()] = value.strip().strip("\"'")
    return out


def from_config(path: Path | None = None, which=shutil.which, fallback: Path = FALLBACK_BINARY) -> "Signal | None":
    """Build a `Signal` client from the notify config, if Signal is configured and `signal-cli` is installed.

    Needs HOUSE_NOTIFY_CHANNEL=signal and a non-empty HOUSE_SIGNAL_ACCOUNT in the config.

    Args:
        path: Config file, as for `read_config`. Defaults to $HOUSE_NOTIFY_CONFIG or ~/.config/house/notify.env.
        which: Callable that finds an executable on PATH (signature of `shutil.which`); replaceable in tests.
        fallback: Binary used when `signal-cli` is not on PATH. Defaults to ~/.local/bin/signal-cli.

    Returns:
        A `Signal` client, or None when Signal is not configured or no binary is found (notifications off).
    """
    cfg = read_config(path)
    if cfg.get("HOUSE_NOTIFY_CHANNEL") != "signal" or not cfg.get("HOUSE_SIGNAL_ACCOUNT"):
        return None
    binary = which("signal-cli") or (str(fallback) if fallback.is_file() else None)
    return Signal(cfg["HOUSE_SIGNAL_ACCOUNT"], binary) if binary else None
