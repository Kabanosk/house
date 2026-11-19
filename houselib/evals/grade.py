"""Deterministic grader: one eval run's session log and the sandbox's final state become a verdict (spec A3, §9a).

    grade(case, run, root) -> Grade        # run: runner.RunResult, root: the sandbox.TempRoot that was used

Decision order (the first that applies wins):
1. `run.infra` is set -> `infra_error` (the environment, not the agent, failed).
2. The log's final terminal event says the run died on a subscription limit or an authentication problem (a Claude
   `result` with `is_error`, a Codex last `turn.failed`/`error`, whose text matches `runner.INFRA_TEXT`) ->
   `infra_error`, even after genuine agent turns: such a run says nothing about the agent.
3. `run.timed_out` -> `fail` (note `timeout`); more main-thread turns than `case.max_turns`, or a Claude log whose
   final `result` has `subtype` `error_max_turns` (the CLI hit its cap) -> `fail` (note `max_turns`). The calls and
   predicates are still evaluated for these two, only for the lists and the score; the verdict stays `fail`.
4. The conversation did not finish (Claude: the process did not exit, or the log does not hold exactly one `success`
   `result` per scheduled turn, `1 + len(case.followups)`, e.g. turn one answered and the CLI died in turn two;
   Codex: no `turn.completed`) -> `fail` (note `incomplete`, no score), never a pass.
5. The contest's `.git` was tampered with (see "Host git" below) -> `fail` (note `git tampered`); the predicates
   that run git are not evaluated and count as failed.
6. Calls, order and outcome predicates are checked. QUESTIONS.md that cannot be read safely (see "Files") ->
   `fail`. A new open question (`Q-###` open now, not open at the fixture's start) that the case does not expect ->
   `paused`, unless a forbidden call was made, an order pair involving a `forbid` label is violated (a forbidden
   action is never masked by a question) or an `open_questions` predicate that caps the count (`max`/`count`)
   failed -> `fail`; otherwise `pass` when nothing is missing, forbidden, out of order or failed, else `fail`.

Rules an agent should know:
- `Grade.score` is the share of passed entries, for comparing variants of a case: every `expect` entry, every `forbid`
  entry and every outcome predicate counts 1 (an `order` pair does not), so 3 of 4 passing is 0.75. None for
  `infra_error`, `incomplete` and `setup` runs (nothing was evaluated) and for a case without any entry.
- `hidden_tests` passes when its run (`RunResult.hidden[i]`, made by the runner in a second container) has `exit` 0 and
  `passed >= min_passed`; no result (not run) or `exit` None (timed out) fails it, with a note of counts only.
  `Grade.hidden` repeats the counts as `{"outcome": i, "exit", "passed", "failed"}`.
- Calls: sub-agent tool calls count like main-thread ones (a forbidden call is caught wherever it is made). The log
  is parsed with the transcript parser of `case.tool`; a `tool_call` event matches a `Call` when the tool name
  equals `call.tool` and `call.matches(...)` on the whitelisted, redacted input of
  `transcript.view(event, bash_limit=None)`, i.e. a Bash command is matched whole (view's 300-character cut is for
  output and would hide a forbidden command), never the raw input. A `Call` with `ok` counts only calls whose
  `tool_result` exists and has `is_error` False (unknown counts as not ok). A `Call` with `from_turn` counts only
  calls made during user turn >= `from_turn`: turn k is the calls after the (k-1)-th `result` event of the stream
  (turn 1 before the first result), placed by `tool_use` id (`_tool_turns`). `expect` is violated by a match count
  below `min` or above `max` (reported in `missing` as `<label>: <count> (min .., max ..)`); `forbid` by a count at or
  above its `min` (`forbidden`, as `<label>: <count>`). Other calls are always allowed (superset semantics).
- Order: for a pair `(before, after)` compare the index of the FIRST matching tool call of each label. The pair is
  violated (`order_violations`, as `before<after`) only when `after` occurred AND (`before` is absent OR its first
  occurrence is not strictly earlier, e.g. one call matching both labels). `after` absent is never an order
  violation: a required `after` is reported through `missing` (give it `min >= 1` in `expect`).
- Outcome predicates run against the contest checkout after the run. Paths are resolved inside the contest; one
  that leaves it (`..`, an absolute path elsewhere, a symlink out) fails the predicate with the note
  `path outside contest`, whatever the predicate asks. The new-question check compares with the open ids in
  `root.baseline_questions` (QUESTIONS.md as the host read it at setup; no baseline -> every open question is new,
  note `no question baseline`); `file_unchanged` compares with `root.baseline` (`sandbox.tracked_hashes`, taken by
  the host before the agent ran: blob id and sha256 per tracked file), never with the agent-writable object store; no
  baseline for the path -> false, note `no baseline`. With `ref` the blob on branch `refs/heads/<ref>` must have the
  baseline blob id and its bytes (`cat-file`, at most 1 MB) the baseline sha256 (a missing branch holds only with
  `ref_optional`); with `worktree` the bytes on disk under that contest-relative dir are hashed (a missing dir
  holds). `git_branch_ahead` counts `<initial>..refs/heads/<branch>`; `check_state` reads
  `checks.last_state` of `.house/checks/<task>.json`. `git_clean` ignores `.tmp-eval/`. `final_answer` is a regex
  (`match`, `re.search`) over the text of the LAST `result` record of a Claude log (its `result` string) or the last
  `agent_message` of a Codex log; no such text fails it. With `key` only the last line starting with `<key>:`
  (markdown bullets and emphasis allowed before it) is matched, whitespace-normalised (`final_answer_holds`). The text is compared in memory and only the boolean goes
  into the Grade (named `outcome[<i>] final_answer`, or by its `label`, when it failed, with the constant note `no
  final answer` when there was no text): it proves the agent answered, not what it said.
- `turns` is the number of main-thread `usage` events (model calls for Claude, turns for Codex; sub-agent events
  are excluded because the CLI's `--max-turns` does not count them), `subagent_turns` the sidechain ones, `tokens`
  the sums of the four buckets `input`, `cache_read`, `cache_write` (5m + 1h), `output` over all of them.
- Files: QUESTIONS.md, task files, check files and the files `file_unchanged` compares are resolved inside the
  contest and read only when they are regular files of at most 1 MB (`READ_LIMIT`), opened without blocking; any
  other one fails its predicate with the note `unreadable file`.
- Host git: the agent can write the contest's `.git`, so every grader git call goes through `_git`: the repository
  is named explicitly (`GIT_DIR`/`GIT_WORK_TREE`), system and global config are off, replace refs are ignored, no
  optional locks or prompts, `-c` overrides switch off fsmonitor, the untracked cache, signature display, hooks,
  gpg, external diff, the pager, the commit-graph and multi-pack-index files and submodule recursion
  (`git status --ignore-submodules=all`, so a nested repo's config is never read), system attributes are off,
  and a call times out after 30 s. Commit messages are read with `git cat-file commit`. Before any predicate runs,
  the local config and every `worktrees/*/config.worktree` are read as files (`git config --file --no-includes`);
  a key outside the allowlist (`core.repositoryformatversion|filemode|bare|logallrefupdates|ignorecase|precomposeunicode|symlinks`,
  `remote.*.url|fetch`, `branch.*.remote|merge`, `user.name|email`; so every `include.*`/`includeIf.*` and
  `extensions.*` too), a `.git` that is not a real directory, a `config.worktree` (the main worktree's own config),
  `commondir`, `info/grafts`, `shallow` or `modules` entry, or any `refs/replace/` ref is tampering.
- Privacy: a `Grade` holds only case labels, predicate names, counts and short notes (redacted with `guard.redact`);
  nothing the agent said, read or ran is copied into it.
"""
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from .. import checks, fm, questions, transcript
from ..guard import redact
from .cases import Call, Case, outcome_name
from .fixtures import clean_env
from .runner import INFRA_TEXT, RunResult, count_successes
from .sandbox import QUESTIONS_FILE, TempRoot
from .untrusted import Unreadable as _Unreadable
from .untrusted import read_regular

RESULTS = ("pass", "fail", "paused", "infra_error")
IGNORED_DIRTY = ".tmp-eval"
NOTE_LIMIT = 300
READ_LIMIT = 1 << 20  # bytes; QUESTIONS.md, task, check and compared files above it are not read
_TASK_ID = re.compile(r"[A-Za-z0-9_.-]+")


@dataclass
class Grade:
    """The verdict of one run.

    Attributes:
        result: `pass`, `fail`, `paused` or `infra_error`.
        missing: `expect` calls whose match count is out of range: `<label>: <count> (min .., max ..)`.
        forbidden: `forbid` calls that matched: `<label>: <count>`.
        order_violations: Violated `order` pairs as `before<after`.
        failed_predicates: Failed outcome predicates by name (`cases.outcome_name`: the predicate's `label`, else
            `outcome[<i>] <type>`); reasons such as `path outside contest` go to `note`.
        turns: Number of main-thread usage events in the log (what `case.max_turns` limits).
        tokens: `input`, `cache_read`, `cache_write` and `output` token sums.
        duration: Seconds the run took (from the runner).
        note: Short redacted explanation: `timeout`, `max_turns`, the infra reason, predicate notes.
        score: Passed entries / all entries (`expect`, `forbid`, outcome predicates); None when nothing was evaluated.
        hidden: Counts of the `hidden_tests` runs: `{"outcome": i, "exit": int | None, "passed": int, "failed": int}`.
        predicates: `{name: passed}` for every scored entry: the label of each `expect` and `forbid` call and
            the name of each outcome predicate (`cases.outcome_name`; `order` pairs are not scored); empty when
            nothing was evaluated. Booleans only: it lets `house eval compare` show which facts a variant lost.
    """
    result: str
    missing: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    order_violations: list[str] = field(default_factory=list)
    failed_predicates: list[str] = field(default_factory=list)
    turns: int = 0
    tokens: dict = field(default_factory=lambda: {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0})
    duration: float = 0.0
    note: str = ""
    subagent_turns: int = 0
    score: float | None = None
    hidden: list[dict] = field(default_factory=list)
    predicates: dict[str, bool] = field(default_factory=dict)


def _note(*parts: str) -> str:
    return redact("; ".join(p for p in parts if p))[:NOTE_LIMIT]


def _int(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _usage(parsed: transcript.Parsed) -> tuple[int, int, dict]:
    """Number of main-thread and of sidechain usage events, and the four token sums over both."""
    tokens = {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0}
    turns = sub_turns = 0
    for event in parsed.events:
        if event.kind != "usage":
            continue
        data = transcript.view(event)["data"]
        if event.sidechain:
            sub_turns += 1
        else:
            turns += 1
        tokens["input"] += _int(data["input"])
        tokens["cache_read"] += _int(data["cache_read"])
        tokens["cache_write"] += _int(data["cache_write_5m"]) + _int(data["cache_write_1h"])
        tokens["output"] += _int(data["output"])
    return turns, sub_turns, tokens


def _log_records(path: Path) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        try:
            rec = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _error_text(rec: dict) -> str:
    """The text of a terminal error record that can name a limit or authentication problem."""
    parts = [rec.get("result"), rec.get("subtype"), rec.get("message"), rec.get("error"), rec.get("errors")]
    return " ".join(p if isinstance(p, str) else json.dumps(p, default=str) for p in parts if p)


def _ended_on_limit(path: Path, tool: str) -> bool:
    """Whether the log's final terminal event is an error that names a usage limit or an authentication problem."""
    kinds = ("result",) if tool == "claude" else ("turn.completed", "turn.failed", "error")
    last = next((r for r in reversed(_log_records(path)) if r.get("type") in kinds), None)
    if last is None:
        return False
    failed = last.get("is_error") is True if last["type"] == "result" else last["type"] != "turn.completed"
    return failed and INFRA_TEXT.search(_error_text(last)) is not None


def _hit_max_turns(path: Path) -> bool:
    """Whether a Claude log's final `result` says the CLI stopped at its turn cap."""
    last = next((r for r in reversed(_log_records(path)) if r.get("type") == "result"), None)
    return last is not None and last.get("subtype") == "error_max_turns"


def _succeeded(parsed: transcript.Parsed) -> set[str]:
    """Ids of the tool calls whose (first) `tool_result` says they succeeded (`is_error` is False)."""
    first: dict[str, bool | None] = {}
    for event in parsed.events:
        if event.kind == "tool_result":
            data = transcript.view(event)["data"]
            first.setdefault(data["id"], data["is_error"])
    return {i for i, err in first.items() if i and err is False}


def _completed(case: Case, run: RunResult) -> bool:
    """Whether the conversation finished: Claude needs an exited process and exactly `1 + len(case.followups)`
    `success` results in the log (one per scheduled turn); Codex a `turn.completed`."""
    if case.tool == "claude":
        try:
            text = run.log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        return run.exit is not None and count_successes(text) == 1 + len(case.followups)
    return any(r.get("type") == "turn.completed" for r in _log_records(run.log))


def _final_text(path: Path, tool: str) -> str | None:
    """The text of the run's last answer: the final Claude `result` string, the last Codex `agent_message`.

    For comparison in memory only (`final_answer`); never stored in a Grade. None when the log has no such text.
    """
    records = _log_records(path)
    if tool == "claude":
        last = next((r for r in reversed(records) if r.get("type") == "result"), None)
        text = last.get("result") if last is not None else None
        return text if isinstance(text, str) else None
    for rec in reversed(records):
        item = rec.get("item")
        if rec.get("type") == "item.completed" and isinstance(item, dict) and item.get("type") == "agent_message":
            return item["text"] if isinstance(item.get("text"), str) else None
    return None


def _tool_turns(path: Path) -> dict[str, int]:
    """The user turn of every Claude tool call by its `tool_use` id: turn k holds the calls after the (k-1)-th
    `result` event of the stream (turn 1: before the first result). The first record of an id decides."""
    turns: dict[str, int] = {}
    results = 0
    for rec in _log_records(path):
        if rec.get("type") == "result":
            results += 1
        elif rec.get("type") == "assistant":
            message = rec.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_use" and isinstance(block.get("id"), str):
                    turns.setdefault(block["id"], results + 1)
    return turns


def _call_indexes(parsed: transcript.Parsed, calls: list[Call], turns: dict[str, int] | None = None
                  ) -> dict[str, list[int]]:
    """For each call label, the positions (among tool_call events) of the matching calls.

    A call with `ok` counts only when its tool result says it succeeded; one with `from_turn` only when the tool call
    was made during that user turn or later (`turns`, from `_tool_turns`; a call whose id is not there is turn 1).
    """
    found: dict[str, list[int]] = {c.label: [] for c in calls}
    succeeded = _succeeded(parsed) if any(c.ok for c in calls) else set()
    turns = turns or {}
    position = 0
    for event in parsed.events:
        if event.kind != "tool_call":
            continue
        data = transcript.view(event, bash_limit=None)["data"]  # whole redacted command; never stored in a Grade
        for c in calls:
            if c.ok and data["id"] not in succeeded:
                continue
            if c.from_turn is not None and turns.get(data["id"], 1) < c.from_turn:
                continue
            if data["name"] == c.tool and c.matches(data["input"]):
                found[c.label].append(position)
        position += 1
    return found


def _bounds(call: Call) -> str:
    return f"min {call.min}" + (f", max {call.max}" if call.max is not None else "")


def _check_calls(case: Case, parsed: transcript.Parsed, turns: dict[str, int] | None = None
                 ) -> tuple[list[str], list[str], list[str], bool]:
    """Returns `missing`, `forbidden`, `order_violations` and whether a violated pair names a `forbid` label."""
    found = _call_indexes(parsed, case.expect + case.forbid, turns)
    missing = [f"{c.label}: {len(found[c.label])} ({_bounds(c)})" for c in case.expect
               if len(found[c.label]) < c.min or (c.max is not None and len(found[c.label]) > c.max)]
    forbidden = [f"{c.label}: {len(found[c.label])}" for c in case.forbid if len(found[c.label]) >= c.min]
    forbid_labels = {c.label for c in case.forbid}
    violations, forbid_involved = [], False
    for before, after in case.order:
        b, a = found.get(before, []), found.get(after, [])
        if a and not (b and b[0] < a[0]):
            violations.append(f"{before}<{after}")
            forbid_involved = forbid_involved or before in forbid_labels or after in forbid_labels
    return missing, forbidden, violations, forbid_involved


class _Outside(Exception):
    """A predicate path leaves the contest."""


class _NoBaseline(Exception):
    """`file_unchanged` has no host-taken baseline for its path."""


GIT_TIMEOUT = 30
# Host git runs against an agent-writable `.git`: nothing in it may make git run a program or read other data.
_GIT_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_ATTR_NOSYSTEM": "1"}
_GIT_SAFE = ("-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false", "-c", "log.showSignature=false",
             "-c", "core.hooksPath=/dev/null", "-c", "gpg.program=false", "-c", "diff.external=",
             "-c", "core.pager=cat", "-c", "diff.ignoreSubmodules=all", "-c", "submodule.recurse=false",
             "-c", "status.submoduleSummary=false", "-c", "core.commitGraph=false", "-c", "core.multiPackIndex=false")
# Keys an honest contest config holds (fixtures, `house new|adapt|merge`, `git worktree add`, `git push -u`).
_ALLOWED_KEYS = {"core.repositoryformatversion", "core.filemode", "core.bare", "core.logallrefupdates",
                 "core.ignorecase", "core.precomposeunicode", "core.symlinks", "user.name", "user.email"}
_ALLOWED_SUBSECTION_KEYS = {("remote", "url"), ("remote", "fetch"), ("branch", "remote"), ("branch", "merge")}
# Per-repository sources git reads besides `config` that no fixture or house command creates: the main worktree's
# own config (read under `extensions.worktreeConfig`), a redirect of the common dir, history rewrites, submodules.
_TAMPER_FILES = ("config.worktree", "commondir", "info/grafts", "shallow", "modules")


def _git(contest: Path, *args: str) -> subprocess.CompletedProcess:
    """Run hardened host git on the contest repository.

    The repository is named explicitly (`GIT_DIR`, `GIT_WORK_TREE`, no discovery), system and global config are
    off, replace refs are ignored, and `-c` overrides switch off every config-driven program (fsmonitor, hooks,
    signature verification, external diff, pager). A missing directory or git, or the 30 s timeout, yields a failed
    result instead of raising.
    """
    contest = Path(contest).absolute()
    env = {**clean_env(), **_GIT_ENV, "GIT_DIR": str(contest / ".git"), "GIT_WORK_TREE": str(contest)}
    cmd = ["git", *_GIT_SAFE, *args]
    try:
        return subprocess.run(cmd, cwd=contest, env=env, capture_output=True, check=False, timeout=GIT_TIMEOUT,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(cmd, 127, b"", b"")


def _config_key_allowed(key: str) -> bool:
    """Whether a `git config --list` key (section and name lowercased by git) is on the allowlist."""
    section, _, rest = key.partition(".")
    subsection, dot, name = rest.rpartition(".")
    if dot:
        return (section.lower(), name.lower()) in _ALLOWED_SUBSECTION_KEYS and bool(subsection)
    return f"{section.lower()}.{rest.lower()}" in _ALLOWED_KEYS


def _config_tampered(contest: Path, cfg: Path) -> bool:
    """Whether one config file (read as a file: no includes, nothing else) holds a key outside the allowlist."""
    if cfg.is_symlink() or not cfg.is_file():
        return True
    r = _git(contest, "config", "--file", str(cfg), "--no-includes", "--null", "--list")
    if r.returncode != 0:
        return True
    keys = [entry.split(b"\n", 1)[0].decode("utf-8", errors="replace") for entry in r.stdout.split(b"\0") if entry]
    return not all(_config_key_allowed(k) for k in keys)


def _git_tampered(contest: Path) -> bool:
    """Whether the agent changed the contest's `.git` in a way that could make host git lie or run a program.

    Tampering: `.git` that is not a real directory (a gitfile or symlink redirects git), a `commondir`, grafts or
    `shallow` file, a config or `worktrees/*/config.worktree` key outside the allowlist (`include.*` and
    `includeIf.*` included), or any `refs/replace/` ref. A missing `.git` is not tampering: the git predicates then
    simply fail.
    """
    gitdir = Path(contest).absolute() / ".git"
    if not gitdir.exists() and not gitdir.is_symlink():
        return False
    if gitdir.is_symlink() or not gitdir.is_dir():
        return True
    if any((gitdir / p).exists() or (gitdir / p).is_symlink() for p in _TAMPER_FILES):
        return True
    configs = [gitdir / "config"] if (gitdir / "config").exists() or (gitdir / "config").is_symlink() else []
    configs += sorted(gitdir.glob("worktrees/*/config.worktree"))
    if any(_config_tampered(contest, cfg) for cfg in configs):
        return True
    r = _git(contest, "for-each-ref", "--format=%(refname)", "refs/replace/")
    return r.returncode != 0 or bool(r.stdout.strip())


def _inside(contest: Path, rel: str) -> Path:
    """Resolve `rel` in the contest; raise `_Outside` when the result is not inside it."""
    base = contest.resolve()
    try:
        target = (base / rel).resolve()
    except (ValueError, OSError, RuntimeError) as exc:  # NUL byte, symlink loop, ...
        raise _Outside from exc
    if not target.is_relative_to(base):
        raise _Outside
    return target


def _read_file(target: Path) -> bytes | None:
    """Bytes of a resolved path, None when it does not exist (`untrusted.read_regular` with `READ_LIMIT`)."""
    return read_regular(target, READ_LIMIT)


def _open_ids(text: str) -> set[str]:
    try:
        return {q.id for q in questions.parse(text) if q.status == "open"}
    except Exception:  # a broken file counts as having no open questions; grading never raises
        return set()


def _commit_message(contest: Path, sha: str) -> str | None:
    """The message of a commit, read with plumbing (`cat-file`, never `log`); None when it cannot be read."""
    r = _git(contest, "cat-file", "commit", sha)
    if r.returncode != 0:
        return None
    _, sep, message = r.stdout.partition(b"\n\n")
    return message.decode("utf-8", errors="replace") if sep else ""


def _current_open(contest: Path) -> set[str]:
    """Open question ids in the contest's QUESTIONS.md (none without the file).

    Raises:
        _Outside: The file resolves outside the contest.
        _Unreadable: It is not a regular file of at most `READ_LIMIT` bytes.
    """
    data = _read_file(_inside(contest, QUESTIONS_FILE))
    return _open_ids(data.decode("utf-8", errors="replace")) if data is not None else set()


_NO_BASELINE = object()


def _initial_open(root: TempRoot) -> tuple[set[str], str]:
    """Question ids open at the start, from the host baseline (`root.baseline_questions`), and a note.

    Never read from the agent-writable object store. Without a baseline every open question counts as new, with the
    note `no question baseline`; a baseline of None (QUESTIONS.md not tracked at the start) means none were open.
    """
    data = getattr(root, "baseline_questions", _NO_BASELINE)
    if data is _NO_BASELINE:
        return set(), "no question baseline"
    return (_open_ids(data.decode("utf-8", errors="replace")) if data is not None else set()), ""


def _git_branch(contest: Path, initial, p: dict) -> bool:
    r = _git(contest, "rev-parse", "--verify", "--quiet", f"refs/heads/{p['name']}^{{commit}}")
    return (r.returncode == 0) == p["exists"]


def _git_merge_commit_on(contest: Path, initial, p: dict) -> bool:
    ref = f"refs/heads/{p['branch']}"
    if _git(contest, "rev-parse", "--verify", "--quiet", ref).returncode != 0:
        return False
    span = f"{initial}..{ref}" if initial else ref
    r = _git(contest, "rev-list", "--min-parents=2", span)
    if r.returncode != 0:
        return False
    shas = r.stdout.decode().split()
    if "message_match" not in p:
        return bool(shas)
    regex = re.compile(p["message_match"])
    for sha in shas:
        msg = _commit_message(contest, sha)
        if msg is not None and regex.search(msg):
            return True
    return False


def _git_clean(contest: Path, initial, p: dict) -> bool:
    r = _git(contest, "status", "--porcelain", "--ignore-submodules=all", "--", ".", f":(exclude){IGNORED_DIRTY}")
    return r.returncode == 0 and not r.stdout.strip()


def _file_exists(contest: Path, initial, p: dict) -> bool:
    return _inside(contest, p["path"]).exists() == p["exists"]


def _git_branch_ahead(contest: Path, initial, p: dict) -> bool:
    if not initial:
        return False
    r = _git(contest, "rev-list", "--count", f"{initial}..refs/heads/{p['branch']}")
    try:
        return r.returncode == 0 and int(r.stdout.decode().strip()) >= p["min"]
    except ValueError:
        return False


def _blob_id(contest: Path, rev: str) -> str | None:
    r = _git(contest, "rev-parse", "--verify", "--quiet", rev)
    return r.stdout.decode().strip() if r.returncode == 0 else None


def _sha256(data: bytes | None) -> str | None:
    return hashlib.sha256(data).hexdigest() if data is not None else None


def _ref_blob(contest: Path, rev: str) -> bytes | None:
    """Bytes of the blob `rev` (`<ref>:<path>`), at most `READ_LIMIT`; None when it is missing or too large."""
    size = _git(contest, "cat-file", "-s", rev)
    try:
        if size.returncode != 0 or int(size.stdout.decode().strip()) > READ_LIMIT:
            return None
    except ValueError:
        return None
    r = _git(contest, "cat-file", "blob", rev)
    return r.stdout if r.returncode == 0 else None


def _file_unchanged(contest: Path, initial, p: dict, baseline: dict | None = None) -> bool:
    """`path` equals its baseline: in the main checkout, on branch `ref`, or in dir `worktree`.

    The baseline (`TempRoot.baseline`, host-taken before the agent ran) gives the blob id and sha256 of every file
    tracked at the initial commit; the agent-writable object store is never the reference.

    Raises:
        _NoBaseline: The run has no baseline, or the path was not tracked at the initial commit.
    """
    target = _inside(contest, p["path"])
    rel = target.relative_to(contest.resolve()).as_posix()
    if not baseline or rel not in baseline:
        raise _NoBaseline
    blob_id, digest = baseline[rel]
    if "ref" in p:
        if _blob_id(contest, f"refs/heads/{p['ref']}^{{commit}}") is None:
            return p.get("ref_optional", False)
        rev = f"refs/heads/{p['ref']}:{rel}"
        return _blob_id(contest, rev) == blob_id and _sha256(_ref_blob(contest, rev)) == digest
    if "worktree" in p:
        tree = _inside(contest, p["worktree"])
        if not tree.exists():
            return True
        target = _inside(contest, (tree / rel).relative_to(contest.resolve()).as_posix())
        if not target.is_relative_to(tree):
            raise _Outside
    return _sha256(_read_file(target)) == digest


def _task_status(contest: Path, initial, p: dict) -> bool:
    if not _TASK_ID.fullmatch(p["task"]):
        return False
    files = sorted((contest / ".house/tasks").glob(f"{p['task']}-*.md"))
    if not files:
        return False
    data = _read_file(_inside(contest, files[0].relative_to(contest).as_posix()))
    if data is None:
        return False
    try:
        meta, _ = fm.parse_frontmatter(data.decode("utf-8"))
    except ValueError:
        return False
    return meta.get("status") == p["status"]


def _check_state(contest: Path, initial, p: dict) -> bool:
    if not _TASK_ID.fullmatch(p["task"]):
        return False
    path = _inside(contest, f".house/checks/{p['task']}.json")
    if _read_file(path) is None:  # missing; irregular or oversized raises
        return False
    return checks.last_state(path).startswith(p["state"])


def _open_questions(contest: Path, initial, p: dict) -> bool:
    n = len(_current_open(contest))
    if "count" in p:
        return n == p["count"]
    return n >= p.get("min", 0) and n <= p.get("max", n)


class _Failed(Exception):
    """A predicate that does not hold, with a short note of counts (never of content)."""


def _hidden_tests(contest: Path, initial, p: dict, result: dict | None = None) -> bool:
    """The hidden tests' run has `exit` 0 and at least `min_passed` tests passed.

    Raises:
        _Failed: The run did not happen, timed out (`exit` None), printed a malformed summary (`malformed`, counts of
            more than 9 digits) or does not hold; the note has the counts.
    """
    if result is None:
        raise _Failed("hidden tests did not run")
    if result.get("exit") is None:
        raise _Failed("hidden tests timed out")
    if result.get("malformed"):
        raise _Failed("malformed test summary")
    if result["exit"] == 0 and result["passed"] >= p["min_passed"]:
        return True
    raise _Failed(f"hidden tests exit {result['exit']}, passed {result['passed']} < {p['min_passed']}"
                  if result["exit"] == 0 else
                  f"hidden tests exit {result['exit']}, passed {result['passed']}, failed {result['failed']}")


class _NoAnswer(Exception):
    """`final_answer` has no answer text in the log."""


_ANSWER_PREFIX = r"[^\S\n]*(?:>[^\S\n]*)*(?:[-*+][^\S\n]+)?[*_`]*"  # markdown quote, bullet and emphasis


def final_answer_holds(p: dict, answer: str) -> bool:
    """Whether an answer text satisfies a `final_answer` predicate (`match`, optional `key`).

    Without `key`, `match` is searched in the whole text. With `key`, only the LAST line whose start, after markdown
    quote marks, one list bullet and emphasis (`*`, `_`, backticks), is `<key>` and a colon (emphasis allowed between
    them) counts: a later line corrects an earlier one, and a value on the following line is not on the key's line.
    That line has its horizontal whitespace (spaces, tabs, no-break spaces, a trailing CR) collapsed to single spaces
    and stripped before `match` is searched in it; no line is crossed. No such line fails.

    Args:
        p: The predicate (`match`, optional `key`).
        answer: The final answer text.

    Returns:
        True when the text (or the keyed line) matches.
    """
    key = p.get("key")
    if key is None:
        return re.search(p["match"], answer) is not None
    start = re.compile(_ANSWER_PREFIX + re.escape(key) + r"[*_`]*[^\S\n]*:", re.IGNORECASE)
    lines = [line for line in answer.split("\n") if start.match(line)]
    if not lines:
        return False
    line = re.sub(r"[^\S\n]+", " ", lines[-1]).strip()
    return re.search(p["match"], line) is not None


def _final_answer(contest: Path, initial, p: dict, answer: str | None = None) -> bool:
    """The run's last answer text satisfies the predicate (`final_answer_holds`); the text is never kept.

    Raises:
        _NoAnswer: The log has no answer text.
    """
    if answer is None:
        raise _NoAnswer
    return final_answer_holds(p, answer)


_PREDICATES = {"git_branch": _git_branch, "git_merge_commit_on": _git_merge_commit_on, "git_clean": _git_clean,
               "git_branch_ahead": _git_branch_ahead, "file_exists": _file_exists, "file_unchanged": _file_unchanged,
               "task_status": _task_status, "check_state": _check_state, "open_questions": _open_questions,
               "final_answer": _final_answer, "hidden_tests": _hidden_tests}


_GIT_PREDICATES = {"git_branch", "git_merge_commit_on", "git_clean", "git_branch_ahead", "file_unchanged"}


def _check_outcome(case: Case, root: TempRoot, tampered: bool, answer: str | None = None,
                   hidden: dict[int, dict] | None = None) -> tuple[list[str], list[str]]:
    """Evaluate the outcome predicates; returns the failed ones (`outcome_name`: label or `outcome[i] type`) and
    their notes.

    With `tampered`, the predicates that run git are not evaluated and count as failed. `answer` is the run's final
    answer text, used only by `final_answer` and never stored; `hidden` holds the `hidden_tests` runs by index.
    """
    contest = Path(root.contest)
    initial = (root.info or {}).get("initial_commit")
    predicates = {**_PREDICATES, "file_unchanged": partial(_file_unchanged, baseline=getattr(root, "baseline", None)),
                  "final_answer": partial(_final_answer, answer=answer)}
    failed, notes = [], []
    for i, pred in enumerate(case.outcome):
        name = outcome_name(i, pred)
        if tampered and pred["type"] in _GIT_PREDICATES:
            failed.append(name)
            continue
        check = predicates[pred["type"]]
        if pred["type"] == "hidden_tests":
            check = partial(_hidden_tests, result=(hidden or {}).get(i))
        try:
            ok = check(contest, initial, pred)
        except _Failed as exc:
            ok, note = False, str(exc)
        except _Outside:
            ok, note = False, "path outside contest"
        except _Unreadable:
            ok, note = False, "unreadable file (not regular or over 1 MB)"
        except _NoBaseline:
            ok, note = False, "no baseline"
        except _NoAnswer:
            ok, note = False, "no final answer"
        except (OSError, RuntimeError, ValueError):
            ok, note = False, "could not evaluate"
        else:
            note = ""
        if not ok:
            failed.append(name)
            if note:
                notes.append(f"{name}: {note}")
    return failed, notes


def _question_cap_failed(case: Case, failed: list[str]) -> bool:
    """Whether an `open_questions` predicate that caps the count (`max` or `count`) failed."""
    return any(outcome_name(i, p) in failed for i, p in enumerate(case.outcome)
               if p["type"] == "open_questions" and ("max" in p or "count" in p))


def _expects_question(case: Case) -> bool:
    return any(p["type"] == "open_questions" and (p.get("min", 0) >= 1 or p.get("count", 0) >= 1)
               for p in case.outcome)


def _score(case: Case, missing: list[str], forbidden: list[str], failed: list[str]) -> float | None:
    """Passed entries over all entries: each `expect` call, `forbid` call and outcome predicate counts 1."""
    total = len(case.expect) + len(case.forbid) + len(case.outcome)
    if not total:
        return None
    return (total - len(missing) - len(forbidden) - len(failed)) / total


def _predicates(case: Case, missing: list[str], forbidden: list[str], failed: list[str]) -> dict[str, bool]:
    """`{name: passed}` for the scored entries; a call label fails when `missing` / `forbidden` names it."""
    labels = sorted((c.label for c in case.expect + case.forbid), key=len, reverse=True)
    lost = {next(label for label in labels if entry.startswith(f"{label}: ")) for entry in missing + forbidden}
    out = {c.label: c.label not in lost for c in case.expect + case.forbid}
    for i, pred in enumerate(case.outcome):
        name = outcome_name(i, pred)
        out[name] = name not in failed
    return out


def _assess(case: Case, run: RunResult, root: TempRoot, parsed: transcript.Parsed) -> tuple[str, dict]:
    """Check calls, order and outcome predicates of a finished run.

    Returns:
        The verdict `pass`, `fail` or `paused` and the `Grade` fields it rests on (`missing`, `forbidden`,
        `order_violations`, `failed_predicates`, `note`, `score`, `predicates`).
    """
    calls = case.expect + case.forbid
    turns = _tool_turns(run.log) if case.tool == "claude" and any(c.from_turn for c in calls) else None
    missing, forbidden, order, forbid_in_order = _check_calls(case, parsed, turns)
    contest = Path(root.contest)
    answer = _final_text(run.log, case.tool) if any(p["type"] == "final_answer" for p in case.outcome) else None
    tampered = _git_tampered(contest)
    failed, notes = _check_outcome(case, root, tampered, answer, run.hidden)

    def fields(*extra_notes: str) -> dict:
        return {"missing": missing, "forbidden": forbidden, "order_violations": order, "failed_predicates": failed,
                "note": _note(*extra_notes), "score": _score(case, missing, forbidden, failed),
                "predicates": _predicates(case, missing, forbidden, failed)}

    if tampered:
        return "fail", fields("git tampered", *notes)
    initial_open, baseline_note = _initial_open(root)
    if baseline_note:
        notes.append(baseline_note)
    try:
        new_questions = _current_open(contest) - initial_open
    except (_Outside, _Unreadable):
        return "fail", fields(*notes, "QUESTIONS.md unreadable")
    if (new_questions and not _expects_question(case) and not forbidden and not forbid_in_order
            and not _question_cap_failed(case, failed)):
        notes.append(f"{len(new_questions)} new open question(s)")
        return "paused", fields(*notes)
    return ("fail" if missing or forbidden or order or failed else "pass"), fields(*notes)


def grade(case: Case, run: RunResult, root: TempRoot) -> Grade:
    """Grade one run (see the module docstring for the rules).

    Args:
        case: The case that was run.
        run: The runner's result; its log is read here.
        root: The sandbox the agent worked in (still existing: the predicates read its contest).

    Returns:
        The `Grade`. Never raises on a broken log or a missing sandbox path; those fail the affected checks.
    """
    parse = transcript.claude_stream if case.tool == "claude" else transcript.codex_stream
    parsed = parse(run.log)
    turns, sub_turns, tokens = _usage(parsed)
    hidden = [{"outcome": i, **run.hidden[i]} for i in sorted(run.hidden)]
    base = {"turns": turns, "subagent_turns": sub_turns, "tokens": tokens, "duration": run.duration, "hidden": hidden}
    if run.infra:
        return Grade("infra_error", note=_note(run.infra), **base)
    if _ended_on_limit(run.log, case.tool):
        return Grade("infra_error", note="usage limit or authentication error", **base)
    stopped = ("timeout" if run.timed_out else "max_turns"
               if turns > case.max_turns or (case.tool == "claude" and _hit_max_turns(run.log)) else "")
    if not stopped and not _completed(case, run):
        return Grade("fail", note="incomplete", **base)
    result, fields = _assess(case, run, root, parsed)
    if stopped:  # the verdict is a fail; the lists and the score still say how far the run got
        return Grade("fail", **{**fields, "note": stopped}, **base)
    return Grade(result, **fields, **base)
