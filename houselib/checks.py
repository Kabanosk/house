"""Acceptance criteria run by code.

A task's `acceptance` is frozen at plan time into `.house/checks/T###.json`; every check runs the frozen
criteria, so a worker cannot loosen them by editing the task file. Only `house` writes the checks file.
"""
import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .fm import parse_frontmatter

DEFAULT_TIMEOUT = 600
TAIL_LINES = 20
_CMP_RE = re.compile(r"^\s*(?P<label>[A-Za-z_][\w .-]*?)\s*(?P<op>>=|<=|==|>|<)\s*(?P<num>-?\d+(?:\.\d+)?)\s*$")
_OPS = {">=": float.__ge__, "<=": float.__le__, "==": float.__eq__, ">": float.__gt__, "<": float.__lt__}


@dataclass
class Criterion:
    id: str
    kind: str  # "run" | "manual"
    run: str | None = None
    expect: str | None = None
    text: str | None = None
    timeout: int = DEFAULT_TIMEOUT


@dataclass
class Task:
    id: str
    path: Path
    meta: dict
    criteria: list[Criterion]
    protected: list[str]


@dataclass
class Result:
    id: str
    passes: bool | None
    exit: int | None
    output_tail: str
    ran_at: str | None
    commit: str | None


@dataclass
class CheckReport:
    task_id: str
    results: list[Result]
    mismatch: bool
    init_ok: bool
    kinds: dict[str, str]

    @property
    def green(self) -> bool:
        runs = [r for r in self.results if self.kinds[r.id] == "run"]
        return not self.mismatch and self.init_ok and all(r.passes is True for r in runs)

    @property
    def complete(self) -> bool:
        return self.green and all(r.passes is True for r in self.results)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_acceptance(raw) -> list[Criterion]:
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
    matches = sorted((house / "tasks").glob(f"{task_id}-*.md"))
    if not matches:
        raise FileNotFoundError(f"no task file {task_id}-*.md in {house / 'tasks'}")
    meta, _ = parse_frontmatter(matches[0].read_text())
    protected = [str(p) for p in (meta.get("protected") or [])]
    return Task(task_id, matches[0], meta, parse_acceptance(meta.get("acceptance")), protected)


def _checks_path(house: Path, task_id: str) -> Path:
    return house / "checks" / f"{task_id}.json"


def _load(house: Path, task_id: str) -> dict:
    path = _checks_path(house, task_id)
    if not path.exists():
        raise ValueError(f"{task_id} is not frozen; run: house check <contest> {task_id} --freeze")
    return json.loads(path.read_text())


def _save(house: Path, task_id: str, data: dict) -> Path:
    path = _checks_path(house, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return path


def _frozen_criteria(data: dict) -> list[Criterion]:
    return [Criterion(**c) for c in data["frozen"]["criteria"]]


def freeze(house: Path, task_id: str) -> Path:
    if _checks_path(house, task_id).exists():
        raise FileExistsError(f"{task_id} is already frozen; change criteria with --refreeze --reason")
    task = load_task(house, task_id)
    data = {"task": task_id, "frozen": {"criteria": [asdict(c) for c in task.criteria],
                                        "protected": {p: None for p in task.protected}},
            "frozen_at": _now(), "results": {}}
    return _save(house, task_id, data)


def _next_decision_id(text: str) -> str:
    nums = [int(n) for n in re.findall(r"^## D-(\d+)", text, re.MULTILINE)]
    return f"D-{max(nums, default=0) + 1:03d}"


def refreeze(house: Path, task_id: str, reason: str) -> str:
    data = _load(house, task_id)
    task = load_task(house, task_id)
    old = {c["id"]: c for c in data["frozen"]["criteria"]}
    new = [asdict(c) for c in task.criteria]
    keep = {c["id"] for c in new if old.get(c["id"]) == c}
    data["frozen"] = {"criteria": new,
                      "protected": {p: data["frozen"]["protected"].get(p) for p in task.protected}}
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
    return did


def expect_ok(expect: str | None, stdout: str) -> bool:
    if expect is None:
        return True
    m = _CMP_RE.match(expect)
    if m:
        label = re.escape(m["label"].strip())
        found = re.findall(rf"{label}\s*[:=]\s*(-?\d+(?:\.\d+)?)", stdout, re.IGNORECASE)
        return bool(found) and _OPS[m["op"]](float(found[-1]), float(m["num"]))
    return re.search(expect, stdout, re.MULTILINE) is not None


def _tail(text: str) -> str:
    return "\n".join(text.strip().splitlines()[-TAIL_LINES:])


def _commit(workdir: Path) -> str | None:
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=workdir, capture_output=True, text=True, check=False)
    return r.stdout.strip() if r.returncode == 0 else None


def _run_one(c: Criterion, workdir: Path, commit: str | None) -> Result:
    try:
        r = subprocess.run(c.run, shell=True, cwd=workdir, capture_output=True, text=True,
                           timeout=c.timeout, check=False)
    except subprocess.TimeoutExpired:
        return Result(c.id, False, None, f"timeout after {c.timeout} s", _now(), commit)
    ok = r.returncode == 0 and expect_ok(c.expect, r.stdout)
    return Result(c.id, ok, r.returncode, _tail(r.stdout + r.stderr), _now(), commit)


def _same_definition(task: Task, data: dict) -> bool:
    return ([asdict(c) for c in task.criteria] == data["frozen"]["criteria"]
            and sorted(task.protected) == sorted(data["frozen"]["protected"]))


def run_checks(house: Path, task_id: str, workdir: Path) -> CheckReport:
    data = _load(house, task_id)
    task = load_task(house, task_id)
    criteria = _frozen_criteria(data)
    init_ok = True
    init = house / "init.sh"
    if init.exists():
        r = subprocess.run(["bash", str(init)], cwd=workdir, capture_output=True, text=True, check=False)
        init_ok = r.returncode == 0
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
    _save(house, task_id, data)
    return CheckReport(task_id, results, not _same_definition(task, data), init_ok,
                       {c.id: c.kind for c in criteria})


def record_manual(house: Path, task_id: str, criterion_id: str, evidence: str) -> None:
    data = _load(house, task_id)
    kinds = {c["id"]: c["kind"] for c in data["frozen"]["criteria"]}
    if kinds.get(criterion_id) != "manual":
        raise ValueError(f"{criterion_id} is not a manual criterion of {task_id}")
    data["results"][criterion_id] = asdict(Result(criterion_id, True, None, f"evidence: {evidence}", _now(), None))
    _save(house, task_id, data)


def seal_protected(house: Path, task_id: str, workdir: Path) -> list[str]:
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
