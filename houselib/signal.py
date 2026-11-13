"""Thin wrapper around `signal-cli` (linked device on the owner's number).

Every call degrades to "nothing happened" (None / []) on failure: notifications must never break a run.
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
    def __init__(self, account: str, binary: str, run=subprocess.run):
        self.account, self.binary, self._run = account, binary, run

    def _call(self, *args: str, timeout: int = TIMEOUT_S) -> subprocess.CompletedProcess | None:
        try:
            return self._run([self.binary, "-a", self.account, *args], capture_output=True, text=True,
                             timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None

    def send(self, text: str, group_id: str | None = None) -> int | None:
        """Send to Note to Self (with a notification) or to a group; returns Signal's message timestamp."""
        target = ["-g", group_id] if group_id else ["--note-to-self", "--notify-self"]
        r = self._call("send", *target, "-m", text)
        if r is None or r.returncode != 0:
            return None
        last = (r.stdout.strip().splitlines() or [""])[-1].strip()
        return int(last) if last.isdigit() else None

    def receive(self, timeout: int = 1) -> list[dict]:
        r = self._call("-o", "json", "receive", "--timeout", str(timeout), timeout=timeout + TIMEOUT_S)
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
        r = self._call("-o", "json", "listGroups")
        try:
            groups = json.loads(r.stdout) if r is not None and r.returncode == 0 else []
        except ValueError:
            return []
        return [{"id": g.get("id"), "name": g.get("name")} for g in groups if isinstance(g, dict)]


def _config_path(path: Path | None) -> Path:
    return path or Path(os.environ.get("HOUSE_NOTIFY_CONFIG") or CONFIG)


def read_config(path: Path | None = None) -> dict[str, str]:
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
    cfg = read_config(path)
    if cfg.get("HOUSE_NOTIFY_CHANNEL") != "signal" or not cfg.get("HOUSE_SIGNAL_ACCOUNT"):
        return None
    binary = which("signal-cli") or (str(fallback) if fallback.is_file() else None)
    return Signal(cfg["HOUSE_SIGNAL_ACCOUNT"], binary) if binary else None
