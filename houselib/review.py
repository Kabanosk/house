"""`house review`: an independent code review by a model family other than the author's (spec §9).

Codex reviews Claude-written work and Claude reviews Codex-written work. Codex reviews are capped per day
(`limits.codex_reviews_per_day`, default 4, counted in `state.json`); past the cap, or when Codex is missing or does
not respond, a fresh Claude session reviews and the report says the independence is weaker. The brief is blind: goal,
acceptance and the exact diff, never the author's report. Rules borrowed from the vendored `second-opinion` skill:
the reader is told it is the only reviewer (no trigger phrases, no delegation), the reviewer runs read-only, Codex is
liveness-checked with a nonce first, and an output that is not a review (empty, apology, no `VERDICT:`) is a failed
review, never "no findings".

Reports: `.house/reports/review-<id>.md` with machine-readable lines (`- **Head:**`, `- **Reviewer:**`,
`- **Blockers:**`) that `house merge` reads for tasks marked `review: always`.
"""
import json
import os
import re
import secrets
import signal
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from . import checks
from . import spec as specmod
from .fm import parse_frontmatter

DEFAULT_BUDGET = 4
DEFAULT_MINUTES = 20
NONCE_SECONDS = 120
CLAUDE_TOOLS = "Read,Grep,Glob,Bash(git diff:*),Bash(git log:*),Bash(git show:*),Bash(git status:*)"
FINDING_RE = re.compile(r"^\W*(?:\d+[.)]\s*)?\W*(BLOCKER|MAJOR|MINOR|NIT)\b", re.IGNORECASE | re.MULTILINE)
NOT_A_REVIEW_RE = re.compile(r"\b(unable to|could not|couldn't|cannot|can't|not logged in|authentication|"
                             r"permission denied|delegat)", re.IGNORECASE)
VERDICT_RE = re.compile(r"^\W*VERDICT\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)


@dataclass
class Findings:
    """Counts of findings by severity plus the reviewer's one-line verdict."""

    blockers: int = 0
    majors: int = 0
    minors: int = 0
    nits: int = 0
    verdict: str = ""


@dataclass
class ReviewResult:
    """Outcome of one review run.

    Attributes:
        ok: True when a real review came back (it may still contain blockers).
        reviewer: "codex" or "claude".
        note: Why this reviewer was chosen (budget, author, liveness), shown in the report.
        why: Reason for a failed review ("" when ok).
        findings: Parsed counts and verdict.
        report: Path of the written report.
        head: Commit reviewed.
    """

    ok: bool
    reviewer: str
    note: str = ""
    why: str = ""
    findings: Findings = field(default_factory=Findings)
    report: Path | None = None
    head: str = ""


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False).stdout.strip()


def brief(goal: str, acceptance: list[str], base: str, head: str) -> str:
    """Write the review request text (blind: no author report, no conclusions).

    Args:
        goal: What the change is for (task objective or milestone goal).
        acceptance: Acceptance criteria lines, e.g. ["AC-1: pytest -q"].
        base: Commit the change starts from.
        head: Commit to review.

    Returns:
        The full prompt for the reviewer.
    """
    criteria = "\n".join(f"- {a}" for a in acceptance) or "- (none given)"
    return (
        "This is a code review request. You are the only reviewer: review directly; do not run any skill, workflow, "
        "agent or CLI tool other than git and file reading (no claude, no codex, no sub-agents).\n\n"
        f"Goal of the change:\n{goal.strip()}\n\nAcceptance criteria:\n{criteria}\n\n"
        f"Scope: review only `git diff {base}...{head}`. Open other files only to verify a specific finding.\n"
        "Look for real problems first: correctness, data leaks between train and test, scoring or submission "
        "mistakes, security, missing edge cases. Style comes last.\n\n"
        "Output format, one finding per line:\n"
        "BLOCKER|MAJOR|MINOR|NIT <file>:<line> — <reason>\n"
        "then a last line `VERDICT: <one line>`. If you find nothing significant, say so plainly and still give the "
        "VERDICT line; do not invent issues.\n"
    )


def parse(text: str) -> Findings:
    """Count findings by severity and extract the verdict line.

    Args:
        text: The reviewer's raw answer.

    Returns:
        Findings with counts; `verdict` is "" when there is no `VERDICT:` line.
    """
    f = Findings()
    names = {"BLOCKER": "blockers", "MAJOR": "majors", "MINOR": "minors", "NIT": "nits"}
    for sev in FINDING_RE.findall(text):
        attr = names[sev.upper()]
        setattr(f, attr, getattr(f, attr) + 1)
    verdicts = VERDICT_RE.findall(text)
    f.verdict = verdicts[-1].strip() if verdicts else ""
    return f


def _state(contest: Path) -> dict:
    try:
        return json.loads((contest / ".house/state.json").read_text())
    except (OSError, ValueError):
        return {}


def _save_state(contest: Path, data: dict) -> None:
    (contest / ".house/state.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _launch(cmd: list[str], cwd: Path, stdin_text: str | None, timeout: float) -> tuple[int | None, str]:
    """Run a reviewer in its own process group; (exit code or None on timeout, stdout+stderr)."""
    with tempfile.TemporaryFile(mode="w+") as out, tempfile.TemporaryFile(mode="w+") as inp:
        inp.write(stdin_text or "")
        inp.seek(0)
        try:
            proc = subprocess.Popen(cmd, cwd=cwd, stdin=inp, stdout=out, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        except OSError as e:  # binary missing although tools.json says it is installed
            return 127, f"could not start {cmd[0]}: {e}"
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            code = None
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.wait()
        out.seek(0)
        return code, out.read()


def _codex_alive(launcher) -> bool:
    """Ask Codex for a nonce it can only know by running a command in its sandbox."""
    with tempfile.TemporaryDirectory() as d:
        nonce = secrets.token_hex(8)
        (Path(d) / "nonce").write_text(nonce)
        out = Path(d) / "out"
        prompt = f"Run `cat {d}/nonce` and reply with only its output. Do not run any skill or other agent.\n"
        launcher(["codex", "exec", "--sandbox", "read-only", "--skip-git-repo-check", "-C", d, "-o", str(out), "-"],
                 Path(d), prompt, NONCE_SECONDS)
        return out.exists() and nonce in out.read_text()


def pick_reviewer(contest: Path, author: str, tools: dict, today: date | None = None) -> tuple[str, str]:
    """Choose the reviewer for a change.

    Args:
        contest: Contest checkout (reads `limits.codex_reviews_per_day` and `state.json`).
        author: Agent that wrote the change ("coder-codex" means Codex wrote it).
        tools: `tools.json` contents (`codex` availability).
        today: Date used for the daily budget (default: today).

    Returns:
        (reviewer kind "codex" or "claude", a note explaining the choice).
    """
    if author == "coder-codex":
        return "claude", "author is Codex, reviewer is Claude"
    if not tools.get("codex"):
        return "claude", "Codex not available; same model family as the author (weaker independence)"
    configured = specmod.get(specmod.load_spec(contest / ".house"), "limits.codex_reviews_per_day")
    budget = DEFAULT_BUDGET if configured is None else int(configured)
    used = _state(contest).get("codex_reviews", {}).get((today or date.today()).isoformat(), 0)
    if used >= budget:
        return "claude", f"daily Codex review budget used ({used}/{budget}); same model family (weaker independence)"
    return "codex", f"Codex reviews Claude's work ({used + 1}/{budget} today)"


def run_review(kind: str, text: str, repo: Path, minutes: float, launcher=None) -> tuple[bool, str, str]:
    """Run one review.

    Args:
        kind: "codex" or "claude".
        text: The brief.
        repo: Checkout the reviewer reads (read-only).
        minutes: Time limit; the process group is killed after it.
        launcher: Injected runner `(cmd, cwd, stdin_text, timeout) -> (code | None, output)` for tests.

    Returns:
        (ok, review text, why it failed or "").
    """
    launcher = launcher or _launch
    timeout = minutes * 60
    if kind == "codex":
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "review.md"
            code, _ = launcher(["codex", "exec", "--sandbox", "read-only", "--skip-git-repo-check", "-C", str(repo),
                                "-o", str(out), "-"], repo, text, timeout)
            answer = out.read_text() if out.exists() else ""
    else:
        code, answer = launcher(["claude", "-p", "--model", "opus", "--allowedTools", CLAUDE_TOOLS,
                                 "--permission-prompts", "none", text], repo, None, timeout)
    if code is None:
        return False, answer, f"{kind} review timed out after {minutes:g} min"
    if code != 0:
        return False, answer, f"{kind} exited with {code}: {answer.strip()[-200:]!r}"
    verdicts = VERDICT_RE.findall(answer)
    if not answer.strip() or not verdicts:
        return False, answer, f"{kind} returned no review (empty or no VERDICT line): {answer.strip()[:200]!r}"
    if NOT_A_REVIEW_RE.search(verdicts[-1]) and not FINDING_RE.search(answer):
        return False, answer, f"{kind} answer is not a review: {verdicts[-1].strip()[:200]!r}"
    return True, answer, ""


def _write_report(contest: Path, rid: str, res: ReviewResult, base: str, text: str) -> Path:
    path = contest / ".house/reports" / f"review-{rid}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    f = res.findings
    path.write_text(
        f"# Review {rid}\n\n- **Reviewer:** {res.reviewer}\n- **Note:** {res.note}\n- **Base:** {base}\n"
        f"- **Head:** {res.head}\n- **Status:** {'ok' if res.ok else 'failed: ' + res.why}\n"
        f"- **Blockers:** {f.blockers}\n- **Majors:** {f.majors}\n- **Minors:** {f.minors}\n- **Nits:** {f.nits}\n"
        f"- **Verdict:** {f.verdict or '—'}\n- **Date:** {datetime.now():%Y-%m-%d %H:%M}\n\n"
        "Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.\n\n"
        f"## Review text\n\n{text.strip() or '(empty)'}\n")
    return path


def _review(contest: Path, rid: str, author: str, goal: str, acceptance: list[str], base: str, head: str,
            repo: Path, launcher=None) -> ReviewResult:
    try:
        tools = json.loads((contest / ".house/tools.json").read_text())
    except (OSError, ValueError):
        tools = {}
    kind, note = pick_reviewer(contest, author, tools)
    if kind == "codex" and not _codex_alive(launcher or _launch):
        kind, note = "claude", "Codex failed the nonce liveness check; same model family (weaker independence)"
    minutes = float(specmod.get(specmod.load_spec(contest / ".house"), "review.minutes") or DEFAULT_MINUTES)
    ok, text, why = run_review(kind, brief(goal, acceptance, base, head), repo, minutes, launcher)
    if kind == "codex":
        state = _state(contest)
        counts = state.setdefault("codex_reviews", {})
        counts[date.today().isoformat()] = counts.get(date.today().isoformat(), 0) + 1
        _save_state(contest, state)
    res = ReviewResult(ok, kind, note, why, parse(text) if ok else Findings(), head=head)
    res.report = _write_report(contest, rid, res, base, text)
    return res


def review_task(contest: Path, task_id: str, launcher=None) -> ReviewResult:
    """Review a task branch against the contest's main line.

    Args:
        contest: Contest checkout.
        task_id: Task id, e.g. "T003"; its `branch` must exist.
        launcher: Injected runner for tests.

    Returns:
        The ReviewResult; the report is `.house/reports/review-<task_id>.md`.
    """
    task = checks.load_task(contest / ".house", task_id)
    branch = str(task.meta.get("branch") or "")
    head = _git(contest, "rev-parse", branch)
    base = _git(contest, "merge-base", "HEAD", branch)
    body = parse_frontmatter(task.path.read_text())[1]
    objective = body.split("## Objective", 1)[-1].split("\n## ", 1)[0] if "## Objective" in body else body
    goal = f"{task.meta.get('title', task_id)}\n{objective.strip()}"
    acceptance = [f"{c.id}: {c.run or c.text}" for c in task.criteria]
    wt = contest / ".worktrees" / branch.removeprefix("task/")
    author = str(task.meta.get("agent") or task.meta.get("owner") or "coder")
    return _review(contest, task_id, author, goal, acceptance, base, head, wt if wt.is_dir() else contest, launcher)


def review_since(contest: Path, ref: str, launcher=None) -> ReviewResult:
    """Review everything merged on the main line since `ref` (milestone review).

    Args:
        contest: Contest checkout.
        ref: Commit to start from, or "last" for the head of the previous successful milestone review.
        launcher: Injected runner for tests.

    Returns:
        The ReviewResult; on success `state.json` `last_review_ref` moves to the reviewed head.
    """
    if ref == "last":
        ref = _state(contest).get("last_review_ref") or _git(contest, "rev-list", "--max-parents=0", "HEAD")
    head = _git(contest, "rev-parse", "HEAD")
    goal = str(specmod.get(specmod.load_spec(contest / ".house"), "goal") or "Contest work since the last review")
    rid = f"since-{ref[:7]}-{datetime.now():%Y%m%d-%H%M}"
    res = _review(contest, rid, "coder", goal, [], ref, head, contest, launcher)
    if res.ok:
        state = _state(contest)
        state["last_review_ref"] = head
        _save_state(contest, state)
    return res


def report_for(contest: Path, task_id: str) -> dict[str, str]:
    """Read the machine-readable lines of `reports/review-<task_id>.md` ({} when there is none)."""
    path = contest / ".house/reports" / f"review-{task_id}.md"
    if not path.exists():
        return {}
    header = path.read_text().split("\n## Review text", 1)[0]  # never the reviewer's own text
    return {k.lower(): v.strip() for k, v in re.findall(r"^- \*\*(\w+):\*\* (.*)$", header, re.MULTILINE)}
