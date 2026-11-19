"""Facts about one attempt at a task, for `house evidence`.

The relation: facts only, nothing inferred. This module reads the contest state (git, `.house/` files) and states what
it holds; it never judges an attempt and never guesses a missing value (it says `unknown` or leaves a list empty).
`state_facts` reads the contest state only; `log_facts` adds the facts that the attempt's worker stream log holds,
read through the transcript reader (tool results are looked into in memory only; the only text taken from a result
is the redacted tail of an error). The gate (`house check`,
`house merge`) is the only source of a result: `last_check` repeats what `.house/checks/T###.json` records and nothing
else. Nothing is stored: facts are computed on demand from the register row and the current state, and every string
that enters `Facts` passes `guard.redact`.

Used by: `house evidence` (full and short views, attempt and session forms).
"""
import fnmatch
import os
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from . import attempts, checks, guard, handoff, transcript
from .attempts import Row

UNKNOWN = "unknown"
LONG_DIFFSTAT = 10
MAX_CALLS = 20
MAX_TAILS = 3
TAIL_LINES = 5
TAIL_WIDTH = 200
REPEAT_MIN = 3
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
CLAIM_RE = re.compile(r"\b(pass(ed|es)?|green|ok)\b", re.IGNORECASE)
# a section that names a failure is not a pass claim, even when it also says "pass" ("AC-1: fail (does not pass)")
NEGATIVE_RE = re.compile(r"\b(?:fail\w*|red|not|no|never|broken|error)\b|n't\b", re.IGNORECASE)
TEMPLATE_LINE = "Each acceptance check from the task: pass / fail."  # the unfilled line of reports/TEMPLATE.md
SECTION = "Result vs acceptance"


@dataclass
class Facts:
    """Facts about one attempt; every field has an "unknown" or empty default.

    Attributes:
        questions_added: Q-### ids the register row records as added to the main checkout's `QUESTIONS.md` during
            the attempt (`Row.questions`); None (unknown) for a row without that field.
        diffstat: `git diff --stat base..head`; only its summary line when the stat is longer than 10 lines; empty
            when `base` or `head` is missing or git cannot say.
        last_check: `GREEN`, `RED (AC-1, ...)`, `incomplete (AC-2, ...)`, `no check result` or `unknown` (no
            checks file), as recorded by the gate in `.house/checks/<task>.json`.
        violations: Guard violations of the task's worktree; empty when the worktree is gone (merged) or clean.
        check_after_last_edit: Log fact: `yes, GREEN` | `yes, RED` | `yes, unknown` | `no` | `unknown`, measured
            from the last code change (see `log_facts`).
        unbacked_claim: Log fact: the report claims a pass the log does not back; None when unknown.
        test_edits: Log fact: successful edits of test or protected files inside the contest.
        guard_refusals: Log fact: number of guard refusals; None when unknown or not applicable (Codex).
        repeats: Log fact: `(command, count)` of a command that failed at least 3 times.
        dead_end_repeats: Log fact: commands that already failed identically in an earlier attempt; None (unknown)
            when the worker log of an earlier attempt is missing.
        calls: Log fact: viewed tool calls in order.
        error_tails: Log fact: tails (last 5 lines, each cut to 200 characters, redacted) of the last 3 error
            results; each entry is the lines of one result joined by newlines.
        model: Log fact: model name, or `unknown`.
        duration: Log fact: wall time as text, or `unknown`.
        n_calls: Log fact: number of tool calls; None when unknown.
    """
    questions_added: list[str] | None = None
    diffstat: str = ""
    last_check: str = UNKNOWN
    violations: list[str] = field(default_factory=list)
    check_after_last_edit: str = UNKNOWN
    unbacked_claim: bool | None = None
    test_edits: list[str] = field(default_factory=list)
    guard_refusals: int | None = None
    repeats: list[tuple[str, int]] = field(default_factory=list)
    dead_end_repeats: list[str] | None = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    error_tails: list[str] = field(default_factory=list)
    model: str = UNKNOWN
    duration: str = UNKNOWN
    n_calls: int | None = None


def _git(cwd: Path, *args: str) -> str | None:
    """Run `git <args>` in `cwd`; return its stdout, or None when git is missing or exits non-zero."""
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, errors="replace")
    except OSError:
        return None
    return r.stdout if r.returncode == 0 else None


def _is_commit(contest: Path, commit: str) -> bool:
    """True when `commit` names a commit of the contest repo."""
    return bool(commit) and _git(contest, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}") is not None


def _diffstat(contest: Path, row: Row) -> str:
    """`git diff --stat base..head`, only its summary line when longer than `LONG_DIFFSTAT` lines."""
    if not (_is_commit(contest, row.base) and _is_commit(contest, row.head)):
        return ""
    lines = (_git(contest, "diff", "--stat", f"{row.base}..{row.head}") or "").strip().splitlines()
    if len(lines) > LONG_DIFFSTAT:
        lines = lines[-1:]
    return "\n".join(lines)


def _worktree(contest: Path, task: str) -> Path | None:
    """The task's worktree `.worktrees/<branch without task/>` when it exists, else None.

    The branch is the task file's `branch` field; without a task file the first `.worktrees/<task>-*` stands in.
    """
    try:
        branch = str(checks.load_task(contest / ".house", task).meta.get("branch") or "")
    except Exception:  # noqa: BLE001 — a malformed task file must not break an evidence view
        branch = ""
    root = contest / ".worktrees"
    candidates = [root / branch.removeprefix("task/")] if branch else []
    candidates += sorted(root.glob(f"{task}-*"))
    for path in candidates:
        # a branch like `task/../..` must not lead outside `.worktrees/`
        if path.is_dir() and path.resolve().parent == root.resolve():
            return path
    return None


def _violations(contest: Path, row: Row) -> list[str]:
    """Guard violations of the task's worktree; empty when it is gone or the guard cannot run."""
    wt = _worktree(contest, row.task)
    if wt is None:
        return []
    try:
        return guard.diff_violations(contest, row.task, wt)
    except Exception:  # noqa: BLE001 — odd state must never break an evidence view
        return []


def state_facts(contest: Path, row: Row) -> Facts:
    """Compute the facts about an attempt that the contest state holds.

    Never reads a session log and never raises on odd state (missing commits, unreadable files): such a fact falls
    back to `unknown` or empty.

    Args:
        contest: Contest root (a git checkout).
        row: The attempt's register row.

    Returns:
        `Facts` with `questions_added` (from the row: workers write questions into the main checkout through
        `$HOUSE_DIR`, never on the task branch), `diffstat`, `last_check` and `violations` filled and the log facts
        at their defaults. Every string is `guard.redact`ed.
    """
    path = contest / ".house" / "checks" / f"{row.task}.json"
    last = checks.last_state(path) if path.is_file() else UNKNOWN
    return Facts(
        questions_added=None if row.questions is None else [guard.redact(q) for q in row.questions],
        diffstat=guard.redact(_diffstat(contest, row)),
        last_check=guard.redact(last),
        violations=[guard.redact(v) for v in _violations(contest, row)])


# ---- facts from the stream log -----------------------------------------------------------------------------------
_SHELL_C = re.compile(r"^-[a-z]*c[a-z]*$")  # `-c`, `-lc`, `-ic`: the next word is a command line
_ASSIGNMENT = re.compile(r"^[A-Za-z_]\w*=")
_REDIRECT = re.compile(r"^(&>>?|[<>]{1,3}[&|]?)$")
_GROUPING = {"(", ")", "{", "}"}
_SHELLS = {"bash", "sh", "zsh", "dash"}
_NOT_A_RUN = ("--help", "--freeze", "--refreeze", "--manual", "--all")
_VALUE_FLAGS = ("--worktree", "--reason", "--evidence")
# interpreter options that take no value and still run the script that follows (`python3 -u -B .../house`)
_PYTHON_FLAGS = re.compile(r"^-[BEIORSbdqsuv]+$")


def _simple_commands(command: str) -> tuple[list[tuple[list[str], str, bool]], bool]:
    """Split a command line into `(words, separator after, negated)`, redirections dropped.

    Returns:
        The simple commands in order (the separator is one of `&&`, `||`, `;`, `|`, `&`, or "" for the last one)
        and whether the line has grouping (`( )`, `{ }`), whose exit code the caller cannot follow.
    """
    toks = guard._tokens(command)
    grouped = any(t in _GROUPING for t in toks)
    segs: list[tuple[list[str], str, bool]] = []
    cur: list[str] = []

    def close(sep: str) -> None:
        nonlocal cur
        neg = bool(cur) and cur[0] == "!"
        words = [w for w in cur if w not in _GROUPING and w != "!"]
        if words:
            segs.append((words, sep, neg))
        cur = []

    i = 0
    while i < len(toks):
        tok = toks[i]
        if tok in guard.CONTROL or tok == "|&":
            close("|" if tok == "|&" else tok)  # `|&` is a pipe that also carries stderr
        elif _REDIRECT.match(tok):
            i += 1  # the redirect target
        elif not (tok.isdigit() and i + 1 < len(toks) and _REDIRECT.match(toks[i + 1])):
            cur.append(tok)
        i += 1
    close("")
    if segs and segs[-1][1] == ";":
        segs[-1] = (segs[-1][0], "", segs[-1][2])
    return segs, grouped


def _is_option(name: str, options: tuple[str, ...]) -> bool:
    """True when `name` is one of `options` or, as argparse allows, an abbreviation of one (`--man` for `--manual`).

    An abbreviation needs at least three characters including the dashes; ambiguous ones are not told apart here
    (`--re` counts for `--refreeze`, which only errs on the side of "not a run").
    """
    return name in options or (name.startswith("--") and len(name) >= 3 and any(o.startswith(name) for o in options))


def _check_args(args: list[str], task: str) -> bool | None:
    """None when `args` (after `house`) do not run the gate on `task` (help, freeze, `--all`, another task, ...);
    else whether they name a worktree (`--worktree`)."""
    if not args or args[0] != "check":
        return None
    positional, skip, worktree = [], False, False
    for arg in args[1:]:
        name = arg.split("=")[0]
        if skip:
            skip = False
        elif name == "-h" or _is_option(name, _NOT_A_RUN):
            return None
        elif _is_option(name, _VALUE_FLAGS):
            worktree = worktree or _is_option(name, ("--worktree",))
            skip = "=" not in arg
        elif not arg.startswith("-"):
            positional.append(arg)
    return None if len(positional) >= 2 and positional[1] != task else worktree


def _shell_c(words: list[str]) -> str | None:
    """The command line of `bash -lc '<cmd>'` / `sh -c '<cmd>'` (`words[0]` is the shell), else None."""
    for k, word in enumerate(words[1:], 1):
        if _SHELL_C.match(word):
            return words[k + 1] if k + 1 < len(words) else None
        if not word.startswith("-"):
            break
    return None


def _segment_run(words: list[str], task: str, depth: int, need_worktree: bool) -> bool | None:
    """None when the simple command is not a `house check` of `task`; else whether its exit code is the check's."""
    i = 0
    while i < len(words) and _ASSIGNMENT.match(words[i]):
        i += 1
    words = words[i:]
    if not words:
        return None
    cmd = os.path.basename(words[0])
    if cmd.startswith("python"):  # `python3 [-u -B ...] .../house check ...`; `-c`, `-m`, `-V`, `-` run no script
        rest = words[1:]
        j = 0
        while j < len(rest) and _PYTHON_FLAGS.match(rest[j]):
            j += 1
        if j < len(rest) and os.path.basename(rest[j]) == "house":
            words, cmd = rest[j:], "house"
    if cmd == "house":
        worktree = _check_args(words[1:], task)
        # without `--worktree` the gate checks the main checkout, not the attempt's work
        return None if worktree is None else worktree or not need_worktree
    if cmd in _SHELLS and depth < 2:  # `bash -lc '<cmd>'` (how Codex wraps commands)
        inner = _shell_c(words)
        return _check_run(inner, task, depth + 1, need_worktree) if inner is not None else None
    return None


def _check_run(command: str, task: str, depth: int = 0, need_worktree: bool = False) -> bool | None:
    """Classify a Bash command line as a run of `house check` for `task`.

    A call counts as a run when a simple command has the command word `house` (after leading `VAR=value`
    assignments, or `python3 [-u -B -E -I -s -S -O ...] .../house`) followed by `check`, without `--help`,
    `--freeze`, `--refreeze`, `--manual` or `--all`, and with `task` as its task id when it names one.
    `bash -lc '...'` and `sh -c '...'` are unwrapped (two levels).

    Args:
        command: The command line.
        task: Task id.
        depth: Shell unwrapping depth (internal).
        need_worktree: A run without `--worktree` checks the main checkout, not the attempt's work, so its exit
            code says nothing about the attempt (a night attempt).

    Returns:
        None when the command is not such a run. True when it is and its exit code is the whole command's: the run
        is the last command, earlier ones are joined by `&&` or `;`, and it is not piped, negated, backgrounded or
        grouped (and names a worktree when `need_worktree`). False when it is a run whose exit code says nothing
        about the check (`| tail`, `|| true`, `!`, `&`, no `--worktree`).
    """
    try:
        segs, grouped = _simple_commands(command)
    except Exception:  # noqa: BLE001 — an odd command must never break an evidence view
        return None
    found: bool | None = None
    for idx, (words, sep, neg) in enumerate(segs):
        inner = _segment_run(words, task, depth, need_worktree)
        if inner is None:
            continue
        owned = (inner and not grouped and not neg and idx == len(segs) - 1 and sep == ""
                 and all(prev_sep in ("&&", ";") for _, prev_sep, _ in segs[:idx]))
        found = owned if found is None else (found or owned)
    return found


def _log_path(contest: Path, row: Row) -> Path | None:
    """The attempt's log file; None when the row has none, it lies outside the contest or is not a file."""
    if not row.log:
        return None
    path = contest / row.log
    try:
        path.resolve().relative_to(contest.resolve())
    except (OSError, ValueError, RuntimeError):
        return None
    return path if path.is_file() else None


def _events(contest: Path, row: Row) -> list[transcript.Event]:
    """Events of the attempt's log; empty when there is no log, it is unreadable or it lies outside the contest."""
    path = _log_path(contest, row)
    if path is None:
        return []
    parse = transcript.codex_stream if row.tool == "codex" else transcript.claude_stream
    return parse(path).events


@dataclass
class _Call:
    """A tool call with its matching result (`result` None when the log has none)."""
    index: int
    event: transcript.Event
    view: dict
    result: transcript.Event | None

    @property
    def name(self) -> str:
        return self.view["data"]["name"]

    @property
    def inp(self) -> dict:
        return self.view["data"]["input"]

    @property
    def is_error(self) -> bool | None:
        """The result's `is_error` (True, False or None); None also when there is no result."""
        err = self.result.data.get("is_error") if self.result is not None else None
        return err if isinstance(err, bool) else None


def _calls(events: list[transcript.Event]) -> list[_Call]:
    """Pair every tool call with the result of the same id (the last one wins), in call order."""
    results = {e.data.get("id"): e for e in events if e.kind == "tool_result" and e.data.get("id")}
    return [_Call(i, e, transcript.view(e), results.get(e.data.get("id")))
            for i, e in enumerate(e for e in events if e.kind == "tool_call")]


def _raw_command(call: _Call) -> str:
    cmd = _input(call.event).get("command")
    return cmd if isinstance(cmd, str) else ""


def _input(event: transcript.Event) -> dict:
    inp = event.data.get("input")
    return inp if isinstance(inp, dict) else {}


def _failed_commands(calls: list[_Call]) -> list[str]:
    """Redacted (view form) Bash commands whose result is an error, one entry per failure, in order."""
    return [c.inp["command"] for c in calls if c.name == "Bash" and c.is_error is True and c.inp.get("command")]


def _call_text(call: _Call) -> str:
    """A tool call as a short string: `Bash: <command>`, `<tool>: <file_path or subagent_type>`, else the name."""
    inp = call.inp
    detail = inp.get("command") or inp.get("file_path") or inp.get("subagent_type") or ""
    detail = " ".join(detail.split())
    return f"{call.name}: {detail}" if detail else call.name


def _check_status(call: _Call, task: str, need_worktree: bool = False) -> str | None:
    """`GREEN`, `RED` or `unknown` for a `house check` call, None when the call is not a run of the gate.

    A run started with `run_in_background: true` returns at once, so its result is not the check's: `unknown`.
    """
    owned = _check_run(_raw_command(call), task, need_worktree=need_worktree) if call.name == "Bash" else None
    if owned is None:
        return None
    if _input(call.event).get("run_in_background") is True:
        return UNKNOWN
    return {False: "GREEN", True: "RED"}.get(call.is_error, UNKNOWN) if owned else UNKNOWN


def _last_check(calls: list[_Call], task: str, after: int = -1, need_worktree: bool = False) -> str | None:
    """Status of the last `house check` call after call index `after`; None when there is none."""
    statuses = [st for c in calls if c.index > after and (st := _check_status(c, task, need_worktree)) is not None]
    return statuses[-1] if statuses else None


def _code_rel(path: str, contest: Path, cwd: str | None = None) -> str | None:
    """`path` relative to the attempt's checkout, or None when it lies outside the contest.

    A relative path is taken relative to `cwd`, or, when that is unknown, to the worker's own checkout (it stays as
    it is). An absolute path must lie in the contest (its task worktrees included); `.worktrees/<name>/` is stripped.
    """
    p = PurePosixPath(os.path.expanduser(path))
    if not p.is_absolute() and cwd:
        p = PurePosixPath(cwd) / p
    p = PurePosixPath(os.path.normpath(p.as_posix()))
    if p.is_absolute():
        for root in dict.fromkeys((contest.as_posix(), contest.resolve().as_posix())):
            try:
                p = p.relative_to(root)
                break
            except ValueError:
                continue
        else:
            return None
    if p.parts[:1] == ("..",):
        return None
    if p.parts[:1] == (".worktrees",):
        if len(p.parts) < 2:
            return None
        p = PurePosixPath(*p.parts[2:]) if len(p.parts) > 2 else PurePosixPath(".")
    return p.as_posix()


def _is_code(rel: str | None) -> bool:
    """True when a contest-relative path (from `_code_rel`) is the attempt's code: inside the contest and not under
    `.house/` (the report, PROGRESS.md, the task file and questions are bookkeeping, not code)."""
    return rel is not None and PurePosixPath(rel).parts[:1] != (".house",)


# git commands that change the files of the checkout they run in (`git -C dir` respected)
_GIT_TREE_WRITERS = {"apply", "am", "restore", "reset", "stash", "merge", "rebase", "cherry-pick", "revert", "pull",
                     "switch", "checkout"}


def _git_tree_write(verb: str, rest: list[str]) -> bool:
    """True when `git <verb> <rest>` changes working tree files (not only the index or refs)."""
    if verb == "apply":
        return not any(a in ("--check", "--stat", "--numstat", "--summary", "--cached") for a in rest)
    if verb == "restore":
        return not any(a in ("--staged", "-S") for a in rest) or any(a in ("--worktree", "-W") for a in rest)
    if verb == "reset":
        return any(a in ("--hard", "--merge", "--keep") for a in rest)
    if verb == "stash":
        return not rest or rest[0] in ("push", "save", "pop", "apply")
    if verb in ("checkout", "switch"):
        return any(not a.startswith("-") for a in rest) and not any(a in ("-b", "-B", "-c", "-C", "--create")
                                                                      for a in rest)
    return verb in _GIT_TREE_WRITERS


# the base of a command whose directory is unknown (after `cd "$DIR"`); nothing relative to it is counted
_UNKNOWN_DIR = Path("/.house-unknown-directory")
_PIPES = ("|", "|&")
_MAX_BASES = 64


def _shell_tokens(command: str) -> list[str]:
    """`guard._tokens` (operators already split apart: `);` is `)` and `;`) with `{ } !` dropped."""
    return [tok for tok in guard._tokens(command) if tok not in ("{", "}", "!")]


def _write_targets(words: list[str], base: Path, skip_root: Path | None = None) -> list[tuple[str, Path]]:
    """`(target, cwd)` pairs a simple command writes: guard's targets (redirections, `tee`, `rm`, `mv`, `sed -i`,
    `cp`, ...) plus a checkout restore (`git checkout [HEAD] -- paths`), git commands that rewrite the working tree
    and `patch` (their target is the directory they run in). The targets of a git command that runs in `skip_root`
    (the contest's main checkout, which is not the attempt's work) are left out."""
    out = guard._written_paths(words, base)
    cmd = os.path.basename(words[0])
    if cmd == "git":
        sub, gcwd = guard._git_parts(words, base)
        verb, rest = (sub[0], sub[1:]) if sub else ("", [])
        if verb == "checkout" and "--" in rest:
            out += [(a, gcwd) for a in rest[rest.index("--") + 1:]]
        elif verb in _GIT_TREE_WRITERS and _git_tree_write(verb, rest):
            out.append((".", gcwd))
        out = [(t, c) for t, c in out if not _same_dir(c, skip_root)]
    elif cmd == "patch" and not any(a in ("--dry-run", "-C", "--check") for a in words[1:]):
        where = next((words[i + 1] for i, a in enumerate(words[:-1]) if a in ("-d", "--directory")), None)
        out.append((".", base / where if where else base))
    return out


def _same_dir(path: Path, other: Path | None) -> bool:
    try:
        return other is not None and path.resolve() == other.resolve()
    except (OSError, RuntimeError):
        return False


def _cd_base(words: list[str], base: Path) -> Path:
    """The directory after `cd <dir>` / `pushd <dir>` run in `base` (a relative `<dir>` is taken against it);
    `_UNKNOWN_DIR` when `<dir>` is no literal path (no argument, `-`, `$`, a backtick, `~`, a glob) or `base` is
    unknown and `<dir>` relative."""
    args = [a for a in words[1:] if a not in ("-L", "-P", "-e", "-@", "--")]
    if len(args) != 1 or args[0] == "-" or any(ch in args[0] for ch in "$`~*?[{\\"):
        return _UNKNOWN_DIR
    target = args[0]
    if not os.path.isabs(target) and _same_dir(base, _UNKNOWN_DIR):
        return _UNKNOWN_DIR
    return Path(os.path.normpath((base / target).as_posix()))


def _simple_writes_code(words: list[str], cwd: Path, contest: Path, depth: int, session: Path,
                        skip_root: Path | None) -> bool:
    """`_writes_code` for one simple command (leading assignments dropped) run in `cwd`."""
    if os.path.basename(words[0]) in _SHELLS:
        inner = _shell_c(words)
        return depth < 2 and inner is not None and _writes_code(inner, cwd, contest, depth + 1, session)
    for target, rel_cwd in _write_targets(words, cwd, skip_root):
        # an undeterminable target (a variable, a command substitution, `~user`, a directory that is unknown) is
        # deliberately not counted: the view never claims a code change it cannot name
        if "$" in target or "`" in target:
            continue
        target = os.path.expanduser(target)
        if not os.path.isabs(target) and (rel_cwd.is_relative_to(_UNKNOWN_DIR)
                                          or any(ch in rel_cwd.as_posix() for ch in "$`~")):
            continue
        full = target if os.path.isabs(target) else (rel_cwd / target).as_posix()
        if _is_code(_code_rel(full, contest)):
            return True
    return False


def _writes_code(command: str, base: Path, contest: Path, depth: int = 0, session: Path | None = None) -> bool:
    """True when the shell command writes a file of the attempt's code (see `_is_code`) by a form whose target the
    command line names; `bash -lc '...'` is unwrapped. Targets with `$` or a backtick cannot be told: no write.

    `cd <dir>` / `pushd <dir>` move the directory later relative targets resolve against, as far as the shell would
    carry them: not out of a `( ... )` group, a pipeline or a backgrounded list; after `||` (the `cd` may or may not
    have run) the targets are tried against both directories. A `cd` that opens an `&&` list is certain for the rest
    of the command; one after another command of the list (`X && cd D`) is certain only inside that list: after
    it ends (`;`, `||`, a newline) both directories are tried. `popd`, or a `<dir>` that is not a literal path, make
    the directory unknown: such targets do not count. `session` is the directory the command starts in; a git
    command that rewrites the contest's main checkout from elsewhere is not the attempt's change.
    """
    try:
        toks = _shell_tokens(command)
    except Exception:  # noqa: BLE001 — an odd command must never break an evidence view
        return False
    session = base if session is None else session
    skip_root = None if _same_dir(session, contest) else contest
    bases = [base]       # the directories the next command may run in
    list_start = bases   # ... at the start of the current `;`-list (restored when the list is backgrounded)
    pipe_base = bases    # ... at the start of the current pipeline
    pending: list[Path] = []  # the directories before a `cd` that ran only if the commands before it in its `&&` list did
    stack: list[tuple[list[Path], list[Path], list[Path]]] = []
    prev = ""            # the operator before the current command
    cur: list[str] = []

    def run(nxt: str) -> bool:
        nonlocal bases, pending
        words, i = cur[:], 0
        while i < len(words) and _ASSIGNMENT.match(words[i]):
            i += 1
        words = words[i:]
        if not words:
            return False
        cmd = os.path.basename(words[0])
        piped = prev in _PIPES or nxt in _PIPES
        if cmd in ("cd", "pushd", "popd"):
            if not piped:  # a pipeline runs its commands in subshells
                new = [_UNKNOWN_DIR] if cmd == "popd" else [_cd_base(words, b) for b in bases]
                if "||" in (prev, nxt):
                    bases = list(dict.fromkeys(bases + new))[:_MAX_BASES]
                else:
                    if prev == "&&":
                        pending = list(dict.fromkeys(pending + bases))[:_MAX_BASES]
                    bases = new
            return False
        return any(_simple_writes_code(words, cwd, contest, depth, session, skip_root)
                   for cwd in (pipe_base if prev in _PIPES else bases))

    for tok in toks:
        if tok in guard.CONTROL or tok == "|&":
            if run(tok):
                return True
            cur = []
            if tok == "&":
                pending = []
            elif tok not in ("&&", *_PIPES) and pending:  # the `&&` list is over: its later `cd` may not have run
                bases, pending = list(dict.fromkeys(bases + pending))[:_MAX_BASES], []
            if tok in (";", ";;"):
                list_start = bases
            elif tok == "&":  # the list so far ran in a background subshell: its `cd` stays there
                bases = list_start
            elif tok in _PIPES and prev not in _PIPES:
                pipe_base = bases
            prev = tok
        elif tok == "(":
            run(tok)  # a command in front of `(` is no valid shell; nothing to carry
            cur = []
            stack.append((bases, list_start, pending))
            list_start, prev, pending = bases, "", []
        elif tok == ")":
            if run(tok):
                return True
            cur = []
            if stack:
                bases, list_start, pending = stack.pop()
            prev = ""
        else:
            cur.append(tok)
    return run("")


def _code_changes(contest: Path, row: Row, calls: list[_Call]) -> list[int]:
    """Indices of the calls that changed the attempt's code: a successful edit tool call (`is_error is False`) of a
    path `_is_code` accepts, or a Bash call that is not a `house check` run and writes such a path (`_writes_code`;
    counted whatever its result, since a failed command may still have written)."""
    checkout = _worktree(contest, row.task) or contest / ".worktrees" / row.task
    need = row.source == "night"
    out = []
    for c in calls:
        if c.name in EDIT_TOOLS:
            path = _input(c.event).get("file_path")
            if c.is_error is False and isinstance(path, str) and _is_code(_code_rel(path, contest, c.event.cwd)):
                out.append(c.index)
        elif c.name == "Bash" and _check_status(c, row.task, need) is None:
            base = Path(c.event.cwd) if c.event.cwd else checkout
            if _writes_code(_raw_command(c), base, contest):
                out.append(c.index)
    return out


def _check_after_last_edit(calls: list[_Call], task: str, changes: list[int], need_worktree: bool) -> str:
    """`yes, GREEN|RED|unknown` | `no` | `unknown` (see `Facts.check_after_last_edit`); needs a code change."""
    if not changes:
        return UNKNOWN
    status = _last_check(calls, task, changes[-1], need_worktree)
    return "no" if status is None else f"yes, {status}"


def _start_time(row: Row) -> float | None:
    """`row.start` as a POSIX time (a time without offset is local, as `night.now_iso` writes it); None if bad."""
    try:
        return datetime.fromisoformat(row.start).timestamp() if row.start else None
    except (ValueError, OverflowError, OSError):
        return None


def _report_path(contest: Path, row: Row) -> Path | None:
    """The report that covers the attempt, or None when that is unknown.

    `row.report` (relative to the contest, like `log`) when the row names one: None when that file is missing or
    lies outside the contest. Else the live `.house/reports/<task>.md`, only for the task's latest attempt and only
    when it does not exist (no report: no claim) or was written at or after `row.start`; an older file belongs to an
    earlier attempt, and a row without a readable start cannot tell.
    """
    house = contest / ".house"
    if row.report:
        path = contest / row.report
        try:
            path.resolve().relative_to(contest.resolve())
        except (OSError, ValueError, RuntimeError):
            return None
        return path if path.is_file() else None
    latest = max((r.n for r in attempts.rows(house, row.task)), default=0)
    if row.n < latest:
        return None
    live = house / "reports" / f"{row.task}.md"
    if not live.exists():
        return live
    start = _start_time(row)
    try:
        return live if start is not None and live.stat().st_mtime >= start else None
    except OSError:
        return None


def _report_claims_pass(path: Path) -> bool:
    """True when the report's `Result vs acceptance` section claims a pass.

    A claim is a match of `CLAIM_RE` in the section (the unfilled template line `TEMPLATE_LINE` left out) when the
    section has no negative marker (`NEGATIVE_RE`: fail, red, not, n't, no, never, broken, error). False without
    report or section.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    body = handoff.report_sections(text).get(SECTION)
    if body is None:
        return False
    section = "\n".join(ln for ln in body.splitlines() if ln.strip(" \t-*") != TEMPLATE_LINE)
    return bool(CLAIM_RE.search(section)) and not NEGATIVE_RE.search(section)


def _is_test_path(rel: str, protected: list[str]) -> bool:
    name = PurePosixPath(rel).name
    if rel.startswith("tests/") or fnmatch.fnmatch(name, "*_test.*") or fnmatch.fnmatch(name, "test_*.*"):
        return True
    return any(rel == p or rel.startswith(p.rstrip("/") + "/") for p in (q.removeprefix("./") for q in protected))


def _test_edits(contest: Path, row: Row, calls: list[_Call]) -> list[str]:
    try:
        protected = checks.load_task(contest / ".house", row.task).protected
    except Exception:  # noqa: BLE001 — a missing or malformed task file only means no protected list
        protected = []
    found: list[str] = []
    for c in calls:
        path = c.inp.get("file_path")
        if c.name not in EDIT_TOOLS or c.is_error is not False or not path:
            continue
        rel = _code_rel(path, contest, c.event.cwd)  # a path outside the contest is not the attempt's file
        if rel is not None and _is_test_path(rel, protected) and rel not in found:
            found.append(rel)
    return found


def _tail(text: str) -> str:
    """The last `TAIL_LINES` lines of `text`, redacted first, then each cut to `TAIL_WIDTH` characters."""
    lines = guard.redact(text).strip().splitlines()[-TAIL_LINES:]
    return "\n".join(line[:TAIL_WIDTH] for line in lines)


def _parse_time(text: str | None) -> datetime | None:
    try:
        when = datetime.fromisoformat(text) if text else None
    except ValueError:
        return None
    return when.replace(tzinfo=timezone.utc) if when and when.tzinfo is None else when


def _duration(row: Row, events: list[transcript.Event]) -> str:
    """Wall time as `45s`, `12m 03s` or `1h 02m 03s`: row start/end, else first/last event time, else unknown."""
    start, end = _parse_time(row.start), _parse_time(row.end)
    if start is None or end is None:
        times = [t for t in (_parse_time(e.ts) for e in events) if t is not None]
        start, end = (times[0], times[-1]) if times else (None, None)
    if start is None or end is None:
        return UNKNOWN
    seconds = int((end - start).total_seconds())
    if seconds < 0:
        return UNKNOWN
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"


def _earlier_failures(contest: Path, row: Row) -> set[str] | None:
    """Failing commands (view form) of the earlier attempts of the task, from the logs the register points to.

    A row without a log (a `merge` row, a worker that never started) ran no commands; None (unknown) when a row
    names a log that is missing.
    """
    seen: set[str] = set()
    for earlier in attempts.rows(contest / ".house", row.task):
        if earlier.n < row.n and earlier.log:
            if _log_path(contest, earlier) is None:
                return None
            seen.update(_failed_commands(_calls(_events(contest, earlier))))
    return seen


def _stream_part(events: list[transcript.Event], calls: list[_Call], codex: bool) -> dict:
    """The log facts that need no attempt: guard refusals, repeats, calls, error tails, model, number of calls."""
    refusals = None
    if not codex:
        refusals = sum(1 for e in events if e.kind == "tool_result" and e.data.get("is_error") is True
                       and "house guard:" in transcript.result_text(e))
    tails = [t for t in (_tail(transcript.result_text(c.result)) for c in calls
                         if c.result is not None and c.is_error is True) if t][-MAX_TAILS:]
    texts = [_call_text(c) for c in calls]
    if len(texts) > MAX_CALLS:
        texts = texts[:MAX_CALLS] + [f"and {len(texts) - MAX_CALLS} more"]
    usage = [e for e in events if e.kind == "usage"]
    models = Counter(m for m in (transcript.view(e)["model"] for e in usage if not e.sidechain) if m) \
        or Counter(m for m in (transcript.view(e)["model"] for e in usage) if m)
    return dict(
        guard_refusals=refusals,
        repeats=[(cmd, n) for cmd, n in Counter(_failed_commands(calls)).items() if n >= REPEAT_MIN],
        calls=texts, error_tails=tails, model=models.most_common(1)[0][0] if models else UNKNOWN,
        n_calls=len(calls))


def _log_facts(contest: Path, row: Row, facts: Facts) -> Facts:
    events = _events(contest, row)
    if not events:
        return facts
    calls = _calls(events)
    need = row.source == "night"
    changes = _code_changes(contest, row, calls)
    check = _check_after_last_edit(calls, row.task, changes, need)
    failed = _failed_commands(calls)
    report = _report_path(contest, row)
    # without a code change the log can see, the last check of the whole log counts
    backed = check == "yes, GREEN" if changes else _last_check(calls, row.task, -1, need) == "GREEN"
    dead: list[str] | None = []
    if row.n >= 2:
        earlier = _earlier_failures(contest, row)
        dead = None if earlier is None else [cmd for cmd in dict.fromkeys(failed) if cmd in earlier]
    return replace(
        facts, check_after_last_edit=check,
        unbacked_claim=None if report is None else _report_claims_pass(report) and not backed,
        test_edits=[guard.redact(p) for p in _test_edits(contest, row, calls)],
        dead_end_repeats=dead, duration=_duration(row, events), **_stream_part(events, calls, row.tool == "codex"))


def session_facts(events: list[transcript.Event]) -> Facts:
    """Facts about an interactive Claude Code session, which has no attempt, task or report.

    Only the facts that need no attempt are computed (guard refusals, repeated failing commands, calls, error
    tails, model, number of calls), by the same rules as `log_facts`; every other field stays at its default.
    Nothing is stored. Never raises.

    Args:
        events: Events of the session, from `transcript.claude_session`.

    Returns:
        The `Facts`; every string is redacted (it comes from `transcript.view` or `guard.redact`).
    """
    try:
        return Facts(**_stream_part(events, _calls(events), False))
    except Exception:  # noqa: BLE001 — odd logs must never break an evidence view
        return Facts()


def log_facts(contest: Path, row: Row, facts: Facts) -> Facts:
    """Add the facts the attempt's worker stream log holds to `facts`.

    Reads `row.log` (relative to the contest) with `transcript.claude_stream`, or `codex_stream` when
    `row.tool == "codex"`. Strings come from `transcript.view` or pass `guard.redact`; tool result text is used in
    memory for detection and for the redacted tails of error results only. Without a readable, non-empty log the
    log facts stay at their defaults. Never raises.

    Rules: a code change is an `Edit`/`Write`/`MultiEdit`/`NotebookEdit` call whose result has `is_error is False`
    (None never counts), or a Bash call (not a `house check` run) whose command writes a named file (redirection,
    `sed -i`, `tee`, `mv`, `cp`, `rm`, `git checkout/apply/restore`, `patch`, ...), of a path inside the contest or
    the task worktree and not under `.house/` (the report, PROGRESS.md and the task status written after the check
    are bookkeeping). `check_after_last_edit` is `unknown` when there is no code change. `unbacked_claim` is None
    when no log was read or the report is unknown: the report is `row.report` (the per-attempt copy, relative to the
    contest; None when missing), or the live `.house/reports/<task>.md` when `row.report` is empty, the row is the
    task's latest attempt and the file was written at or after `row.start`. Without a code change the last
    `house check` of the whole log decides whether a pass claim is backed. A `house check` counts as a run only with
    its own exit code (see `_check_run`); piped, negated, backgrounded (`&`, `run_in_background`) or `||`-joined
    runs, and in a night attempt runs without `--worktree`, give `yes, unknown`.

    Args:
        contest: Contest root.
        row: The attempt's register row.
        facts: Facts from `state_facts`; not changed.

    Returns:
        A copy of `facts` with the log facts filled in.
    """
    try:
        return _log_facts(contest, row, facts)
    except Exception:  # noqa: BLE001 — odd logs must never break an evidence view
        return facts


# ---- rendering -----------------------------------------------------------------------------------------------------
SHORT_PATHS_WIDTH = 100


def _claim_text(value: bool | None) -> str:
    return UNKNOWN if value is None else "yes" if value else "no"


def _refusals_text(facts: Facts, row: Row | None) -> str:
    """`N`, `not applicable` (Codex has no guard hooks) or `unknown`."""
    if facts.guard_refusals is not None:
        return str(facts.guard_refusals)
    return "not applicable" if row is not None and row.tool == "codex" else UNKNOWN


def _block(title: str, lines: list[str] | None) -> list[str]:
    """`title: unknown` (None), `title: none` or `title:` followed by the lines indented by two spaces (each line of
    a multi-line one)."""
    if lines is None:
        return [f"{title}: {UNKNOWN}"]
    if not lines:
        return [f"{title}: none"]
    return [f"{title}:"] + [f"  {part}" for line in lines for part in (line.splitlines() or [""])]


def render_full(facts: Facts, row: Row | None) -> str:
    """Render the full evidence as plain text: only facts, no judgement.

    Args:
        facts: Facts from `state_facts` and `log_facts` (attempt form) or from `session_facts` (session form).
        row: The attempt's register row, which gives the header `T### attempt N · <source> · <tool> · <outcome>`;
            None for a session: no header and only the facts a session has (guard refusals, repeats, calls,
            error tails, model, number of calls).

    Returns:
        The text, without a trailing newline. Every string in `facts` is already redacted; the row's fields are
        redacted here, and its free-text `message` is not printed.
    """
    lines: list[str] = []
    if row is not None:
        head = f"{row.task} attempt {row.n} \u00b7 {row.source} \u00b7 {row.tool} \u00b7 {row.outcome}"
        lines.append(guard.redact(head))
        lines.append(f"check after last edit: {facts.check_after_last_edit}")
        lines.append(f"claim without backing in the log: {_claim_text(facts.unbacked_claim)}")
        lines.append("test files edited: " + (", ".join(facts.test_edits) or "none"))
    lines.append(f"guard refusals: {_refusals_text(facts, row)}")
    lines += _block("repeated failing commands", [f"{cmd} ({n} times)" for cmd, n in facts.repeats])
    if row is not None:
        lines += _block("commands that already failed in an earlier attempt", facts.dead_end_repeats)
        lines += _block("questions added", facts.questions_added)
        lines.append(f"last check: {facts.last_check}")
        lines += _block("diffstat", [facts.diffstat] if facts.diffstat else [])
        lines += _block("guard violations", facts.violations)
    lines += _block(f"calls ({facts.n_calls if facts.n_calls is not None else UNKNOWN})", facts.calls)
    tails = [part for i, tail in enumerate(facts.error_tails) for part in (["--"] if i else []) + [tail]]
    lines += _block("error tails", tails)
    lines.append(f"model: {facts.model}")
    if row is not None:
        lines.append(f"duration: {facts.duration}")
    lines.append(f"number of calls: {UNKNOWN if facts.n_calls is None else facts.n_calls}")
    return "\n".join(lines)


def render_short(facts: Facts, row: Row) -> list[str]:
    """Render the short evidence: at most five lines, only the facts that are not empty.

    Args:
        facts: Facts from `state_facts` and `log_facts`.
        row: The attempt's register row (`tool` decides between `0` refusals and `not applicable`).

    Returns:
        In this order: `check after last edit: <value>` (always), `claim without backing in the log` (when the
        claim is unbacked), `guard refusals: N` (when above 0) or `guard refusals: not applicable` (Codex),
        `repeated failing commands: <k>` (k = number of commands that failed at least 3 times, when any) and
        `test files edited: <paths>` (when any, cut to about 100 characters).
    """
    lines = [f"check after last edit: {facts.check_after_last_edit}"]
    if facts.unbacked_claim is True:
        lines.append("claim without backing in the log")
    if row.tool == "codex" and facts.guard_refusals is None:
        lines.append("guard refusals: not applicable")
    elif facts.guard_refusals:
        lines.append(f"guard refusals: {facts.guard_refusals}")
    if facts.repeats:
        lines.append(f"repeated failing commands: {len(facts.repeats)}")
    if facts.test_edits:
        paths = ", ".join(facts.test_edits)
        cut = paths if len(paths) <= SHORT_PATHS_WIDTH else paths[:SHORT_PATHS_WIDTH] + "..."
        lines.append(f"test files edited: {cut}")
    return lines
