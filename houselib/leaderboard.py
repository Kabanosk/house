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


@dataclass
class Change:
    """A team that is new or changed its score (rank shifts caused by others are not changes)."""

    team: str
    score: float
    old_score: float | None
    rank: int
    old_rank: int | None


@dataclass
class Summary:
    """The `/lb` answer for one contest: `at` = snapshot shown, `sentences` = quote the previous `/lb`."""

    text: str
    at: str | None
    sentences: bool


def _rank(rows: list[dict], team: str | None) -> int | None:
    return next((i for i, r in enumerate(rows, 1) if team and r["team"] == team), None)


def changes(old: list[dict], new: list[dict], team: str | None) -> list[Change]:
    """Teams in the new top 10 or within ±2 of our rank that are new or changed their score (not us)."""
    ours = _rank(new, team)
    before = {r["team"]: (i, r["score"]) for i, r in enumerate(old, 1)}
    out = []
    for i, r in enumerate(new, 1):
        if r["team"] == team or not (i <= TOP or (ours and abs(i - ours) <= NEAR)):
            continue
        prev = before.get(r["team"])
        if prev is None:
            out.append(Change(r["team"], r["score"], None, i, None))
        elif prev[1] != r["score"]:
            out.append(Change(r["team"], r["score"], prev[1], i, prev[0]))
    return out


def _num(x: float) -> str:
    return f"{x:g}"


def table(rows: list[dict], team: str | None) -> str:
    """`miejsce | team | wynik`: top 10, plus our row after `…` when we are lower."""
    def line(i, r):
        return f"{i} | {r['team']} | {_num(r['score'])}" + (" ← my" if r["team"] == team else "")
    lines = ["miejsce | team | wynik"] + [line(i, r) for i, r in enumerate(rows[:TOP], 1)]
    ours = _rank(rows, team)
    if ours and ours > TOP:
        lines += ["…", line(ours, rows[ours - 1])]
    elif team and not ours:
        lines.append("nas nie ma w rankingu")
    return "\n".join(lines)


def _sentence(c: Change) -> str:
    if c.old_score is None:
        return f"{c.team} wszedł z {_num(c.score)} na #{c.rank}"
    verb = "poprawił" if c.score > c.old_score else "zmienił"
    where = f"z #{c.old_rank} na #{c.rank}" if c.old_rank != c.rank else f"#{c.rank}"
    return f"{c.team} {verb} na {_num(c.score)} (było {_num(c.old_score)}), {where}"


def _us(old: list[dict], new: list[dict], team: str) -> str:
    rank, was = _rank(new, team), _rank(old, team)
    if rank is None:
        return "my: nas nie ma w rankingu"
    score = new[rank - 1]["score"]
    text = f"my: #{rank} ({_num(score)})"
    if was is None:
        return text + ", wcześniej poza rankingiem"
    if old[was - 1]["score"] != score:
        text += f", nasz wynik był {_num(old[was - 1]['score'])}"
    return text + (f", było #{was}" if was != rank else "")


def _clock(iso: str, now: datetime) -> str:
    from .signalread import parse_iso
    t = parse_iso(iso).astimezone(now.tzinfo)
    return t.strftime("%H:%M" if t.date() == now.date() else "%d.%m %H:%M")


def summary(contest: Path, seen_at: str | None, asked: datetime | None, now: datetime) -> Summary:
    """The `/lb` answer: changes since the snapshot `seen_at` the chat saw when it asked at `asked`.

    Table when there is no previous `/lb`, its snapshot is gone, it was over 8 h ago, or more than 3 teams
    changed; sentences otherwise (quote the previous answer). Ends with the data time and, when the last
    poll failed, a staleness warning.
    """
    cfg = config(contest) or {}
    team = cfg.get("team")
    from .signalread import parse_iso
    snaps, state = snapshots(contest), poll_state(contest)
    failed = bool(state["error"]) and (not state["last_ok"] or not state["last_try"]
                                       or parse_iso(state["last_try"]) > parse_iso(state["last_ok"]))
    if not snaps:
        text = "brak danych rankingu (proces w tle jeszcze nic nie pobrał)"
        if failed:
            when = f" ({_clock(state['last_try'], now)})" if state["last_try"] else ""
            text += f"\n⚠ ostatnie pobranie nieudane{when}: {state['error']}"
        return Summary(text, None, False)
    latest = snaps[-1]
    fresh = max([latest["at"], *([state["last_ok"]] if state["last_ok"] else [])], key=parse_iso)
    tail = (f"⚠ ostatnie pobranie nieudane ({_clock(state['last_try'] or fresh, now)}); dane z {_clock(fresh, now)}"
            if failed else f"ranking z {_clock(fresh, now)}")
    prev = next((s for s in reversed(snaps) if s["at"] == seen_at), None) if seen_at else None
    found = changes(prev["rows"], latest["rows"], team) if prev else []
    if prev is None or (asked and now - asked > TABLE_AFTER) or len(found) > MANY:
        return Summary(f"{table(latest['rows'], team)}\n{tail}", latest["at"], False)
    lines = [_sentence(c) for c in found] or [f"bez zmian od {_clock(asked.isoformat(), now)}" if asked
                                              else "bez zmian"]
    if team:
        lines.append(_us(prev["rows"], latest["rows"], team))
    return Summary("\n".join(lines + [tail]), latest["at"], True)


UA = "Mozilla/5.0 (X11; Linux x86_64) house-lb"


def _http_get(url: str, headers: dict) -> str:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": UA, **headers})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def agorize_board(page_url: str, fetch=None, sort: str | None = None) -> list[dict]:
    """Board of an Agorize challenge (Huawei Tech Arena) from its public leaderboard page.

    The page's JS holds the organisers' API url and a public bearer token (`const url = '…'`,
    `const token = '…'`); both are read on every call and never stored.

    Args:
        page_url: The challenge's `…/pages/leaderboard` URL.
        fetch: `fetch(url, headers) -> str`; defaults to urllib.
        sort: "max"/"min" to stable-sort by score; None keeps the API order (as the page shows it).

    Returns:
        Rows in the `parse_rows` contract (`team`, `score`, plus `id`/`filename`/`created_at` when present).

    Raises:
        ValueError: The page has no API url or token.
    """
    fetch = fetch or _http_get
    page = fetch(page_url, {})
    url = re.search(r"const\s+url\s*=\s*'(https://[^']+)'", page)
    token = re.search(r"const\s+token\s*=\s*'([^']+)'", page)
    if not url or not token:
        raise ValueError("strona nie zawiera adresu API rankingu albo tokenu")
    data = json.loads(fetch(url.group(1), {"Authorization": f"Bearer {token.group(1)}"}))
    rows = parse_rows(json.dumps([
        {"team": r.get("team__name"), "score": r.get("score"),
         **{k: r[k] for k in ("id", "filename", "created_at") if k in r}}
        for r in (data.get("data") if isinstance(data, dict) else None) or []]))
    if sort in ("max", "min"):
        rows.sort(key=lambda r: r["score"], reverse=sort == "max")
    return rows
