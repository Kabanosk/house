"""Acceptance criteria run by code.

A task's `acceptance` is frozen at plan time into `.house/checks/T###.json`; every check runs the frozen
criteria, so a worker cannot loosen them by editing the task file. Only `house` writes the checks file.

Used by: `house check <contest> T### [--freeze|--refreeze --reason|--manual AC-n --evidence|--worktree]`,
`house check <contest> --all`, the merge gate (`merge.merge_task`) and `house night`. Roles: the coordinator
freezes criteria when a task is planned and may refreeze; coders run the check in their worktree and finish
when it prints GREEN; the judge/owner records manual criteria with evidence.

Rules an agent should know:
- Task status flow is todo → doing → review → merged → done, or blocked. A task with at least one manual
  criterion that has no recorded evidence becomes `merged`; when every criterion passes it becomes `done`.
- Criteria are `run` (shell command in the checkout, passes on exit 0 and a matching `expect`) or `manual`
  (passes only through `record_manual` with evidence). GREEN means every `run` criterion passed, init.sh
  succeeded and the criteria still equal the frozen ones; manual ones do not affect GREEN.
- Freezing is once per task (`FileExistsError` after that); changing criteria needs `refreeze` with a reason,
  which is logged in DECISIONS.md. Editing `.house/checks/` by hand is tampering and `house merge` refuses it.
- `house check` exit codes: 0 GREEN (or recorded/frozen), 1 RED, 2 usage error.
"""
import hashlib
import json
import os
import re
import signal
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .fm import parse_frontmatter

DEFAULT_TIMEOUT = 600
INIT_TIMEOUT = 300
TAIL_LINES = 20
_CMP_RE = re.compile(r"^\s*(?P<label>[A-Za-z_][\w .-]*?)\s*(?P<op>>=|<=|==|>|<)\s*(?P<num>-?\d+(?:\.\d+)?)\s*$")
_OPS = {">=": float.__ge__, "<=": float.__le__, "==": float.__eq__, ">": float.__gt__, "<": float.__lt__}


@dataclass
class Criterion:
    """One acceptance criterion as frozen in `.house/checks/T###.json`.

    Attributes:
        id: Criterion id, e.g. "AC-1".
        kind: "run" (executed shell command) or "manual" (needs recorded evidence).
        run: Shell command, run with the checkout as cwd (kind "run" only).
        expect: Optional extra condition on the output: a regex, or a comparison such as "coverage >= 80"
            (kind "run" only). None means exit code 0 is enough.
        text: Description a human verifies (kind "manual" only).
        timeout: Seconds before the command is killed (default 600).
    """
    id: str
    kind: str  # "run" | "manual"
    run: str | None = None
    expect: str | None = None
    text: str | None = None
    timeout: int = DEFAULT_TIMEOUT


@dataclass
class Task:
    """A task file loaded by `load_task`.

    Attributes:
        id: Task id, e.g. "T001".
        path: The task file `.house/tasks/T###-*.md`.
        meta: Parsed frontmatter (status, branch, agent, depends_on, acceptance, protected, ...).
        criteria: The criteria parsed from `acceptance` (as currently written in the file, not the frozen ones).
        protected: Paths whose content is pinned by hash while the task is in progress.
    """
    id: str
    path: Path
    meta: dict
    criteria: list[Criterion]
    protected: list[str]


@dataclass
class Result:
    """Outcome of one criterion.

    Attributes:
        id: Criterion id.
        passes: True, False, or None for a manual criterion without evidence yet.
        exit: Exit code of the command; None for manual criteria and timeouts.
        output_tail: Last TAIL_LINES lines of output, or the evidence text of a manual criterion.
        ran_at: ISO timestamp of the run or of the recorded evidence; None if never.
        commit: HEAD of the checkout when the command ran; None if unknown.
    """
    id: str
    passes: bool | None
    exit: int | None
    output_tail: str
    ran_at: str | None
    commit: str | None


@dataclass
class CheckReport:
    """Outcome of `run_checks` for one task.

    Attributes:
        task_id: Task id.
        results: One `Result` per frozen criterion, in order.
        mismatch: True when the task file's criteria or protected list differ from the frozen ones.
        init_ok: False when `.house/init.sh` exited non-zero or timed out.
        kinds: Criterion id → "run" or "manual".
    """
    task_id: str
    results: list[Result]
    mismatch: bool
    init_ok: bool
    kinds: dict[str, str]

    @property
    def green(self) -> bool:
        """Return True when every `run` criterion passed, init.sh succeeded and nothing changed since freeze."""
        runs = [r for r in self.results if self.kinds[r.id] == "run"]
        return not self.mismatch and self.init_ok and all(r.passes is True for r in runs)

    @property
    def complete(self) -> bool:
        """Return True when the report is green and every manual criterion has recorded evidence too."""
        return self.green and all(r.passes is True for r in self.results)


def _now() -> str:
    """Return the current local time as an ISO string with UTC offset, to the second."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_acceptance(raw) -> list[Criterion]:
    """Turn the `acceptance` frontmatter value into criteria.

    Args:
        raw: The parsed list, or None. Each item is either a mapping with `run` (plus optional `id`, `expect`,
            `timeout` in seconds) or `manual` (description), or a plain string, which becomes a manual criterion.
            Missing ids become `AC-<position>`.

    Returns:
        The criteria in order.

    Raises:
        ValueError: A mapping item has neither `run` nor `manual`.
    """
    out = []
    for n, item in enumerate(raw or [], start=1):
        if isinstance(item, dict):
            cid = str(item.get("id") or f"AC-{n}")
            if item.get("run"):
                out.append(Criterion(cid, "run", run=str(item["run"]),
                                     expect=None if item.get("expect") is None else str(item["expect"]),
                                     timeout=int(item.get("timeout") or DEFAULT_TIMEOUT)))
            elif item.get("manual"):
                out.append(Criterion(cid, "manual", text=str(item["manual"])))
            else:
                raise ValueError(f"acceptance item {cid} needs `run` or `manual`")
        else:
            out.append(Criterion(f"AC-{n}", "manual", text=str(item)))
    return out


def load_task(house: Path, task_id: str) -> Task:
    """Load a task file by id.

    Args:
        house: Path of the contest's `.house/` directory.
        task_id: Task id, e.g. "T001"; the first file matching `.house/tasks/T001-*.md` is used.

    Returns:
        The `Task`.

    Raises:
        FileNotFoundError: No matching task file.
        ValueError: The task file has no valid frontmatter, or an acceptance item is invalid.
    """
    matches = sorted((house / "tasks").glob(f"{task_id}-*.md"))
    if not matches:
        raise FileNotFoundError(f"no task file {task_id}-*.md in {house / 'tasks'}")
    meta, _ = parse_frontmatter(matches[0].read_text())
    protected = [str(p) for p in (meta.get("protected") or [])]
    return Task(task_id, matches[0], meta, parse_acceptance(meta.get("acceptance")), protected)


def _checks_path(house: Path, task_id: str) -> Path:
    """Return the path of the frozen-criteria file `.house/checks/<task_id>.json`."""
    return house / "checks" / f"{task_id}.json"


def _load(house: Path, task_id: str) -> dict:
    """Read the frozen-criteria file; raise ValueError telling to freeze when it does not exist."""
    path = _checks_path(house, task_id)
    if not path.exists():
        raise ValueError(f"{task_id} is not frozen; run: house check <contest> {task_id} --freeze")
    return json.loads(path.read_text())


def _save(house: Path, task_id: str, data: dict) -> Path:
    """Write the frozen-criteria file (creating `.house/checks/`) and return its path."""
    path = _checks_path(house, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return path


def _frozen_criteria(data: dict) -> list[Criterion]:
    """Rebuild the `Criterion` list from the checks-file data."""
    return [Criterion(**c) for c in data["frozen"]["criteria"]]


def _hash_if_exists(path: Path) -> str | None:
    """Return the SHA-256 of the file, or None when it is not an existing regular file."""
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def freeze(house: Path, task_id: str, workdir: Path | None = None) -> Path:
    """Freeze the task's acceptance criteria into `.house/checks/<task_id>.json` and commit that file.

    Args:
        house: Path of the contest's `.house/` directory.
        task_id: Task id, e.g. "T001".
        workdir: Checkout in which existing `protected` files are hashed now; defaults to the contest checkout.
            Protected files that do not exist yet are hashed when the task is merged.

    Returns:
        The path of the checks file.

    Raises:
        FileExistsError: The task is already frozen (use `refreeze`).
        ValueError: The task has no acceptance criteria.
        FileNotFoundError: The task file does not exist.
    """
    if _checks_path(house, task_id).exists():
        raise FileExistsError(f"{task_id} is already frozen; change criteria with --refreeze --reason")
    task = load_task(house, task_id)
    if not task.criteria:
        raise ValueError(f"{task_id} has no acceptance criteria; add at least one `run` or `manual` item")
    data = {"task": task_id, "frozen": {"criteria": [asdict(c) for c in task.criteria],
                                        "protected": {p: _hash_if_exists((workdir or house.parent) / p)
                                                      for p in task.protected}},
            "frozen_at": _now(), "results": {}}
    path = _save(house, task_id, data)
    _git_commit(house, [path], f"chore(house): freeze {task_id}")
    return path


def _git_commit(house: Path, paths: list[Path], message: str) -> None:
    """Commit the given files at once (no-op outside a git repo)."""
    repo = house.parent
    if subprocess.run(["git", "rev-parse", "--git-dir"], cwd=repo, capture_output=True, check=False).returncode:
        return
    rels = [str(p.relative_to(repo)) for p in paths]
    subprocess.run(["git", "add", "--", *rels], cwd=repo, capture_output=True, check=False)
    subprocess.run(["git", "commit", "-q", "-m", message, "--", *rels], cwd=repo, capture_output=True, check=False)


def _next_decision_id(text: str) -> str:
    """Return the next free decision id (`D-###`) found in the text of DECISIONS.md."""
    nums = [int(n) for n in re.findall(r"^## D-(\d+)", text, re.MULTILINE)]
    return f"D-{max(nums, default=0) + 1:03d}"


def refreeze(house: Path, task_id: str, reason: str) -> str:
    """Replace the frozen criteria with the task file's current ones and log the change in DECISIONS.md.

    Args:
        house: Path of the contest's `.house/` directory.
        task_id: Task id; it must be frozen already.
        reason: Why the criteria change; written to the decision entry.

    Returns:
        The id of the new decision entry, e.g. "D-004". Stored results of criteria that did not change are kept,
        the others are dropped. Both files are committed.

    Raises:
        ValueError: The task is not frozen yet.
    """
    data = _load(house, task_id)
    task = load_task(house, task_id)
    old = {c["id"]: c for c in data["frozen"]["criteria"]}
    new = [asdict(c) for c in task.criteria]
    keep = {c["id"] for c in new if old.get(c["id"]) == c}
    data["frozen"] = {"criteria": new,
                      "protected": {p: data["frozen"]["protected"].get(p) or _hash_if_exists(house.parent / p)
                                    for p in task.protected}}
    data["frozen_at"] = _now()
    data["results"] = {k: v for k, v in data["results"].items() if k in keep}
    _save(house, task_id, data)
    decisions = house / "DECISIONS.md"
    text = decisions.read_text() if decisions.exists() else "# Dziennik decyzji\n"
    did = _next_decision_id(text)
    entry = (f"\n## {did} Zmiana kryteriów {task_id}\n\n"
             f"- **Decyzja:** nowe kryteria akceptacji {task_id}: "
             + ", ".join(f"{c['id']} ({c['kind']})" for c in new) + "\n"
             f"- **Dlaczego:** {reason}\n- **Kto:** coordinator\n"
             f"- **Data:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    decisions.write_text(text.rstrip("\n") + "\n" + entry)
    _git_commit(house, [_checks_path(house, task_id), decisions], f"chore(house): refreeze {task_id} ({did})")
    return did


def expect_ok(expect: str | None, stdout: str) -> bool:
    """Judge a criterion's output against its `expect` condition.

    Args:
        expect: None (always passes), a comparison like "coverage >= 80" (the last number printed as
            `coverage: 85` or `coverage=85` is compared; ops are >= <= == > <), or a regex searched per line.
        stdout: Combined output of the command.

    Returns:
        True when the condition holds.

    Raises:
        re.error: `expect` is not a valid regex.
    """
    if expect is None:
        return True
    m = _CMP_RE.match(expect)
    if m:
        label = re.escape(m["label"].strip())
        found = re.findall(rf"{label}\s*[:=]\s*(-?\d+(?:\.\d+)?)", stdout, re.IGNORECASE)
        return bool(found) and _OPS[m["op"]](float(found[-1]), float(m["num"]))
    return re.search(expect, stdout, re.MULTILINE) is not None


def _tail(text: str) -> str:
    """Return the last TAIL_LINES lines of the text."""
    return "\n".join(text.strip().splitlines()[-TAIL_LINES:])


def _commit(workdir: Path) -> str | None:
    """Return HEAD of the checkout, or None when it is not a git repo."""
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=workdir, capture_output=True, text=True, check=False)
    return r.stdout.strip() if r.returncode == 0 else None


def _env() -> dict[str, str]:
    """Return the environment for check commands (bytecode writing disabled)."""
    # no .pyc: a file rewritten within the same second with the same size would otherwise run stale bytecode
    return {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def _kill_group(pgid: int) -> None:
    """SIGKILL a process group, ignoring one that is already gone."""
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _start(cmd: list[str] | str, workdir: Path, out) -> subprocess.Popen:
    """Own process group, output to a file (not a pipe): background children can neither hang us nor survive."""
    return subprocess.Popen(cmd, shell=isinstance(cmd, str), cwd=workdir, env=_env(), stdout=out,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)


def _run_cmd(cmd: list[str] | str, workdir: Path, timeout: int, keep_group: bool = False):
    """Run a command in its own process group and capture its output.

    Args:
        cmd: Argument list, or a string run through the shell.
        workdir: Working directory.
        timeout: Seconds before the group is killed.
        keep_group: Leave background children running after the command exits (used for init.sh).

    Returns:
        `(exit code or None on timeout, output, process group id)`.
    """
    with tempfile.TemporaryFile(mode="w+") as out:
        proc = _start(cmd, workdir, out)
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_group(proc.pid)
            proc.wait()
            code = None
        if not keep_group:
            _kill_group(proc.pid)
        out.seek(0)
        return code, out.read(), proc.pid


def _run_one(c: Criterion, workdir: Path, commit: str | None) -> Result:
    """Run one `run` criterion in workdir and return its `Result` (timeout and bad regex count as failures)."""
    code, output, _ = _run_cmd(c.run, workdir, c.timeout)
    if code is None:
        return Result(c.id, False, None, f"timeout after {c.timeout} s\n" + _tail(output), _now(), commit)
    try:
        ok = code == 0 and expect_ok(c.expect, output)
    except re.error as e:
        return Result(c.id, False, code, f"invalid expect pattern {c.expect!r}: {e}", _now(), commit)
    return Result(c.id, ok, code, _tail(output), _now(), commit)


def _same_definition(task: Task, data: dict) -> bool:
    """Return True when the task file's criteria and protected list equal the frozen ones."""
    return ([asdict(c) for c in task.criteria] == data["frozen"]["criteria"]
            and sorted(task.protected) == sorted(data["frozen"]["protected"]))


def is_frozen(house: Path, task_id: str) -> bool:
    """Tell whether the task has frozen criteria.

    Args:
        house: Path of the contest's `.house/` directory.
        task_id: Task id, e.g. "T001".

    Returns:
        True when `.house/checks/<task_id>.json` exists.
    """
    return _checks_path(house, task_id).exists()


def run_checks(house: Path, task_id: str, workdir: Path, record: bool = True) -> CheckReport:
    """Run the task's frozen criteria in a checkout.

    Args:
        house: Path of the contest's `.house/` directory (holds the frozen criteria and `init.sh`).
        task_id: Task id; it must be frozen.
        workdir: Checkout to run in: the contest checkout or a task worktree.
        record: Store the results in the checks file (default). Use False for read-only re-checks of other tasks.

    Returns:
        A `CheckReport`. If `.house/init.sh` exists it runs first (limit INIT_TIMEOUT, 300 s) and
        anything it starts in the background is killed after the criteria ran. Manual criteria reuse their
        recorded evidence.

    Raises:
        ValueError: The task is not frozen.
        FileNotFoundError: The task file does not exist.
    """
    data = _load(house, task_id)
    task = load_task(house, task_id)
    criteria = _frozen_criteria(data)
    init_ok, init_group = True, None
    init = house / "init.sh"
    if init.exists():  # may start the app in the background; it lives until the criteria are done
        code, _, init_group = _run_cmd(["bash", str(init)], workdir, INIT_TIMEOUT, keep_group=True)
        init_ok = code == 0
    try:
        commit = _commit(workdir)
        results = []
        for c in criteria:
            if c.kind == "run":
                res = _run_one(c, workdir, commit)
                data["results"][c.id] = asdict(res)
            else:
                stored = data["results"].get(c.id) or {}
                res = Result(c.id, stored.get("passes"), None, stored.get("output_tail", ""),
                             stored.get("ran_at"), stored.get("commit"))
            results.append(res)
    finally:
        if init_group is not None:
            _kill_group(init_group)
    if record:
        _save(house, task_id, data)
    return CheckReport(task_id, results, not _same_definition(task, data), init_ok,
                       {c.id: c.kind for c in criteria})


def record_manual(house: Path, task_id: str, criterion_id: str, evidence: str) -> None:
    """Record evidence that a manual criterion passed.

    Args:
        house: Path of the contest's `.house/` directory.
        task_id: Task id; it must be frozen.
        criterion_id: Id of a frozen manual criterion, e.g. "AC-2".
        evidence: Free text saying how it was verified; stored with the result.

    Raises:
        ValueError: The task is not frozen, or the criterion is not a manual one of this task.
    """
    data = _load(house, task_id)
    kinds = {c["id"]: c["kind"] for c in data["frozen"]["criteria"]}
    if kinds.get(criterion_id) != "manual":
        raise ValueError(f"{criterion_id} is not a manual criterion of {task_id}")
    data["results"][criterion_id] = asdict(Result(criterion_id, True, None, f"evidence: {evidence}", _now(), None))
    _save(house, task_id, data)


def seal_protected(house: Path, task_id: str, workdir: Path) -> list[str]:
    """Hash protected files that did not exist when the task was frozen (called after the merge).

    Args:
        house: Path of the contest's `.house/` directory.
        task_id: Task id; it must be frozen.
        workdir: Checkout (the merged main line) in which to look for the files.

    Returns:
        The protected paths that are still missing; their hashes stay unset.

    Raises:
        ValueError: The task is not frozen.
    """
    data = _load(house, task_id)
    missing = []
    for rel, sha in data["frozen"]["protected"].items():
        if sha is None:
            path = workdir / rel
            if path.is_file():
                data["frozen"]["protected"][rel] = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                missing.append(rel)
    _save(house, task_id, data)
    return missing
