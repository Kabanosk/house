"""Official leaderboard tracking (`house lb`) and the `/lb` change summary.

A systemd user timer runs `house lb poll`: for every contest whose `.house/spec.yaml` has `leaderboard.cmd`, once
`leaderboard.every` has passed, the command runs in the contest root and prints the board as JSON
(`[{"team": str, "score": number, ...}]`, list order = rank order). A board that differs from the last stored one
is appended to `.house/leaderboard.jsonl`; every attempt goes to `.house/leaderboard.state.json` (`last_try`,
`last_ok`, `error`). Nothing is ever sent to Signal from here.

`/lb` (signalagent) never fetches: `summary` compares the newest snapshot with the one the chat saw at its
previous `/lb` and answers with sentences (few changes) or a `rank | team | score` table.

Used by: `house lb poll|show|agorize`, `signalagent.handle_command` (`/lb`).
"""
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_EVERY = timedelta(minutes=15)
CMD_TIMEOUT_S = 120
SNAPSHOTS = "leaderboard.jsonl"
STATE = "leaderboard.state.json"
TOP = 10
NEAR = 2          # ranks around ours that count as changes
MANY = 3          # more changes than this → table
TABLE_AFTER = timedelta(hours=8)


def parse_every(text: str | None) -> timedelta:
    """`90s` / `15min` / `2h` → timedelta; anything else → 15 min."""
    m = re.fullmatch(r"\s*(\d+)\s*(s|sec|m|min|h)\s*", str(text or ""))
    if not m or int(m.group(1)) == 0:
        return DEFAULT_EVERY
    unit = {"s": "seconds", "sec": "seconds", "m": "minutes", "min": "minutes", "h": "hours"}[m.group(2)]
    return timedelta(**{unit: int(m.group(1))})


def config(contest: Path) -> dict | None:
    """The contest's `leaderboard` section: `{"cmd", "every", "team"}`; None without `leaderboard.cmd`."""
    from . import spec as specmod
    section = specmod.load_spec(Path(contest) / ".house").get("leaderboard")
    if not isinstance(section, dict) or not section.get("cmd"):
        return None
    team = section.get("team")
    return {"cmd": str(section["cmd"]), "every": parse_every(section.get("every")),
            "team": str(team) if team not in (None, "") else None}


def parse_rows(stdout: str) -> list[dict]:
    """Validate the command's JSON: a list of objects with `team` and a numeric `score` (extra keys kept).

    Raises:
        ValueError: Not JSON, not a list, or a row without team / numeric score.
    """
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise ValueError(f"nie JSON: {stdout.strip()[:80]!r}") from e
    if not isinstance(data, list):
        raise ValueError("JSON nie jest listą")
    rows = []
    for r in data:
        if not isinstance(r, dict) or not r.get("team"):
            raise ValueError(f"wiersz bez team: {str(r)[:80]}")
        try:
            score = float(r.get("score"))
        except (TypeError, ValueError) as e:
            raise ValueError(f"wynik nie jest liczbą: {str(r)[:80]}") from e
        rows.append({**r, "team": str(r["team"]), "score": score})
    return rows


def _house(contest: Path) -> Path:
    return Path(contest) / ".house"


def snapshots(contest: Path) -> list[dict]:
    """Stored boards, oldest first: `[{"at": iso, "rows": [...]}]` (broken lines skipped)."""
    path = _house(contest) / SNAPSHOTS
    out = []
    if path.is_file():
        for line in path.read_text().splitlines():
            try:
                snap = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(snap, dict) and isinstance(snap.get("rows"), list) and snap.get("at"):
                out.append(snap)
    return out


def poll_state(contest: Path) -> dict:
    """`{"last_try", "last_ok", "error"}` of the last polls (missing keys are None)."""
    try:
        state = json.loads((_house(contest) / STATE).read_text())
    except (OSError, json.JSONDecodeError):
        state = {}
    state = state if isinstance(state, dict) else {}
    return {k: state.get(k) for k in ("last_try", "last_ok", "error")}


def _write_state(contest: Path, state: dict) -> None:
    from .signalstore import replace_durably
    replace_durably(_house(contest) / STATE, (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode())


def _append(contest: Path, snap: dict) -> None:
    with open(_house(contest) / SNAPSHOTS, "a") as f:
        f.write(json.dumps(snap, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def poll(contest: Path, now: datetime, run=subprocess.run, force: bool = False) -> str | None:
    """Fetch the board once when due and store it if it changed.

    Args:
        contest: Contest checkout root.
        now: Reference time (aware).
        run: Runner with the signature of `subprocess.run`.
        force: Ignore `every`.

    Returns:
        A log line, or None when the contest has no leaderboard or is not due.
    """
    cfg = config(contest)
    if cfg is None:
        return None
    state = poll_state(contest)
    from .signalread import parse_iso
    last = parse_iso(state["last_try"]) if state["last_try"] else None
    if not force and last and now - last < cfg["every"]:
        return None
    name = Path(contest).name
    state["last_try"] = now.isoformat()
    try:
        r = run(cfg["cmd"], shell=True, cwd=Path(contest), timeout=CMD_TIMEOUT_S, capture_output=True, text=True,
                check=False)
        if r.returncode != 0:
            raise ValueError(f"kod {r.returncode}: {(r.stderr or r.stdout or '').strip()[-200:]}")
        rows = parse_rows(r.stdout)
    except subprocess.TimeoutExpired:
        state["error"] = f"przekroczony limit {CMD_TIMEOUT_S} s"
    except (ValueError, OSError) as e:
        state["error"] = str(e)[:300]
    else:
        state["error"], state["last_ok"] = None, now.isoformat()
        snaps = snapshots(contest)
        changed = not snaps or snaps[-1]["rows"] != rows
        if changed:
            _append(contest, {"at": now.isoformat(), "rows": rows})
        _write_state(contest, state)
        return f"lb {name}: {len(rows)} drużyn" + (" · zmiana zapisana" if changed else " · bez zmian")
    _write_state(contest, state)
    return f"lb {name}: błąd: {state['error']}"
