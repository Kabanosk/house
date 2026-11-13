"""Experiment loop for optimize contests (`house exp`).

Every experiment is a small change of a parent (the best `keep`, any earlier experiment, or a commit), run with
a fixed time budget, scored by `spec.scoring.local_scorer` and decided by code: better than the parent → `keep`,
otherwise `revert`. Each attempt is one row of `.house/experiments.csv`; `parent` makes the rows a tree, so an
agent can improve the best or debug a crash. Stagnation is computed from that file, not from memory.

Used by: coders and the coordinator in optimize contests (`playbooks/optimize.md`), `house exp new|run|status|
prune`, and the session-start hook (one summary line).

Rules an agent should know:
- Workflow: `house exp new <c> --from best` prints `E-### <worktree>`; make ONE change there; then
  `house exp run <c> E-### --budget 5m -- <command>`. The command trains/predicts inside the worktree, then the
  scorer runs there. `house exp run <c> --from best -- <command>` does both when the change is the command line.
- The scorer must print `score: <number>` (or a bare number as its last line). Direction comes from
  `scoring.direction` (`max`/`min`), else from the words max/min in `scoring.metric`.
- An experiment that changes the scorer or a frozen acceptance test is a `crash` ("tampered"). Scores are
  always computed with the contest checkout's current scorer; rows scored by an older scorer are not compared.
- Statuses: `new` (created), `keep`, `revert`, `crash`, `timeout`. Ties are `revert`. Every scored commit stays
  reachable as `refs/exp/E-###`; `keep` worktrees stay (their outputs may be submitted), the others are removed.
- Data outside git: list it in `exp.shared` in the spec and it is symlinked into every experiment worktree.
  `HOUSE_EXP` (id) and `HOUSE_CONTEST` (checkout path) are set for the command and the scorer.
"""
import contextlib
import csv
import fcntl
import os
import re
from pathlib import Path

from . import spec as specmod

COLUMNS = ["id", "parent", "commit", "idea_id", "budget", "duration", "score", "status", "scorer", "time", "note"]
SCORED = ("keep", "revert")
_BUDGET_PART = re.compile(r"(\d+)([smh])")
_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_SCORE_RE = re.compile(rf"\bscore\s*[:=]\s*({_NUM})", re.IGNORECASE)
_DIRECTION_RE = re.compile(r"\b(min|minimi[sz]e|max|maximi[sz]e)\b", re.IGNORECASE)


def parse_budget(text: str) -> int:
    """Turn a time budget into seconds.

    Args:
        text: Plain seconds (`"45"`) or units `s`/`m`/`h`, combinable (`"90s"`, `"5m"`, `"1h30m"`).

    Returns:
        The budget in seconds, at least 1.

    Raises:
        ValueError: Empty, malformed or zero budget.
    """
    t = str(text).strip().lower()
    if t.isdigit():
        seconds = int(t)
    elif t and re.fullmatch(r"(?:\d+[smh])+", t):
        seconds = sum(int(n) * {"s": 1, "m": 60, "h": 3600}[u] for n, u in _BUDGET_PART.findall(t))
    else:
        raise ValueError(f"invalid budget {text!r}; use e.g. 90s, 5m, 1h30m")
    if seconds <= 0:
        raise ValueError(f"budget must be positive, got {text!r}")
    return seconds


def direction(spec: dict) -> str | None:
    """Tell whether the contest metric is maximized or minimized.

    Args:
        spec: Parsed `spec.yaml` (`spec.load_spec`).

    Returns:
        `"max"` or `"min"` from `scoring.direction`; otherwise from the first word max/min/maximize/minimize in
        `scoring.metric`; None when neither says it (then `house exp run` refuses).
    """
    explicit = specmod.get(spec, "scoring.direction")
    if isinstance(explicit, str) and explicit.strip().lower() in ("max", "min"):
        return explicit.strip().lower()
    metric = specmod.get(spec, "scoring.metric")
    m = _DIRECTION_RE.search(metric) if isinstance(metric, str) else None
    return m.group(1).lower()[:3] if m else None


def parse_score(output: str) -> float | None:
    """Read the score from the scorer's output.

    Args:
        output: Everything the scorer printed (stdout and stderr together).

    Returns:
        The number in the last `score: X` / `score=X` (case-insensitive); otherwise the last non-empty line if it
        is a single number; otherwise None.
    """
    found = _SCORE_RE.findall(output or "")
    if found:
        return float(found[-1])
    lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
    if lines and re.fullmatch(_NUM, lines[-1]):
        return float(lines[-1])
    return None


def _csv_path(contest: Path) -> Path:
    """Return the path of `.house/experiments.csv`."""
    return contest / ".house" / "experiments.csv"


def _state_dir(contest: Path) -> Path:
    """Return `.house/experiments/` (lock and logs; never committed), creating it."""
    d = contest / ".house" / "experiments"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load(contest: Path) -> list[dict]:
    """Read every experiment row.

    Args:
        contest: Contest checkout root.

    Returns:
        Rows in file order as dicts with every key of `COLUMNS` (missing cells are ""); [] without the file.
    """
    path = _csv_path(contest)
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return [{k: (r.get(k) or "") for k in COLUMNS} for r in csv.DictReader(f)]


def _write(contest: Path, rows: list[dict]) -> None:
    """Rewrite experiments.csv atomically (call under `_locked`); newlines in cells become spaces."""
    path = _csv_path(contest)
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({k: " ".join(str(r.get(k, "") or "").splitlines()) for k in COLUMNS})
    os.replace(tmp, path)


@contextlib.contextmanager
def _locked(contest: Path):
    """Hold the exclusive experiments lock (`.house/experiments/lock`) for id allocation and csv updates."""
    with (_state_dir(contest) / "lock").open("w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def next_id(rows: list[dict]) -> str:
    """Return the next free experiment id (`E-001`, `E-002`, …) after the highest one in rows."""
    nums = [int(m.group(1)) for r in rows if (m := re.fullmatch(r"E-(\d+)", r.get("id", "")))]
    return f"E-{max(nums, default=0) + 1:03d}"


def _score(r: dict) -> float | None:
    """Return the row's score as a float, or None when it has none."""
    try:
        return float(r.get("score") or "")
    except ValueError:
        return None


def _better(a: float, b: float, sense: str) -> bool:
    """Tell whether score a is strictly better than b for the direction ("max"/"min")."""
    return a > b if sense == "max" else a < b


def _comparable(rows: list[dict], scorer: str) -> list[dict]:
    """Rows scored (keep/revert with a number) by the given scorer hash, in file order."""
    return [r for r in rows if r.get("status") in SCORED and r.get("scorer") == scorer and _score(r) is not None]


def best(rows: list[dict], scorer: str, sense: str) -> dict | None:
    """Pick the best `keep` scored by the current scorer.

    Args:
        rows: Experiment rows (`load`).
        scorer: Current scorer hash; rows scored by another scorer are ignored.
        sense: `"max"` or `"min"`.

    Returns:
        The best row (the earliest one on a tie), or None when there is no comparable keep.
    """
    keeps = [r for r in _comparable(rows, scorer) if r["status"] == "keep"]
    if not keeps:
        return None
    pick = max if sense == "max" else min
    return pick(keeps, key=_score)


def stagnation(rows: list[dict], scorer: str, sense: str) -> int:
    """Count scored experiments since the last new best.

    Only `keep`/`revert` rows of the current scorer count; crashes and timeouts do not (a typo must not force a
    pivot). Compare the result with `limits.stagnation_n`.

    Args:
        rows: Experiment rows (`load`).
        scorer: Current scorer hash.
        sense: `"max"` or `"min"`.

    Returns:
        Number of comparable experiments after the row that last set a new best (all of them if none did).
    """
    count, current = 0, None
    for r in _comparable(rows, scorer):
        s = _score(r)
        if r["status"] == "keep" and (current is None or _better(s, current, sense)):
            current, count = s, 0
        else:
            count += 1
    return count
