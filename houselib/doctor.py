"""Detect which tools are available on this machine.

Used by: `house doctor [contest]` (prints the summary and, with a contest, writes `.house/tools.json`),
`house setup`, `house adapt` and `house new`. The coordinator reads `.house/tools.json` to decide which
agents can run (codex and local-model tiers fall back to other agents when missing).
"""
import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path


def _run(cmd: list[str]) -> str | None:
    """Run a command with a 5 s timeout; return its stdout, or None when it fails, times out or is missing."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def _gpu(out: str | None) -> dict | None:
    """Parse the first line of `nvidia-smi` CSV output into `{name, vram_mib}`; None when absent or malformed."""
    if not out:
        return None
    name, sep, mem = out.splitlines()[0].rpartition(",")
    if not sep or not mem.strip().isdigit():
        return None
    return {"name": name.strip(), "vram_mib": int(mem)}


def detect(which=shutil.which, run=_run) -> dict:
    """Detect the available tools.

    Args:
        which: Function like `shutil.which` to look up executables; replace in tests.
        run: Function `(cmd list) -> stdout or None` used for `ollama list`, `dpkg-query` and `nvidia-smi`.

    Returns:
        A dict with booleans `claude`, `codex`, `cursor`, `uv`, `ruff`, `grok_bot`, `ollama`
        (`{"installed": bool, "models": [names]}`) and `gpu` (`{"name", "vram_mib"}` or None).
    """
    ollama = bool(which("ollama"))
    models = []
    if ollama:
        out = run(["ollama", "list"]) or ""
        models = [ln.split()[0] for ln in out.splitlines()[1:] if ln.strip()]
    return {
        "claude": bool(which("claude")),
        "codex": bool(which("codex")),
        "cursor": bool(which("cursor")),
        "uv": bool(which("uv")),
        "ruff": bool(which("ruff")),
        "grok_bot": bool(which("grok-bot")) or bool((run(["dpkg-query", "-W", "grok-bot"]) or "").strip()),
        "ollama": {"installed": ollama, "models": models},
        "gpu": _gpu(run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])),
    }


def write(contest_dir: Path, tools: dict) -> Path:
    """Write `.house/tools.json`.

    Args:
        contest_dir: Path of the contest checkout; `.house/` must exist.
        tools: The dict from `detect`; a `checked_at` timestamp is added.

    Returns:
        The path of the file.
    """
    path = contest_dir / ".house" / "tools.json"
    data = dict(tools, checked_at=datetime.now().astimezone().isoformat(timespec="seconds"))
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def summary(tools: dict) -> str:
    """Format the tools dict for display.

    Args:
        tools: The dict from `detect`.

    Returns:
        One line per tool, with ✓ or ✗, plus ollama models and the GPU.
    """
    mark = lambda ok: "✓" if ok else "✗"  # noqa: E731
    lines = [f"{mark(tools[k])} {k}" for k in ("claude", "codex", "cursor", "grok_bot", "uv", "ruff")]
    o = tools["ollama"]
    lines.append(f"{mark(o['installed'])} ollama  models: {', '.join(o['models']) or '(none)'}")
    g = tools["gpu"]
    lines.append(f"{mark(g)} gpu  " + (f"{g['name']} ({g['vram_mib']} MiB)" if g else "(none)"))
    return "\n".join(lines)


def alias_collisions(contests: list[Path]) -> dict[str, list[str]]:
    """Find Signal aliases (spec `notify.alias`) used by several running contests.

    Args:
        contests: Contest paths to examine; ended contests may reuse an alias and are ignored.

    Returns:
        Alias to sorted contest names, only for aliases used more than once.
    """
    from . import inbox, notify
    seen: dict[str, list[str]] = {}
    for c in inbox.running(contests):
        seen.setdefault(notify.alias(c), []).append(c.name)
    return {a: sorted(names) for a, names in seen.items() if len(names) > 1}


def _perm_problems() -> list[str]:
    """Signal store files and dirs whose mode is not 0600 / 0700, and a `signal.toml` that others can read."""
    from . import signalchats, signalstore
    out = []
    d = signalstore.store_dir()
    for path in [d, *d.rglob("*"), signalchats.config_path()]:
        if not path.exists() or path.is_symlink() or path.suffix == ".lock":
            continue
        want = 0o700 if path.is_dir() else 0o600
        mode = path.stat().st_mode & 0o777
        if mode & ~want:
            out.append(f"! signal: uprawnienia {path}: {oct(mode)} (powinno być {oct(want)})")
    return out


def signal_timer_status(home: Path | None = None, run=subprocess.run) -> str | None:
    """One line about the Signal pull timer: None when not installed, ✓ when active, ! when installed but not."""
    from .setup import TIMER
    unit = (home or Path.home()) / ".config/systemd/user" / f"{TIMER}.timer"
    if not unit.exists():
        return None
    try:
        r = run(["systemctl", "--user", "is-active", f"{TIMER}.timer"], capture_output=True, text=True,
                check=False, timeout=5)
        state = (r.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        state = "unknown"
    if state == "active":
        try:
            linger = run(["loginctl", "show-user", os.environ.get("USER") or "", "-p", "Linger"], capture_output=True,
                         text=True, check=False, timeout=5).stdout or ""
        except (OSError, subprocess.TimeoutExpired):
            linger = ""
        if "Linger=no" in linger:
            return ("! signal: timer działa tylko, gdy jesteś zalogowany (linger wyłączony: "
                    "`loginctl enable-linger`)")
        return "✓ signal: timer odbioru działa"
    return f"! signal: timer zainstalowany, ale nieaktywny ({state}); `house setup --signal-timer`"


def signal_status(now: datetime | None = None) -> list[str]:
    """Check the Signal setup for `house doctor`.

    Returns:
        Human-readable lines: not configured / signal-cli missing, else OK plus: last successful receive (warning
        when never or over 7 days ago: Signal may unlink an idle device), the last receive error (an unlinked
        device says how to relink), broken store lines, `signal.toml` problems and alias collisions, and store
        files with permissions other than 0600/0700.
    """
    from . import signalchats, signalread, signalstore
    from .signal import from_config, read_config
    if not read_config():
        return ["- signal: not configured (~/.config/house/notify.env); notifications are off"]
    sig = from_config()
    if sig is None:
        return ["✗ signal: signal-cli not found (house setup installs it to ~/.local)"]
    now = now or datetime.now().astimezone()
    lines = [f"✓ signal: {sig.binary}"]
    state = signalstore.read_state()
    ok = signalread.parse_iso(state.get("last_pull_ok"))
    if ok is None:
        lines.append("! signal: jeszcze nie było udanego odbioru (`house signal pull`)")
    elif now - ok > timedelta(days=7):
        lines.append(f"! signal: ostatni udany odbiór {ok:%Y-%m-%d}; Signal może odłączyć bezczynne urządzenie "
                     "(`house signal pull`, timer: `house setup --signal-timer`)")
    else:
        lines.append(f"✓ signal: ostatni odbiór {signalread.fmt_time(ok, now)}")
    err = state.get("last_pull_error")
    if err and any(x in str(err).lower() for x in ("not registered", "unlinked", "not linked", "no such account")):
        lines.append(f"✗ signal: urządzenie odłączone, `house setup` → ponowne linkowanie ({err})")
    elif err:
        lines.append(f"! signal: ostatni odbiór z błędem: {err}")
    bad = signalstore.bad_lines()
    if bad:
        lines.append("! signal: uszkodzone linie magazynu: " + ", ".join(map(str, bad[:10]))
                     + " (czytelnicy je pomijają)")
    lines += [f"! signal: {p}" for p in signalchats.collisions()]
    lines += _perm_problems()
    timer = signal_timer_status()
    if timer:
        lines.append(timer)
    from . import signalagent
    lines += signalagent.doctor_lines()
    return lines
