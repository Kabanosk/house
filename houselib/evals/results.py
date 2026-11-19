"""The eval results file `evals/results.jsonl` and what `house eval status` computes from it.

One JSON object per line, one line per run, appended as the runs finish (git-ignored: it is a local measurement
log, not part of the repo). A row holds numbers and short labels only, never session content (no prompt, no tool
input, no log text; `note` is the grader's short redacted note and `eval_events` counts of the house eval log by event
name). Rows are grouped by `(case, case_hash, commit, model, variant)`: editing a case file changes its hash and
starts a new history, and runs on another commit or model are a separate history too, so they are never mixed.

Used by: `house eval run` (`append`, `is_pass_k`, `cli_version`, `event_counts`), `house eval status` (`read`,
`status`) and `house eval compare` (`read`, `compare`, `render_compare`).

Rules an agent should know:
- `infra_error` rows are kept in the file but never count in the runs, pass^1 or pass^k of a case: they say
  nothing about the agent.
- pass^k means the last K runs (default 3) all passed and there are at least K of them; `flaky` means some but not
  all of the last K passed; `zero` (shown as `0/n`) means no run ever passed, so suspect the case itself first.
- `read` is tolerant: blank, unparsable and non-object lines are skipped, a half-written last line included.
- A row has `variant` (the case variant run, `default` when none) and `metrics` (`run_metrics`: the cost of the run's
  stream log). `status` groups by variant too; a row without `variant` (written before variants) counts as `default`.
- A row has `score` (null when nothing was evaluated) and `predicates` (`{name: passed}` booleans of the scored entries,
  see `grade.Grade`). `compare` only reads them; a row without them is skipped in the numbers that need them.
- A row has `cohort` (`house eval run --cohort NAME`, null without) and `cli` (the version of the CLI that ran: for
  Claude the `house-agent` image's, from the log's init event). `compare --cohort NAME` reads only that cohort, and
  never pools rows of different `(commit, model, cli)`: they are separate groups flagged `mixed`.
- A row has `audit` (`{"house_reads": N}`, numbers only): the tool calls of the run whose input named the agent's
  read-only house export (`house_reads`); `compare` flags a variant `house-read` when any of its runs has N > 0.
"""
import json
import posixpath
import re
import statistics
import subprocess
import threading
from datetime import datetime
from functools import cache
from pathlib import Path

from .. import agentdocker, cost, transcript
from .untrusted import Unreadable, read_regular

DEFAULT_K = 3
INFRA = "infra_error"
MAX_LOG_BYTES = 2_000_000  # how much of the (agent-writable) house eval log is read
MAX_EVENT_NAMES = 20  # distinct event names kept; the rest, and odd names, count as "other"
EVENT_NAME = re.compile(r"[a-z][a-z0-9_.]{0,39}")


_APPEND_LOCK = threading.Lock()  # `house eval run --parallel`: the runs of one process append from several threads


def append(path: Path, row: dict) -> None:
    """Append one row to the results file (created with its parent directory when missing).

    Safe to call from several threads: the complete line is built first and written with one `write` under a
    process-wide lock, so rows of concurrent runs never interleave.

    Args:
        path: The `results.jsonl` path.
        row: A JSON-serialisable mapping; written as a single line.
    """
    path = Path(path)
    line = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
    with _APPEND_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line)


def read(path: Path) -> list[dict]:
    """Read every well-formed row of the results file, in file order.

    Args:
        path: The `results.jsonl` path; a missing or unreadable file means no rows.

    Returns:
        The rows. Blank lines, invalid JSON and lines that are not JSON objects are skipped.
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rows = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def is_pass_k(results: list[str], k: int = DEFAULT_K) -> bool | None:
    """Tell whether the last `k` runs among `results` all passed.

    Args:
        results: Results of one case in run order (`pass`, `fail`, `paused`, `infra_error`).
        k: Window size.

    Returns:
        None when there is no run besides `infra_error` ones; True when there are at least `k` of them and the
        last `k` all passed; False otherwise.
    """
    real = [r for r in results if r != INFRA]
    if not real:
        return None
    return len(real) >= k and all(r == "pass" for r in real[-k:])


def status(rows: list[dict], k: int = DEFAULT_K) -> list[dict]:
    """Aggregate result rows per `(case, case_hash, commit, model, variant)`.

    Args:
        rows: Rows as read by `read`, oldest first (a missing `commit` or `model` reads as an empty string, a
            missing `variant` as `default`).
        k: Window of the pass^k and `flaky` checks.

    Returns:
        One dict per group, sorted by case name, then by the order the groups first appeared. Keys: `case`,
        `case_hash`, `commit` (full sha), `model`, `variant`, `kind` (of the latest row), `runs` (non-infra), `infra`
        (infra_error runs), `passes`, `pass1` (share of passing runs, None without runs), `passk` (see `is_pass_k`;
        False without runs), `window` (runs in the last-K window), `flaky` (0 < passes < window), `zero` (runs > 0
        and no pass at all), `streak` (non-infra runs passed in a row, newest first), `last_result`, `last_date`
        (of the latest row, infra included), `latest` (the group has the case's latest `case_hash`),
        `latest_commit` (the group has the commit of the case's latest row) and `latest_commit_of_hash` (the group
        has the commit of the latest row of its `(case, case_hash)`).
    """
    groups: dict[tuple[str, str, str, str, str], list[dict]] = {}
    latest_hash: dict[str, str] = {}
    latest_commit: dict[str, str] = {}
    hash_commit: dict[tuple[str, str], str] = {}
    for row in rows:
        case, digest = str(row.get("case", "")), str(row.get("case_hash", ""))
        if not case:
            continue
        commit, model = str(row.get("commit", "")), str(row.get("model", ""))
        variant = str(row.get("variant") or "default")
        groups.setdefault((case, digest, commit, model, variant), []).append(row)
        latest_hash[case], latest_commit[case] = digest, commit
        hash_commit[(case, digest)] = commit
    out = []
    for (case, digest, commit, model, variant), items in groups.items():
        real = [r for r in items if r.get("result") != INFRA]
        passes = sum(1 for r in real if r.get("result") == "pass")
        window = real[-k:]
        window_passes = sum(1 for r in window if r.get("result") == "pass")
        streak = 0
        for r in reversed(real):
            if r.get("result") != "pass":
                break
            streak += 1
        last = items[-1]
        out.append({
            "case": case, "case_hash": digest, "commit": commit, "model": model, "variant": variant,
            "kind": str(last.get("kind", "")),
            "runs": len(real), "infra": len(items) - len(real), "passes": passes,
            "pass1": passes / len(real) if real else None,
            "passk": bool(is_pass_k([str(r.get("result")) for r in items], k)),
            "window": len(window), "flaky": 0 < window_passes < len(window),
            "zero": bool(real) and passes == 0, "streak": streak,
            "last_result": str(last.get("result", "")), "last_date": str(last.get("date", "")),
            "latest": latest_hash[case] == digest, "latest_commit": latest_commit[case] == commit,
            "latest_commit_of_hash": hash_commit[(case, digest)] == commit,
        })
    return sorted(out, key=lambda g: g["case"])  # stable: groups of one case keep their first-seen order


def _local_day(row: dict) -> str | None:
    """The local `YYYY-MM-DD` of a row's `date`, None when it is missing or unreadable."""
    try:
        return datetime.fromisoformat(str(row.get("date"))).astimezone().date().isoformat()
    except ValueError:
        return None


def _ungradable(row: dict) -> bool:
    """A run that says nothing about the agent: `infra_error`, `incomplete` or a failed `setup` (no score)."""
    note = str(row.get("note") or "")
    return row.get("result") == INFRA or note == "incomplete" or note.startswith("setup")


def _metric(rows: list[dict], key: str) -> list[int | float]:
    """The numeric `metrics[key]` of the rows that have one."""
    out = []
    for r in rows:
        value = (r.get("metrics") or {}).get(key) if isinstance(r.get("metrics"), dict) else None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out.append(value)
    return out


def _house_reads(row: dict) -> int:
    """The row's `audit.house_reads` when it is an integer count, else 0 (older rows have no audit)."""
    audit = row.get("audit")
    value = audit.get("house_reads") if isinstance(audit, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def compare(rows: list[dict], case: str, digest: str, since: str | None = None,
            cohort: str | None = None) -> list[dict]:
    """Aggregate the runs of one case per variant, for comparing variants on quality and cost.

    Only rows of this `case` and `case_hash` count (an edited case starts a new history); with `since` only rows
    whose local date is that day or later (a row with an unreadable date is dropped then); with `cohort` only rows
    whose `cohort` is that name (`house eval run --cohort`). Rows are never pooled across a commit, a model or a CLI
    version: a group is `(variant, commit, model, cli)`, and when the selected rows hold more than one `(commit,
    model, cli)` every group is flagged `mixed`.

    Args:
        rows: Rows as read by `read`.
        case: The case name.
        digest: The case's current `case_hash`.
        since: `YYYY-MM-DD` or None.
        cohort: A cohort name or None (every row).

    Returns:
        One dict per group in first-seen order (a row without `variant` is `default`). Keys: `variant`, `commit`,
        `model`, `cli` (empty when a row lacks them), `n` (gradable runs), `passes`, `infra` (`infra_error`,
        `incomplete` and `setup` runs: counted here, never in the numbers below), `score_mean` / `score_min` /
        `score_max` (None without a score), `weighted_mean` / `weighted_min` / `weighted_max`, `peak_median`,
        `compactions_mean` (over runs with metrics; None without), `flags` (`control compacted`: a variant named
        `control*` had a run with compactions, any run; `n<3`: fewer than three gradable runs; `mixed`: the rows
        span several commits, models or CLI versions; `house-read`: a run of the group, any run, has
        `audit.house_reads` > 0, i.e. a tool call named the house export, see `house_reads`) and `predicates`
        (`{name: (passed runs, runs carrying it)}`, first-seen order).
    """
    groups: dict[tuple[str, str, str, str], list[dict]] = {}
    for row in rows:
        if row.get("case") != case or row.get("case_hash") != digest:
            continue
        if since is not None and (_local_day(row) or "") < since:
            continue
        if cohort is not None and row.get("cohort") != cohort:
            continue
        key = (str(row.get("variant") or "default"), str(row.get("commit") or ""), str(row.get("model") or ""),
               str(row.get("cli") or ""))
        groups.setdefault(key, []).append(row)
    mixed = len({key[1:] for key in groups}) > 1
    out = []
    for (variant, commit, model, cli), items in groups.items():
        real = [r for r in items if not _ungradable(r)]
        scores = [r["score"] for r in real if isinstance(r.get("score"), (int, float))]
        weighted, peaks, compactions = (_metric(real, k) for k in ("weighted", "peak", "compactions"))
        flags = []
        if variant.startswith("control") and any(c > 0 for c in _metric(items, "compactions")):
            flags.append("control compacted")
        if len(real) < 3:
            flags.append("n<3")
        if mixed:
            flags.append("mixed")
        if any(_house_reads(r) > 0 for r in items):
            flags.append("house-read")
        predicates: dict[str, tuple[int, int]] = {}
        for r in real:
            for name, ok in (r.get("predicates") or {}).items():
                if isinstance(ok, bool):
                    passed, total = predicates.get(name, (0, 0))
                    predicates[name] = (passed + ok, total + 1)
        out.append({
            "variant": variant, "commit": commit, "model": model, "cli": cli,
            "n": len(real), "passes": sum(1 for r in real if r.get("result") == "pass"),
            "infra": len(items) - len(real),
            "score_mean": statistics.fmean(scores) if scores else None,
            "score_min": min(scores) if scores else None, "score_max": max(scores) if scores else None,
            "weighted_mean": statistics.fmean(weighted) if weighted else None,
            "weighted_min": min(weighted) if weighted else None,
            "weighted_max": max(weighted) if weighted else None,
            "peak_median": statistics.median(peaks) if peaks else None,
            "compactions_mean": statistics.fmean(compactions) if compactions else None,
            "flags": flags, "predicates": predicates})
    return out


def _group_name(g: dict) -> str:
    """The variant, or `variant@<commit7>/<model>/<cli>` for a group of a `mixed` comparison."""
    if "mixed" not in g["flags"]:
        return g["variant"]
    return f"{g['variant']}@{g['commit'][:7]}/{g['model']}/{g['cli']}"


def render_compare(groups: list[dict]) -> list[str]:
    """Lines of the `house eval compare` table and of the per-predicate breakdown below it.

    Token numbers are K-rounded like `house cost` (`cost.format_k`); an empty number is `-`. A group of a `mixed`
    comparison is named `variant@<commit7>/<model>/<cli>`.

    Args:
        groups: The result of `compare`.

    Returns:
        The header, one line per variant and, when any run carries per-predicate booleans, a blank line,
        `predicates (passed/runs per variant):` and one line per predicate name.
    """
    def dash(value, fmt):
        return "-" if value is None else fmt(value)

    def span(low, high, fmt):
        return "-" if low is None else f"{fmt(low)}–{fmt(high)}"

    two, tokens = "{:.2f}".format, cost.format_k
    table = [("variant", "n", "pass", "infra", "score", "score_min–max", "weighted", "weighted_min–max", "peak",
              "compactions", "flags")]
    for g in groups:
        table.append((_group_name(g), str(g["n"]), str(g["passes"]), str(g["infra"]), dash(g["score_mean"], two),
                      span(g["score_min"], g["score_max"], two), dash(g["weighted_mean"], tokens),
                      span(g["weighted_min"], g["weighted_max"], tokens), dash(g["peak_median"], tokens),
                      dash(g["compactions_mean"], "{:.1f}".format), ", ".join(g["flags"])))
    widths = [max(len(row[i]) for row in table) for i in range(len(table[0]))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip() for row in table]
    names: dict[str, None] = {}
    for g in groups:
        names.update(dict.fromkeys(g["predicates"]))
    if names:
        lines += ["", "predicates (passed/runs per variant):"]
        for name in names:
            cells = [f"{_group_name(g)} {g['predicates'][name][0]}/{g['predicates'][name][1]}"
                     for g in groups if name in g["predicates"]]
            lines.append(f"{name}  " + "  ".join(cells))
    return lines


def event_counts(path: Path) -> dict[str, int]:
    """Count the events of a house eval log (`evalmode.log` lines) by event name.

    The log lives in the agent's read-write mount, so it is untrusted: it is read through `untrusted.read_regular`
    (no blocking on a FIFO, no symlink, regular files only), only the first `MAX_LOG_BYTES` are read, names that are not short lowercase dotted words (and names
    beyond the first `MAX_EVENT_NAMES` distinct ones) are counted as `other`, and nothing but counts is returned.

    Args:
        path: The log (`HOUSE_EVAL_LOG`); a missing, unreadable, symlinked or non-regular file means no events.

    Returns:
        `{event name: count}`, empty when there is nothing to count.
    """
    try:
        data = read_regular(path, MAX_LOG_BYTES, truncate=True)
    except Unreadable:
        return {}
    if data is None:
        return {}
    counts: dict[str, int] = {}
    for line in data.decode("utf-8", errors="replace").splitlines():
        try:
            name = json.loads(line).get("event")
        except (ValueError, AttributeError):
            continue
        if not isinstance(name, str):
            continue
        if not EVENT_NAME.fullmatch(name) or (name not in counts and len(counts) >= MAX_EVENT_NAMES):
            name = "other"
        counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items()))


@cache
def tool_version(tool: str) -> str:
    """Return the version string of the `claude` or `codex` CLI on this machine, best effort.

    Args:
        tool: `claude` or `codex`.

    Returns:
        The first line of `<tool> --version`, or `unknown` when the tool is missing or fails.
    """
    try:
        done = subprocess.run([tool, "--version"], capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    lines = done.stdout.strip().splitlines()
    return lines[0].strip() if done.returncode == 0 and lines else "unknown"


def run_metrics(log: Path) -> dict[str, int]:
    """Per-run cost numbers of a stream log, through the K0 parser (`cost.log_row`: the buckets come from the last
    `result` total, compaction summary calls included; sub-agent usage counted).

    Args:
        log: The run's stream log (may be missing or empty).

    Returns:
        `{"weighted": cost with the default weights (rounded), "peak": largest context of a main-thread call,
        "calls": model calls including sub-agents, "compactions": `compact_boundary` count, "output": output
        tokens}`; all zero when the log holds nothing readable.
    """
    row = cost.log_row(log)
    if row is None:
        return {"weighted": 0, "peak": 0, "calls": 0, "compactions": 0, "output": 0}
    return {"weighted": round(row.weighted or 0), "peak": row.context_peak or 0, "calls": row.calls,
            "compactions": row.compactions, "output": row.output}


# The rest of a path after the export directory: up to whitespace, a quote or a shell operator.
_PATH_TAIL = re.compile(r"""[^\s'"`;|&<>(){}\[\],*?$\\]*""")
_NAME_CHAR = re.compile(r"[\w.-]")


def _input_strings(value) -> list[str]:
    """Every string inside a tool input (dicts and lists walked iteratively, so deep nesting cannot raise)."""
    out, stack = [], [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return out


def _names_export(text: str, export: str, contest: str) -> bool:
    """Whether `text` holds the export directory `export` as a path that, normalised, is outside `contest`."""
    start = 0
    while (i := text.find(export, start)) != -1:
        end = start = i + len(export)
        if end < len(text) and text[end] != "/" and _NAME_CHAR.match(text, end):
            continue  # another name with the same prefix (`<tmp>/house-old`)
        path = posixpath.normpath(export + _PATH_TAIL.match(text, end).group())
        if path != contest and not path.startswith(contest + "/"):
            return True
    return False


def house_reads(log: Path, tool: str, export: Path, contest: Path) -> int:
    """Count the tool calls of a run whose input names the house export outside the contest (the peek audit).

    The agent container mounts the export (`TempRoot.root`, `<tmp>/house`) read-only at the same path; the contest
    (`TempRoot.contest`) lies inside it and is the workspace, so a path in the contest never counts, and neither does
    a command found through `PATH` (`house check`): only an input string holding the export directory itself (e.g.
    `<tmp>/house/houselib/...`, `<tmp>/house/contests/<name>/../..`) counts. Main-thread and sub-agent calls of a
    Claude stream log and the commands and file changes of a Codex log are read (`transcript`); a call counts once
    however often it names the export, and a call id seen twice counts once. Relative paths (`../../evals`) are not
    seen: the count is a signal, the exclusion of the answer key from the export (`sandbox.EXPORT_EXCLUDE`) is the
    protection.

    Args:
        log: The run's stream log (may be missing or empty).
        tool: `claude` or `codex` (picks the parser).
        export: The export directory as the agent sees it.
        contest: The contest directory as the agent sees it.

    Returns:
        The number of such tool calls; 0 when the log holds nothing readable.
    """
    try:
        parsed = transcript.codex_stream(Path(log)) if tool == "codex" else transcript.claude_stream(Path(log))
    except OSError:
        return 0
    root, inside = posixpath.normpath(str(export)), posixpath.normpath(str(contest))
    seen: set[str] = set()
    count = 0
    for event in parsed.events:
        if event.kind != "tool_call":
            continue
        call_id = event.data.get("id") or ""
        if call_id and call_id in seen:
            continue
        if any(_names_export(text, root, inside) for text in _input_strings(event.data.get("input"))):
            count += 1
            if call_id:
                seen.add(call_id)
    return count


def cli_version(log: Path, tool: str) -> str:
    """Return the CLI version a run used: from the Claude log's init event, else a fallback.

    Claude runs in the pinned `house-agent` image, so its fallback is `agentdocker.CLAUDE_VERSION`, not the host's
    `claude --version` (which says nothing about the code that ran). Codex runs locally: `codex --version`.

    Args:
        log: The run's stream log (may be missing or empty).
        tool: `claude` or `codex`.

    Returns:
        A version string, `unknown` at worst. Only the `claude_code_version` field is taken from the log.
    """
    if tool == "claude":
        try:
            with Path(log).open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    if "claude_code_version" not in line:
                        continue
                    try:
                        value = json.loads(line).get("claude_code_version")
                    except (ValueError, AttributeError):
                        continue
                    if isinstance(value, str) and value:
                        return value
        except OSError:
            pass
        return agentdocker.CLAUDE_VERSION
    return tool_version(tool)
