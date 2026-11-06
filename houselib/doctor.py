"""Detect which tools are available on this machine."""
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path


def _run(cmd: list[str]) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def _gpu(out: str | None) -> dict | None:
    if not out:
        return None
    name, sep, mem = out.splitlines()[0].rpartition(",")
    if not sep or not mem.strip().isdigit():
        return None
    return {"name": name.strip(), "vram_mib": int(mem)}


def detect(which=shutil.which, run=_run) -> dict:
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
    path = contest_dir / ".house" / "tools.json"
    data = dict(tools, checked_at=datetime.now().astimezone().isoformat(timespec="seconds"))
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def summary(tools: dict) -> str:
    mark = lambda ok: "✓" if ok else "✗"  # noqa: E731
    lines = [f"{mark(tools[k])} {k}" for k in ("claude", "codex", "cursor", "grok_bot", "uv", "ruff")]
    o = tools["ollama"]
    lines.append(f"{mark(o['installed'])} ollama  models: {', '.join(o['models']) or '(none)'}")
    g = tools["gpu"]
    lines.append(f"{mark(g)} gpu  " + (f"{g['name']} ({g['vram_mib']} MiB)" if g else "(none)"))
    return "\n".join(lines)
