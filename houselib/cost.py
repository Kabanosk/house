"""Session cost rows of a contest (spec K0): one row of numbers per interactive, night or Signal session.

What an agent should know:
  * Sources: interactive session files (`transcript.contest_sessions`, plus session directories that only hold
    sub-agent files), night worker logs named by `.house/attempts.jsonl` (`log`, relative to the contest) and Signal job
    logs (`<signal dir>/<job id>/log.jsonl` of jobs whose `repo` resolves to the contest). Everything goes through one
    `transcript.load`, so a call is counted once across all files and a session file whose id equals a stream log's id
    is dropped (a night or Signal run seen both as stream and as session file is one row). A stream log replaces the
    session file tree of the same id: stream-json carries the sub-agent events too (`parent_tool_use_id`, checked in
    A0), so the stream is complete and its sub-agent tokens are counted.
  * A row is the tree of one session id: the main thread plus its sub-agents (`sidechain` events). Its kind is `night`
    when a register row names the session id or a log with it (also when that log file is missing), `signal` when a
    job log has it, else `worker?` when the first `cwd` of the session is under `<contest>/.worktrees/` (a worker
    session with no register row, e.g. from before the register) and `interactive` otherwise. Signal rows exist only
    on the host that runs the Signal agent.
  * The row date is the local date (of this machine) of the earliest event by epoch time; `--since` compares it. Resumed copies of a
    call belong to whichever session `load` keeps the call in.
  * Night log format: the attempt's `tool` field decides (`codex` is a `codex exec --json` log written by
    `bin/codex-task --json-log`, anything else a Claude stream log); when `tool` is empty the first record of the log
    decides (`thread.*`, `turn.*` and `item.*` records are Codex). Signal job logs are always stream logs.
  * Context of a call = `input + cache_read + cache_write_5m + cache_write_1h` of a main-thread `usage` event. Codex
    reports one `usage` per turn, whose input is not a context size, so a Codex row has no context (None).
  * "Outside the record" (`Row.outside`): per model, the `session_total` tokens minus the `usage` tokens of the same
    session with `ts >= since_ms` (the whole log when `since_ms` is None; events without a `ts` count as inside), never
    below 0. The usage is the session's own, de-duplicated within the session before the global assignment of calls,
    so a fork that wins the de-duplication of a shared call does not make the original session's outside larger. It measures calls the record does not hold (title and search calls). None without a `session_total`, and
    for Codex, whose total is synthesised from the same turns.
  * Totals of a stream (C1 of K5): compaction summary calls (and other side calls) have no `assistant` event, so
    per-call sums undercount. A stream row with a `result` record takes its five buckets from the LAST `result`'s
    `modelUsage` summed over models (it is cumulative for the session, checked on CLI 2.1.327; each `result.usage` is
    per turn), with `cache_write_5m` / `cache_write_1h` from `cacheCreationEphemeral5m/1hInputTokens`; `note` is
    `totals from result`. A model whose total has no TTL split gets the 5m:1h proportion of its calls (of all calls
    when it has none; all 5 minute when no call wrote the cache), noted `TTL split from calls`. Calls and contexts
    stay per call. A stream row without a `result` keeps the per-call sums, noted `no result totals` (its per-call
    `output_tokens` are partial values from the start of each message). Session files (interactive rows) never see
    those calls: their `cost-state` covers only the last process run, so their buckets stay per call.
  * Partial stream output: in a Claude Code stream log the `output_tokens` of a call is a partial value from the start
    of the message (no `stop_reason`, so no usage is `complete`); `outside` then takes that model's output from the
    total so it is not counted as outside a second time.
  * Weighted cost = sum of bucket * weight (`DEFAULT_WEIGHTS`, spec section 4); an estimate of API cost, not a bill.
  * A row without per-call usage shows what it has: `note` is `total only` (the buckets come from the session total;
    a stream `result` gives the TTL split when it reports one, otherwise cache writes are priced as 5 minute writes;
    the model is the one with the most tokens in the total) or `no data`. A session that holds only
    sub-agent files has `note` `sub-agents only` and no context.
  * Rows hold numbers, a model name, a session id and a date, never message text, prompts or commands. A model name
    that looks like a path is shown as `?`.
  * `render` prints the rows as one fixed-width table (token columns in K) with a totals line, the weights used and
    the footer; `weights_from_spec` reads `cost.weights` of `spec.yaml` (unknown keys ignored, bad values fall back to
    the default with one warning). `house cost <contest> [--since YYYY-MM-DD]` (`cli.cmd_cost`) prints it.
  * `log_row(path)` gives the row of one unregistered log (an eval run's stream log) by the same rules; `house eval`
    takes its per-run metrics from it (`evals.results.run_metrics`).
  * Nothing is created or written: Signal jobs are read straight from `jobs.json` (never through `JobStore`, which
    creates directories).
"""
import json
import math
import os
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import date as Date
from datetime import datetime, timezone
from pathlib import Path

from houselib import attempts, transcript

PROJECTS = Path("~/.claude/projects").expanduser()  # read at call time, so tests and callers can replace it
FOOTER = "estimate of API cost (relative prices), not a bill and not subscription usage"
DEFAULT_WEIGHTS = {"input": 1, "cache_read": 0.1, "cache_write_5m": 1.25, "cache_write_1h": 2, "output": 5}
_BUCKETS = tuple(DEFAULT_WEIGHTS)
_SNIFF_LINES = 200
_CODEX_RECORDS = ("thread.", "turn.", "item.")
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")


@dataclass
class Row:
    """Numbers of one session tree.

    Attributes:
        session: Session id (Claude session id, or the Codex thread id).
        kind: `interactive`, `night`, `signal`, or `worker?` (a session that started under `<contest>/.worktrees/`
            and has no register row).
        date: `YYYY-MM-DD` on this machine's clock of the earliest event by epoch time (the register's start / the
            job's creation when the log has no timestamps); None when unknown.
        model: The model of most main-thread calls (of all calls when there are none); None without usage.
        calls: Model calls of the whole tree; for Codex, turns.
        context_first: Context of the first main-thread call (the base of the session); None when not known.
        context_median: Median context over main-thread calls (the mean of the middle two for an even count).
        context_peak: Largest context of a main-thread call.
        input: `input` tokens of the session: from the last stream `result` when there is one, else summed over calls.
        cache_read: Cache read tokens (same source).
        cache_write_5m: 5 minute cache write tokens (same source).
        cache_write_1h: 1 hour cache write tokens (same source).
        output: Output tokens (same source).
        compactions: Number of compactions.
        max_pre_tokens: Largest `pre_tokens` of a compaction; None without compactions.
        outside: Per model, tokens in the session total that are not in the usage records; None without a total.
        weighted: Weighted cost with `DEFAULT_WEIGHTS`; None for a row without data.
        note: `""`, `total only`, `no data`, `sub-agents only`, `totals from result`, `TTL split from calls` or
            `no result totals` (joined with `; ` when several apply).
    """
    session: str
    kind: str
    date: str | None
    model: str | None
    calls: int
    context_first: int | None
    context_median: float | None
    context_peak: int | None
    input: int
    cache_read: int
    cache_write_5m: int
    cache_write_1h: int
    output: int
    compactions: int
    max_pre_tokens: int | None
    outside: dict[str, int] | None
    weighted: float | None
    note: str = ""


@dataclass
class _Label:
    """What the register or a job says about a session id."""
    kind: str
    date: str | None = None


@dataclass
class _Sources:
    """Sources to load and the labels of the logs among them."""
    items: list[tuple[str, Path]] = field(default_factory=list)
    labels: dict[str, _Label] = field(default_factory=dict)
    seen: set[Path] = field(default_factory=set)

    def add_log(self, flavour: str, path: Path, kind: str, session: str, date: str | None) -> None:
        """Add a stream or Codex log once; label its session id (the first label of an id wins)."""
        real = Path(os.path.realpath(path))
        if real in self.seen:
            return
        self.seen.add(real)
        self.items.append((flavour, path))
        sid, _ = _sniff(path)
        self.label(sid or session, kind, date)

    def label(self, session: str, kind: str, date: str | None) -> None:
        """Label a session id (the first label of an id wins)."""
        if session and session not in self.labels:
            self.labels[session] = _Label(kind, date)


def weighted(row: Row, weights: dict[str, float] | None = None) -> float:
    """Weighted cost of a row.

    Args:
        row: The row.
        weights: Weight per bucket (`input`, `cache_read`, `cache_write_5m`, `cache_write_1h`, `output`); a missing
            bucket takes its default.

    Returns:
        The sum of bucket tokens times weight.
    """
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    return sum(getattr(row, b) * w[b] for b in _BUCKETS)


def _sniff(path: Path) -> tuple[str, str]:
    """Session id and flavour (`codex` or `stream`) from the first records of a log; ("", "stream") if unknown."""
    sid, flavour = "", ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for _, line in zip(range(_SNIFF_LINES), fh):
                try:
                    rec = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                if not isinstance(rec, dict):
                    continue
                kind = rec.get("type")
                if not flavour and isinstance(kind, str):
                    flavour = "codex" if kind.startswith(_CODEX_RECORDS) else "stream"
                for key in ("thread_id", "session_id", "sessionId"):
                    if isinstance(rec.get(key), str) and rec[key] and (key != "thread_id" or kind == "thread.started"):
                        sid = rec[key]
                        break
                if sid:
                    break
    except OSError:
        pass
    return sid, flavour or "stream"


def _day(text: object) -> str | None:
    """`YYYY-MM-DD` from the start of an ISO time, None when `text` is not one."""
    if isinstance(text, str) and re.match(r"\d{4}-\d{2}-\d{2}", text):
        return text[:10]
    return None


def _inside(path: Path, root: Path) -> bool:
    real = Path(os.path.realpath(path))
    return real == root or root in real.parents


def _night(contest: Path, src: _Sources) -> None:
    """Add the worker logs of the attempt register that exist inside the contest."""
    root = Path(os.path.realpath(contest))
    for row in attempts.rows(contest / ".house"):
        if not row.log:
            src.label(row.session, "night", _day(row.start))
            continue
        path = contest / row.log
        if not _inside(path, root) or not path.is_file():
            src.label(row.session, "night", _day(row.start))  # the log is gone, the session file is still a night one
            continue
        flavour = "codex" if row.tool == "codex" else "stream" if row.tool else _sniff(path)[1]
        src.add_log(flavour, path, "night", row.session, _day(row.start))


def _signal_dir(given: Path | None) -> Path:
    """`<data dir>/signal/agent` without creating anything (the data dir is $HOUSE_DATA_DIR or ~/.local/share/house)."""
    if given is not None:
        return given
    return Path(os.environ.get("HOUSE_DATA_DIR") or Path.home() / ".local/share/house") / "signal" / "agent"


def _signal(contest: Path, signal_dir: Path | None, src: _Sources) -> None:
    """Add the stream logs of Signal jobs whose `repo` resolves to the contest (`jobs.json` is read, never written)."""
    base = _signal_dir(signal_dir)
    try:
        jobs = json.loads((base / "jobs.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return
    if not isinstance(jobs, dict):
        return
    root = Path(os.path.realpath(contest))
    for job in jobs.values():
        if not isinstance(job, dict) or not isinstance(job.get("repo"), str) or not job["repo"]:
            continue
        job_id = job.get("id")
        if not isinstance(job_id, str) or not _SAFE_ID.fullmatch(job_id):
            continue
        log = base / job_id / "log.jsonl"
        if Path(os.path.realpath(job["repo"])) == root and log.is_file():
            src.add_log("stream", log, "signal", "", _day(job.get("created")) or _day(job.get("started")))


def _sessions(contest: Path, projects: Path) -> list[Path]:
    """Main session files of the contest, and the (absent) main files of session directories that only hold
    sub-agent files, so that a sub-agent tree without its parent is still read."""
    root = Path(os.path.realpath(contest))
    found = list(transcript.contest_sessions(contest, projects))
    for d in transcript.project_dirs(contest, projects):
        try:
            candidates = sorted(c for c in d.iterdir() if c.is_dir() and (c / "subagents").is_dir())
        except OSError:
            continue
        for c in candidates:
            main = d / f"{c.name}.jsonl"
            if main.is_file():
                continue
            try:
                files = sorted((c / "subagents").glob("agent-*.jsonl"))
            except OSError:
                continue
            cwd = next((cwd for cwd in map(transcript.first_cwd, files) if cwd), None)
            if cwd is not None and _inside(Path(cwd), root):
                found.append(main)
    return found


def _ms(ts: str | None) -> int | None:
    """Epoch milliseconds of an ISO time (UTC when it has no zone), None when it is not one."""
    try:
        t = datetime.fromisoformat(ts) if ts else None
    except ValueError:
        return None
    if t is None:
        return None
    return int((t if t.tzinfo else t.replace(tzinfo=timezone.utc)).timestamp() * 1000)


def _tokens(e: transcript.Event) -> int:
    return sum(e.data[b] for b in _BUCKETS)


def _partial_output(usages: list[transcript.Event], total: transcript.Event | None) -> set[str]:
    """Models whose per-call `output` is a partial value and whose true output is in the stream's total.

    Claude Code stream-json `assistant` records carry the usage of the start of the message (a small `output_tokens`,
    no `stop_reason`), the `result` record the true totals. A model qualifies when every `usage` event of it comes from
    a stream, none is `complete`, and the `session_total` names the model. Input and cache are complete at message
    start, so only output is ever corrected.
    """
    if total is None:
        return set()
    seen: dict[str, list[transcript.Event]] = {}
    for e in usages:
        seen.setdefault(e.model or "unknown", []).append(e)
    return {m for m, es in seen.items()
            if m in total.data["models"] and all(e.source == "stream" and not e.data.get("complete") for e in es)}


def _outside(usages: list[transcript.Event], total: transcript.Event | None,
             corrected: frozenset[str] | set[str] = frozenset()) -> dict[str, int] | None:
    """Per model, total tokens not in `usages` (the session's own de-duplicated `usage` events).

    For a model in `corrected` the output of the record is the total's output (see `_partial_output`), so the output
    is not counted as outside a second time.
    """
    if total is None:
        return None
    since = total.data.get("since_ms")
    used: Counter = Counter()
    for e in usages:
        at = _ms(e.ts) if since is not None else None
        if since is not None and at is not None and at < since:
            continue
        used[e.model or "unknown"] += _tokens(e)
        if (e.model or "unknown") in corrected:
            used[e.model or "unknown"] -= e.data["output"]
    for m in corrected:
        used[m] += total.data["models"][m]["output"]
    return {m: max(0, u["input"] + u["cache_read"] + u["cache_write"] + u["output"] - used[m])
            for m, u in total.data["models"].items()}


def _local_day(ms: int) -> str:
    """The date of an epoch millisecond time on this machine's clock."""
    return datetime.fromtimestamp(ms / 1000).date().isoformat()


def _first_day(events: list[transcript.Event]) -> str | None:
    """Local date of the earliest event by epoch time (not by text), None when no event has a usable time."""
    times = [t for t in map(_ms, (e.ts for e in events if _day(e.ts))) if t is not None]
    return _local_day(min(times)) if times else None


def _kind(events: list[transcript.Event], label: _Label | None, worktrees: Path | None) -> str:
    """`night` / `signal` from the label; else `worker?` when the first cwd is under the contest's `.worktrees/`."""
    if label:
        return label.kind
    first = next((e.cwd for e in events if e.cwd), None)
    if first and worktrees is not None and _inside(Path(first), worktrees):
        return "worker?"
    return "interactive"


def _join(note: str, more: str) -> str:
    return f"{note}; {more}" if note else more


def _result_counts(total: transcript.Event, usages: list[transcript.Event]) -> tuple[dict[str, int], bool]:
    """Buckets of a stream row from its last `result.modelUsage`, summed over models.

    The `result` totals are cumulative for the session and hold the calls that have no `assistant` event (compaction
    summaries), so they are the true totals. A model whose total has no TTL split gets its cache writes split by the
    per-call 5m:1h proportion of that model (of all calls when the model has no per-call writes; all 5 minute when
    no call wrote the cache).

    Returns:
        The buckets and whether some model's split came from the calls.
    """
    def split(events: list[transcript.Event]) -> tuple[int, int]:
        return sum(e.data["cache_write_5m"] for e in events), sum(e.data["cache_write_1h"] for e in events)

    counts = dict.fromkeys(_BUCKETS, 0)
    from_calls = False
    for name, u in total.data["models"].items():
        counts["input"] += u["input"]
        counts["cache_read"] += u["cache_read"]
        counts["output"] += u["output"]
        if "cache_write_5m" in u:
            counts["cache_write_5m"] += u["cache_write_5m"]
            counts["cache_write_1h"] += u["cache_write_1h"]
            continue
        w5, w1 = split([e for e in usages if (e.model or "unknown") == name])
        if not w5 + w1:
            w5, w1 = split(usages)
        hour = round(u["cache_write"] * w1 / (w5 + w1)) if w5 + w1 else 0
        counts["cache_write_5m"] += u["cache_write"] - hour
        counts["cache_write_1h"] += hour
        from_calls = from_calls or u["cache_write"] > 0
    return counts, from_calls


def _row(sid: str, events: list[transcript.Event], label: _Label | None, weights: dict[str, float] | None,
         own: list[transcript.Event] | None = None, worktrees: Path | None = None) -> Row:
    usages = [e for e in events if e.kind == "usage"]
    totals = [e for e in events if e.kind == "session_total"]
    total = totals[-1] if totals else None
    compactions = [e for e in events if e.kind == "compaction"]
    main = [e for e in usages if not e.sidechain]
    own_usages = [e for e in own if e.kind == "usage"] if own is not None else usages
    day = _first_day(events)
    note = ""
    if not usages and total is not None and total.data["models"]:
        note = "total only"
    elif not usages:
        note = "no data"
    elif not main:
        note = "sub-agents only"
    counts = {b: sum(e.data[b] for e in usages) for b in _BUCKETS}
    corrected = _partial_output(usages, total)
    if total is not None and total.source == "stream" and total.data["models"]:
        # the last `result` is the true total (calls without an `assistant` event included); calls stay per call
        counts, from_calls = _result_counts(total, usages)
        if usages:
            note = _join(note, "totals from result")
            if from_calls:
                note = _join(note, "TTL split from calls")
    elif note == "total only":  # nothing per call: show the totals, cache writes as 5 minute writes
        models = total.data["models"].values()
        counts = {"input": sum(m["input"] for m in models), "cache_read": sum(m["cache_read"] for m in models),
                  "cache_write_5m": sum(m["cache_write"] for m in models), "cache_write_1h": 0,
                  "output": sum(m["output"] for m in models)}
    elif total is None and any(e.source == "stream" for e in usages):
        note = _join(note, "no result totals")
    contexts = [sum(e.data[b] for b in _BUCKETS[:4]) for e in main if e.source != "codex"]
    named = Counter(e.model for e in (main or usages) if e.model)
    if note == "total only":
        named = Counter({m: sum(u[k] for k in ("input", "cache_read", "cache_write", "output"))
                         for m, u in total.data["models"].items()})
    row = Row(
        session=sid, kind=_kind(events, label, worktrees),
        date=day or (label.date if label else None),
        model=named.most_common(1)[0][0] if named else None,
        calls=len(usages),
        context_first=contexts[0] if contexts else None,
        context_median=statistics.median(contexts) if contexts else None,
        context_peak=max(contexts) if contexts else None,
        compactions=len(compactions),
        max_pre_tokens=max((e.data["pre_tokens"] for e in compactions), default=None),
        outside=None if note == "total only" or (total and total.source == "codex") else _outside(own_usages, total, corrected),
        weighted=None, note=note, **counts)
    if note != "no data":
        row.weighted = weighted(row, weights)
    return row


def log_row(path: Path, weights: dict[str, float] | None = None) -> Row | None:
    """The cost row of one stream log (or Codex JSONL log), by the same rules as `rows`.

    Meant for a log that is not registered anywhere (an eval run's `run.jsonl`): the buckets come from the last
    `result` total (`totals from result`, compaction summary calls included), sub-agent usage counts, compactions
    are counted.

    Args:
        path: The log file (a missing or unreadable file reads as empty).
        weights: Bucket weights for `Row.weighted`; default `DEFAULT_WEIGHTS`.

    Returns:
        The row of the whole log, or None when it holds no event at all.
    """
    sid, flavour = _sniff(path)
    events = transcript.load([(flavour, Path(path))]).events
    if not events:
        return None
    return _row(sid or events[0].session_id, events, None, weights)


def parse_since(since: str | Date | None) -> str | None:
    """Normalise a `--since` value to `YYYY-MM-DD`.

    Args:
        since: None, a `YYYY-MM-DD` string, a `date` or a `datetime` (its date is used).

    Returns:
        The date as `YYYY-MM-DD`, or None for None.

    Raises:
        ValueError: A string that is not a real date written exactly as `YYYY-MM-DD`.
    """
    if since is None:
        return None
    if isinstance(since, datetime):
        return since.date().isoformat()
    if isinstance(since, Date):
        return since.isoformat()
    if isinstance(since, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", since):
        try:
            return Date.fromisoformat(since).isoformat()
        except ValueError:
            pass
    raise ValueError(f"invalid date {since!r}: use YYYY-MM-DD")


def rows(contest: Path, since: str | Date | None = None, projects: Path | None = None,
         signal_dir: Path | None = None, weights: dict[str, float] | None = None) -> list[Row]:
    """One row per session of the contest (interactive, night worker, Signal job).

    Args:
        contest: Contest root (may be a symlink).
        since: Keep sessions whose first event is on this local date or later (`YYYY-MM-DD`, a date or a datetime, see
            `parse_since`). A session with no known date is kept, since it cannot be shown to be older.
        projects: Directory with the per-project session directories; default `PROJECTS`.
        signal_dir: Signal agent directory holding `jobs.json` and `<job id>/log.jsonl`; default
            `<data dir>/signal/agent`. It is only read.
        weights: Bucket weights for `Row.weighted`; default `DEFAULT_WEIGHTS`.

    Returns:
        Rows sorted by date (unknown dates last) and session id. A session id with no events at all gives no row.

    Raises:
        ValueError: `since` is not a valid date.
    """
    first_day = parse_since(since)
    projects = PROJECTS if projects is None else projects
    src = _Sources()
    _night(contest, src)
    _signal(contest, signal_dir, src)
    sources = [("session", p) for p in _sessions(contest, projects)] + src.items
    local: dict[str, list[transcript.Event]] = {}
    for e in transcript.collect(sources).events:
        local.setdefault(e.session_id, []).append(e)
    # each session first de-duplicates its own calls (for `outside`); then every call is assigned to one session
    own = {sid: transcript.dedupe(events) for sid, events in local.items()}
    by_session: dict[str, list[transcript.Event]] = {}
    for e in transcript.dedupe([e for events in local.values() for e in events]):
        by_session.setdefault(e.session_id, []).append(e)
    worktrees = Path(os.path.realpath(contest)) / ".worktrees"
    out = []
    for sid, events in by_session.items():
        row = _row(sid, events, src.labels.get(sid), weights, own.get(sid), worktrees)
        if first_day is None or row.date is None or row.date >= first_day:
            out.append(row)
    return sorted(out, key=lambda r: (r.date is None, r.date or "", r.session))


def _number(value: object) -> float | None:
    """A finite, non-negative number from a spec value (int, float or numeric string); None otherwise."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value) if isinstance(value, (int, float, str)) else None
    except ValueError:
        return None
    return number if number is not None and math.isfinite(number) and number >= 0 else None


def weights_from_spec(spec: dict) -> tuple[dict[str, float], str | None]:
    """Cost weights from `cost.weights` of a loaded `spec.yaml`.

    Args:
        spec: The mapping of `spec.load_spec`.

    Returns:
        The weights (a fresh dict: the defaults with every valid override applied) and a one-line warning, or None.
        Keys that are not buckets are ignored. A value that is not a finite number >= 0 keeps the default for that
        bucket and is named in the warning (never echoed); weights that are not a mapping give the defaults and a
        warning. No `cost` section or `cost.weights` gives the defaults without a warning.
    """
    node = spec.get("cost") if isinstance(spec, dict) else None
    raw = node.get("weights") if isinstance(node, dict) else None
    out = dict(DEFAULT_WEIGHTS)
    if raw is None:
        return out, None
    if not isinstance(raw, dict):
        return out, "warning: spec.yaml cost.weights is not a mapping; using the default weights"
    bad = []
    for key in _BUCKETS:
        if key in raw:
            number = _number(raw[key])
            if number is None:
                bad.append(key)
            else:
                out[key] = number
    warning = (f"warning: spec.yaml cost.weights has a bad value for {', '.join(bad)} (a number >= 0 is needed); "
               "using the default for it") if bad else None
    return out, warning


def format_k(n: float | None) -> str:
    """Tokens in K for the table: `—` for None, `0`, `<0.1K`, one decimal below 10K (`5.4K`), else whole K (`42K`)."""
    if n is None:
        return "—"
    n = int(n)
    if n <= 0:
        return "0"
    if n < 50:
        return "<0.1K"
    if n < 9950:
        tenths = (n + 50) // 100
        return f"{tenths // 10}.{tenths % 10}K"
    return f"{(n + 500) // 1000}K"


_TITLES = ("date", "kind", "model", "calls", "ctx_first", "ctx_med", "ctx_peak", "input", "cache_r", "cache_w5",
           "cache_w1h", "output", "compact", "outside", "weighted", "note")
_MODEL_WIDTH = 28


_PATH_LIKE = re.compile(r"[/~.\\]|[A-Za-z]:[\\/]")


def _path_like(model: str) -> bool:
    """Whether a model name looks like a file path (a leading `/`, `~` or `.`, a backslash or a drive letter)."""
    return bool(_PATH_LIKE.match(model)) or "\\" in model


def _compact(count: int, largest: int | None) -> str:
    return f"{count} ({format_k(largest)})" if count and largest else str(count)


def render(rows: list[Row], weights: dict[str, float] | None = None) -> str:
    """Render rows as a fixed-width table with a totals line, the weights used and the footer.

    Args:
        rows: Rows of `rows()`; they are sorted by date (unknown dates last), the sort is stable.
        weights: Bucket weights; a missing bucket takes its default. `Row.weighted` is recomputed with them.

    Returns:
        The text without a trailing newline: a header, one line per session, `total`, the weights line and the
        footer. Token columns are in K (`format_k`); `ctx_*` is the context of main-thread calls (`—` when not
        known, always for Codex); `compact` is the number of compactions and the largest `pre_tokens`; `outside` is
        the sum over models of tokens in the session total that are not in the record; `weighted` is rounded.
        `no sessions` for no rows. Only numbers, kind, date, model and note are printed, never session content.
    """
    if not rows:
        return "no sessions"
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    ordered = sorted(rows, key=lambda r: (r.date is None, r.date or ""))

    def cells(r: Row) -> list[str]:
        name = r.model or ""
        model = "?" if _path_like(name) else re.sub(r"[^A-Za-z0-9._:/-]", "?", name)[:_MODEL_WIDTH] or "—"
        return [r.date or "—", r.kind, model, str(r.calls), format_k(r.context_first), format_k(r.context_median),
                format_k(r.context_peak), format_k(r.input), format_k(r.cache_read), format_k(r.cache_write_5m),
                format_k(r.cache_write_1h), format_k(r.output), _compact(r.compactions, r.max_pre_tokens),
                "—" if r.outside is None else format_k(sum(r.outside.values())),
                "—" if r.weighted is None else str(round(weighted(r, w))), r.note]

    body = [list(_TITLES)] + [cells(r) for r in ordered]
    total = ordered
    costs = [weighted(r, w) for r in total if r.weighted is not None]
    outside = [sum(r.outside.values()) for r in total if r.outside is not None]
    pre = [r.max_pre_tokens for r in total if r.max_pre_tokens]
    n = len(total)
    body.append(["total", f"{n} session{'' if n == 1 else 's'}", "—", str(sum(r.calls for r in total)),
                 "—", "—", "—",
                 *(format_k(sum(getattr(r, b) for r in total)) for b in
                   ("input", "cache_read", "cache_write_5m", "cache_write_1h", "output")),
                 _compact(sum(r.compactions for r in total), max(pre, default=None)),
                 format_k(sum(outside)) if outside else "—", str(round(sum(costs))) if costs else "—", ""])
    widths = [max(len(line[i]) for line in body) for i in range(len(_TITLES) - 1)]
    lines = ["  ".join(c.ljust(widths[i]) if i < 3 else c.rjust(widths[i]) for i, c in enumerate(line[:-1]))
             + ("  " + line[-1] if line[-1] else "") for line in body]
    lines.append("weights: " + ", ".join(f"{b} {w[b]:g}" for b in _BUCKETS))
    lines.append(FOOTER)
    return "\n".join(line.rstrip() for line in lines)
