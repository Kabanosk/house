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
* `receive` consumes messages on the signal-cli side. Only `signalstore.pull` may call it: it writes every object
  to the store before the next line is read. Never call `Signal.receive` anywhere else and never run
  `signal-cli … receive|send` by hand or from an agent: both bypass the store (received messages are lost,
  sent ones are missing from the history).
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import evalmode

CONFIG = Path.home() / ".config/house/notify.env"
FALLBACK_BINARY = Path.home() / ".local/bin/signal-cli"
TIMEOUT_S = 60
ATTACHMENTS = Path.home() / ".local/share/signal-cli/attachments"
ERROR_MAX = 200


@dataclass
class ReceiveResult:
    """Outcome of one `Signal.receive` run.

    Attributes:
        objects: JSON objects handed to the callback.
        bad_lines: Non-empty stdout lines that were not a JSON object (their content is never kept anywhere).
        error: None on success, else "signal-cli not found", "timeout", "read: <reason>" or the first stderr line
            that is not INFO (cut to 200 characters) when signal-cli exits non-zero.
        returncode: Exit code of signal-cli, None if it never started.
        stopped: True when `should_stop` asked to end early (the process was terminated, its output drained).
    """

    objects: int = 0
    bad_lines: int = 0
    error: str | None = None
    returncode: int | None = None
    stopped: bool = False


PHONE_RE = re.compile(r"\+?\d[\d ]{6,}\d")
UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")


def _error_line(stderr: str) -> str | None:
    """The line that explains a failure: the first ERROR line, else the first non-INFO line, else the first line.

    Phone numbers and UUIDs are replaced (`+…`, `<uuid>`): a warning may name a sender from a private chat, and
    nothing from a private chat may be stored, even in an error. Cut to ERROR_MAX characters; None when empty.
    """
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
    useful = [ln for ln in lines if "ERROR" in ln] or [ln for ln in lines if not ln.startswith("INFO")] or lines
    if not useful:
        return None
    return UUID_RE.sub("<uuid>", PHONE_RE.sub("+…", useful[0]))[:ERROR_MAX]


class Signal:
    """Client for one Signal account driven through the `signal-cli` binary.

    Attributes:
        account: Account phone number in international form (e.g. "+48123456789"), passed as `-a`.
        binary: Path or name of the `signal-cli` executable.
        timeout: Default per-call timeout in seconds (hooks lower it so a session start stays quick).
    """

    def __init__(self, account: str, binary: str, run=subprocess.run, timeout: int = TIMEOUT_S,
                 popen=subprocess.Popen, attachments_dir: Path | None = None):
        """Store the account and the way to run `signal-cli`; nothing is executed here.

        Args:
            account: Account phone number in international form, e.g. "+48123456789".
            binary: Path or name of the `signal-cli` executable.
            run: Callable with the signature of `subprocess.run`; replace it in tests with a fake. Defaults to
                `subprocess.run`.
            timeout: Default timeout in seconds for one `signal-cli` call. Defaults to TIMEOUT_S (60).
            popen: Callable with the signature of `subprocess.Popen`, used by `receive`; replaceable in tests.
            attachments_dir: Where signal-cli stores downloaded attachments. Defaults to
                $HOUSE_SIGNAL_ATTACHMENTS, else ~/.local/share/signal-cli/attachments.
        """
        self.account, self.binary, self._run, self.timeout = account, binary, run, timeout
        self._popen = popen
        self.attachments_dir = Path(attachments_dir or os.environ.get("HOUSE_SIGNAL_ATTACHMENTS") or ATTACHMENTS)

    def _call(self, *args: str, timeout: int | None = None) -> subprocess.CompletedProcess | None:
        """Run `signal-cli -a <account> <args>`; None if it cannot start, rc -1 with partial stdout on timeout.

        None without running anything in eval mode (`HOUSE_EVAL=1`): the one place every `signal-cli` call but
        `receive` goes through.
        """
        if evalmode.active():
            return None
        argv = [self.binary, "-a", self.account, *args]
        try:
            return self._run(argv, capture_output=True, text=True, timeout=timeout or self.timeout, check=False,
                             stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired as e:  # keep what was already printed: those messages are acknowledged
            out = e.output.decode() if isinstance(e.output, bytes) else (e.output or "")
            return subprocess.CompletedProcess(argv, -1, out, "")
        except OSError:
            return None

    def send(self, text: str, group_id: str | None = None, *, attachments=(), quote: tuple[int, str] | None = None,
             meta: dict | None = None) -> int | None:
        """Send a message to Note to Self (with a notification) or to a group, and log it in the store.

        Every call, delivered or not, becomes an `out` event in the message store (`signalstore.record_out`), so a
        later quote-reply can be matched and the conversation history is complete. Logging never breaks a send.

        Args:
            text: Message body, sent verbatim (house's callers add the `🤖 [..]` prefix).
            group_id: Signal group id (base64, as returned by `list_groups`). When None or empty the message goes
                to the owner's Note to Self instead. Defaults to None.
            attachments: Paths of files to attach (`--attachment`, one per file). Defaults to none.
            quote: (timestamp, author uuid or number) of the message this one quotes. Defaults to None.
            meta: Stored with the event: `origin` (notify, ask, inbox-reply, deadline, night, cli; "python" when
                missing), `contest`, `question`. Defaults to None.

        Returns:
            Signal's message timestamp (milliseconds since the epoch), which is also the id a quote-reply refers
            to. None when `signal-cli` is missing, exits non-zero, times out or prints no timestamp, and in eval
            mode (`HOUSE_EVAL=1`): nothing is sent or stored, one `signal.send` line (chat, length) goes to the
            eval log.
        """
        if evalmode.active():
            evalmode.log("signal.send", chat=group_id or "self", chars=len(text))
            return None
        target = ["-g", group_id] if group_id else ["--note-to-self", "--notify-self"]
        extra = [x for path in attachments for x in ("--attachment", str(path))]
        if quote:
            extra += ["--quote-timestamp", str(quote[0]), "--quote-author", str(quote[1])]
        r = self._call("send", *target, "-m", text, *extra)
        ts = None
        if r is not None and r.returncode == 0:
            last = (r.stdout.strip().splitlines() or [""])[-1].strip()
            ts = int(last) if last.isdigit() else None
        try:
            from . import signalstore
            signalstore.record_out(self.account, group_id or "self", text, ts, attachments=attachments, quote=quote,
                                   meta={"origin": "python", **(meta or {})})
        except Exception:  # noqa: BLE001, S110 — the log must never turn a delivered message into a failure
            pass
        return ts

    def receive(self, handle: Callable[[dict], None], timeout: int = 1, max_messages: int | None = None,
                should_stop: Callable[[], bool] = lambda: False) -> ReceiveResult:
        """Stream pending messages to `handle`, one JSON object at a time; this consumes them in signal-cli.

        signal-cli acknowledges a message to the server when it prints it, so `handle` must persist each object
        before returning: the next line is read only afterwards. Only `signalstore.pull` calls this.

        Args:
            handle: Called with each parsed JSON object (an envelope wrapper), in output order. An exception from
                it kills signal-cli and propagates.
            timeout: Seconds signal-cli waits for new messages (`--timeout`). Defaults to 1. The whole process is
                killed after `timeout + self.timeout` seconds; what it printed until then is still handled.
            max_messages: Passed as `--max-messages` when set; the rest stays on the server. Defaults to None.
            should_stop: Checked after each object; when it returns True signal-cli is terminated and the output
                it already printed is drained and handled (nothing printed is dropped).

        Returns:
            A `ReceiveResult` with counts and the error, if any. Never raises for signal-cli problems. An empty
            result in eval mode (`HOUSE_EVAL=1`), with one `signal.pull` line in the eval log.
        """
        if evalmode.active():
            evalmode.log("signal.pull")
            return ReceiveResult()
        argv = [self.binary, "-a", self.account, "-o", "json", "receive", "--timeout", str(timeout)]
        if max_messages:
            argv += ["--max-messages", str(max_messages)]
        argv.append("--ignore-stories")
        res = ReceiveResult()
        with tempfile.TemporaryFile() as err:
            try:
                proc = self._popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=err)
            except OSError:
                res.error = "signal-cli not found"
                return res
            timed_out = threading.Event()

            def kill():
                timed_out.set()
                proc.kill()

            timer = threading.Timer(timeout + self.timeout, kill)
            timer.daemon = True
            timer.start()
            try:
                lines = iter(proc.stdout)
                while True:
                    try:
                        raw = next(lines)
                    except StopIteration:
                        break
                    except OSError as e:
                        res.error = f"read: {e}"
                        break
                    line = raw.decode("utf-8", errors="replace").strip() if isinstance(raw, bytes) else raw.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        obj = None
                    if not isinstance(obj, dict):
                        res.bad_lines += 1
                        continue
                    res.objects += 1
                    handle(obj)
                    if not res.stopped and should_stop():
                        res.stopped = True
                        proc.terminate()
                res.returncode = proc.wait()
            except BaseException:
                proc.kill()
                proc.wait()
                raise
            finally:
                timer.cancel()
            if timed_out.is_set():
                res.error = "timeout"
            elif res.error is None and res.returncode not in (0, None) and not res.stopped:
                err.seek(0)
                res.error = _error_line(err.read().decode("utf-8", errors="replace")) or f"exit {res.returncode}"
        return res

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

    def group_members(self, group_id: str) -> list[str]:
        """Members of one group as ACIs (uuid), else numbers; [] when signal-cli fails (used for the S7 snapshot).

        Args:
            group_id: Signal group id.
        """
        r = self._call("-o", "json", "listGroups", "-d")
        try:
            groups = json.loads(r.stdout) if r is not None and r.returncode == 0 else []
        except ValueError:
            return []
        group = next((g for g in groups if isinstance(g, dict) and g.get("id") == group_id), None) or {}
        return [str(m.get("uuid") or m.get("number")) for m in group.get("members") or []
                if isinstance(m, dict) and (m.get("uuid") or m.get("number"))]


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
