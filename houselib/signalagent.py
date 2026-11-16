"""S7: a coding agent started from Signal (`@claude …` in an allowed chat), safely.

Reads only the message store (after `signalstore.pull`), never `receive`. Design and threat model: spec
`docs/specs/2026-11-16-signal-groups-design.md` §15 and `docs/research/2026-11-16-signal-claude-agent.md`.
Rules a reader of this module must keep:

* The trigger is the literal text `@claude` in the message as sent (`raw`, where mentions are still U+FFFC),
  never the quoted text, an edit, a reaction or anything house itself sent (`dir: out`, text starting with 🤖).
* Authorisation uses the sender's ACI (`sender.uuid`), never the display name; owner commands only as `dir: own`
  in Note to Self.
* The agent runs sandboxed in its own worktree and never commits, pushes or sends; house does that afterwards,
  after filtering what goes back to the chat.
"""
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import unicodedata
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

TRIGGER_RE = re.compile(r"(?<![\w@.])@claude\b", re.IGNORECASE)
ALIAS_HINT_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9-]*)\s*:\s*(.+)$", re.DOTALL)
COMMANDS = {"/status": "status", "/lb": "lb", "/stop": "stop"}
SELF = "self"


@dataclass
class Request:
    """A request to start the agent (`@claude …`).

    Attributes:
        key: Store key `chat:author:ts` (idempotency key of the job).
        chat: "self" or the group id. chat_alias: alias at the time of receiving.
        ts: Signal timestamp of the message (quoted by every reply).
        author: Sender ACI (uuid), else number; the only identity used for decisions.
        author_name: Display name, for messages only.
        own: Sent by the owner's own account (sync message).
        text: The request without `@claude` and without an alias hint.
        full_text: The request without `@claude` (alias hint kept).
        alias_hint: `fr` for `@claude fr: …`, lower-case, else None; routing decides whether it names a contest.
        quote_ts: Timestamp of the quoted message, if any.
        received_ms: `serverReceivedTimestamp` of the envelope (age check), None when unknown.
    """

    key: str
    chat: str
    chat_alias: str
    ts: int
    author: str
    author_name: str
    own: bool
    text: str
    full_text: str
    alias_hint: str | None
    quote_ts: int | None
    received_ms: int | None


@dataclass
class Command:
    """A chat command handled by house without a model: status, lb, stop, agent-off, agent-on."""

    name: str
    key: str
    chat: str
    author: str
    own: bool
    quote_ts: int | None


def _sent_text(event: dict) -> str:
    """The text exactly as sent (mentions still U+FFFC), from `raw`; the stored text when there is no envelope."""
    env = (event.get("raw") or {}).get("envelope") or {}
    msg = ((env.get("syncMessage") or {}).get("sentMessage") or env.get("dataMessage") or {})
    text = msg.get("message") if isinstance(msg, dict) else None
    return unicodedata.normalize("NFKC", str(text if text is not None else event.get("text") or ""))


def parse(event: dict) -> "Request | Command | None":
    """Classify one store event as a request, a command, or nothing.

    Args:
        event: A store event (`signalstore.iter_events`).

    Returns:
        `Request` for `@claude …`, `Command` for `/status`, `/lb`, `/stop` (any chat) and `/agent off|on` (owner,
        Note to Self only), None for everything else (house's own messages, edits, reactions, quotes, chatter).
    """
    if event.get("kind") != "message" or event.get("dir") not in ("in", "own"):
        return None
    sent = _sent_text(event)
    if sent.lstrip().startswith("🤖"):
        return None
    sender = event.get("sender") or {}
    author = str(sender.get("uuid") or sender.get("number") or "?")
    own = event.get("dir") == "own"
    chat = str(event.get("chat"))
    quote_ts = (event.get("quote") or {}).get("ts")
    words = sent.strip().lower().split()
    if words and words[0] in COMMANDS:
        return Command(COMMANDS[words[0]], str(event["key"]), chat, author, own, quote_ts)
    if words[:1] == ["/agent"] and words[1:2] in (["off"], ["on"]):
        if own and chat == SELF:
            return Command(f"agent-{words[1]}", str(event["key"]), chat, author, own, quote_ts)
        return None
    if not TRIGGER_RE.search(sent):
        return None
    shown = str(event.get("text") or sent)
    full = " ".join(TRIGGER_RE.sub(" ", shown, count=1).split()).strip(" ,:")
    hint = ALIAS_HINT_RE.match(full)
    env = (event.get("raw") or {}).get("envelope") or {}
    received = env.get("serverReceivedTimestamp")
    return Request(
        key=str(event["key"]), chat=chat, chat_alias=str(event.get("chat_alias") or chat), ts=int(event["ts"]),
        author=author, author_name=str(sender.get("name") or author), own=own,
        text=hint[2].strip() if hint else full, full_text=full, alias_hint=hint[1].lower() if hint else None,
        quote_ts=int(quote_ts) if quote_ts is not None else None,
        received_ms=int(received) if str(received or "").isdigit() else None)


# --- routing ----------------------------------------------------------------------------------------------------

@dataclass
class Route:
    """Where a request runs.

    Attributes:
        repo: Repository to work in (a contest checkout or the house repo); None when house must ask first.
        contest: The contest, when the repo is one.
        text: The request text to give the agent (alias hint removed only when it named a contest).
        asks: (contest, message) pairs to send when the contest is ambiguous; nothing starts then.
    """

    repo: Path | None
    contest: Path | None
    text: str
    asks: list[tuple[Path, str]] = field(default_factory=list)


def route(req: Request, chats: dict, out_events: dict[int, dict]) -> Route:
    """Pick the repo for a request (spec §15 F5).

    Order: a quote of a house message tagged with a contest of this chat → that contest; an alias hint naming a
    contest of this chat → it; a chat with exactly one contest → it; several → ask in each contest's name (a quote
    on one of those questions decides later); Note to Self and `signal.toml` groups → the house repo.

    Args:
        req: The parsed request.
        chats: Allowed chats (`signalchats.allowed`), giving each group's contests.
        out_events: House's sent messages by Signal timestamp (their `meta.contest` names the contest).
    """
    from . import HOUSE_ROOT, notify
    chat = chats.get(req.chat)
    contests = list(chat.contests) if chat is not None else []
    if not contests:
        return Route(HOUSE_ROOT, None, req.full_text)
    by_name = {notify.contest_name(c): c for c in contests}
    quoted = ((out_events.get(req.quote_ts) or {}).get("meta") or {}).get("contest") if req.quote_ts else None
    if quoted in by_name:
        return Route(by_name[quoted], by_name[quoted], req.full_text)
    if req.alias_hint:
        named = [c for c in contests if notify.alias(c).lower() == req.alias_hint]
        if named:
            return Route(named[0], named[0], req.text)
    if len(contests) == 1:
        return Route(contests[0], contests[0], req.full_text)
    short = " ".join(req.full_text.split())[:60]
    return Route(None, None, req.full_text,
                 [(c, f"odpowiedz cytatem na tę wiadomość, jeśli zlecenie „{short}” jest do {notify.alias(c)}")
                  for c in contests])


# --- jobs ---------------------------------------------------------------------------------------------------------

REQUIRE_SNAPSHOT = True      # Q1: only members present at `allow` (or refreshed by `agent members`) start jobs
QUEUE_LIMIT = 5              # per chat
BREAKER = 3                  # failures in a row that switch S7 off
MAX_AGE = timedelta(hours=2)  # older requests wait for the owner's 👍
ACTIVE = ("waiting-approval", "queued", "running")


@dataclass
class Job:
    """One request turned into work. Persisted in `signal/agent/jobs.json`; every change is audited in
    `jobs.jsonl` (with a hash of the text instead of the text)."""

    id: str
    key: str
    chat: str
    chat_alias: str
    author: str
    author_name: str
    own: bool
    ts: int
    text: str
    repo: str
    contest: str | None
    status: str
    created: str
    started: str | None = None
    ended: str | None = None
    unit: str | None = None
    branch: str | None = None
    worktree: str | None = None
    result: str | None = None
    cost: float | None = None
    error: str | None = None
    ack_ts: int | None = None
    approve_ts: int | None = None
    reply_ts: int | None = None
    reply_pending: bool = False


class JobStore:
    """Jobs and S7 state under `<data dir>/signal/agent/` (dir 0700, files 0600).

    `state.json`: `enabled`, `cursor` (store seq read so far), `failures` in a row, `members` (chat → ACIs),
    `greeted` chats, `spent` (date → USD). Writers hold `lock()`; `tick` never waits for it.
    """

    def __init__(self):
        from . import signalstore
        self.dir = signalstore.store_dir() / "agent"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.dir.chmod(0o700)

    @contextmanager
    def lock(self):
        """Non-blocking exclusive lock `agent.lock`; yields whether it is held."""
        fd = os.open(self.dir / "agent.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held = True
            except BlockingIOError:
                held = False
            yield held
            if held:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def _read(self, name: str, default):
        from . import signalstore
        value = signalstore.read_json(self.dir / name, default)
        return value if isinstance(value, type(default)) else default

    def _write(self, name: str, value) -> None:
        from . import signalstore
        signalstore.write_json(self.dir / name, value)

    def state(self) -> dict:
        return self._read("state.json", {})

    def set_state(self, **changes) -> dict:
        state = self.state()
        state.update(changes)
        self._write("state.json", state)
        return state

    def _audit(self, job: Job, change: dict) -> None:
        from . import signalstore
        record = {"at": datetime.now().astimezone().isoformat(timespec="seconds"), "job": job.id, **change}
        if "text" in record:
            record["text_sha256"] = hashlib.sha256(str(record.pop("text")).encode()).hexdigest()
        signalstore._append_bytes(self.dir / "jobs.jsonl", (json.dumps(record, ensure_ascii=False) + "\n").encode())

    def all(self) -> list[Job]:
        return [Job(**j) for j in self._read("jobs.json", {}).values()]

    def get(self, job_id: str) -> Job | None:
        raw = self._read("jobs.json", {}).get(job_id)
        return Job(**raw) if raw else None

    def by_key(self, key: str) -> Job | None:
        return next((j for j in self.all() if j.key == key), None)

    def add(self, req: Request, repo: Path, contest: Path | None, status: str) -> Job | None:
        """Record a job for `req` (claim before launch); None when this request already has one."""
        if self.by_key(req.key) is not None:
            return None
        job = Job(id=hashlib.sha1(req.key.encode()).hexdigest()[:8], key=req.key, chat=req.chat,
                  chat_alias=req.chat_alias, author=req.author, author_name=req.author_name, own=req.own,
                  ts=req.ts, text=req.text, repo=str(repo), contest=str(contest) if contest else None,
                  status=status, created=datetime.now().astimezone().isoformat(timespec="seconds"))
        jobs = self._read("jobs.json", {})
        jobs[job.id] = asdict(job)
        self._write("jobs.json", jobs)
        self._audit(job, {k: v for k, v in asdict(job).items() if k != "id"})
        return job

    def update(self, job_id: str, **fields) -> Job:
        jobs = self._read("jobs.json", {})
        jobs[job_id].update(fields)
        self._write("jobs.json", jobs)
        job = Job(**jobs[job_id])
        self._audit(job, fields)
        return job

    def running(self) -> list[Job]:
        return [j for j in self.all() if j.status == "running"]

    def queued(self, repo: Path) -> list[Job]:
        return sorted((j for j in self.all() if j.status == "queued" and j.repo == str(repo)), key=lambda j: j.created)

    def active_in_chat(self, chat: str) -> list[Job]:
        return [j for j in self.all() if j.chat == chat and j.status in ACTIVE]

    def enabled(self) -> bool:
        return bool(self.state().get("enabled"))

    def set_enabled(self, on: bool) -> None:
        self.set_state(enabled=on, failures=0 if on else self.state().get("failures", 0))

    def record_failure(self) -> bool:
        """Count a failed job; True when this one switched S7 off (the caller alerts the owner)."""
        failures = int(self.state().get("failures") or 0) + 1
        self.set_state(failures=failures)
        if failures >= BREAKER:
            self.set_state(enabled=False)
            return True
        return False

    def record_success(self) -> None:
        self.set_state(failures=0)

    def members(self, chat: str) -> list[str]:
        return list((self.state().get("members") or {}).get(chat) or [])

    def set_members(self, chat: str, ids: list[str]) -> None:
        members = self.state().get("members") or {}
        members[chat] = sorted(set(ids))
        self.set_state(members=members)


def admit(req: Request, store: JobStore, now: datetime) -> tuple[str, str | None]:
    """Decide what happens to a new request: ("queued", None), ("waiting-approval", why) or ("refused", why)."""
    if not store.enabled():
        return "refused", "S7 wyłączony (właściciel: /agent on w Notatce do siebie)"
    if len(store.active_in_chat(req.chat)) >= QUEUE_LIMIT:
        return "refused", f"kolejka pełna ({QUEUE_LIMIT})"
    if req.received_ms is not None:
        age = now - datetime.fromtimestamp(req.received_ms / 1000).astimezone()
        if age > MAX_AGE:
            return "waiting-approval", f"stare zlecenie (sprzed {int(age.total_seconds() // 3600)} h)"
    if REQUIRE_SNAPSHOT and not req.own and req.chat != SELF and req.author not in store.members(req.chat):
        return "waiting-approval", "nowa osoba w grupie (spoza listy z chwili dodania grupy)"
    return "queued", None


def approval(event: dict, store: JobStore) -> str | None:
    """Job id approved by this event: the owner's own 👍 in Note to Self on house's approval question."""
    if event.get("kind") != "reaction" or event.get("dir") != "own" or event.get("chat") != SELF:
        return None
    r = event.get("reaction") or {}
    if r.get("remove"):
        return None
    from .inbox import reaction_answer
    if reaction_answer(str(r.get("emoji") or "")) != "yes":
        return None
    target = (event.get("target") or {}).get("ts")
    job = next((j for j in store.all() if j.status == "waiting-approval" and j.approve_ts == target), None)
    return job.id if job else None


# --- workspace and launch -----------------------------------------------------------------------------------------

BUDGET_USD = 10                                    # Q5: per job (--max-budget-usd)
ALLOWED_DOMAINS = ["pypi.org", "files.pythonhosted.org", "download.pytorch.org"]  # Q3
RUNTIME_MAX_S = 7200
CONTEXT_MESSAGES = 30
PROMPT_TEMPLATE = "core/templates/signal-agent-prompt.md"


def job_files(job: Job) -> dict[str, Path]:
    """House-side files of a job (outside the worktree, unreadable for the agent): prompt, settings, log."""
    d = JobStore().dir / job.id
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)
    return {"dir": d, "prompt": d / "prompt.md", "settings": d / "settings.json", "log": d / "log.jsonl",
            "err": d / "stderr.log"}


def worktree_of(job: Job) -> Path:
    return Path(job.repo) / ".worktrees" / f"signal-{job.id}"


def prepare(job: Job) -> Path:
    """Create the job's worktree on branch `signal/<id>` with `.house-job/` (request, context, files, out/).

    The context holds only this chat (last 30 messages); the attachments of the request message are copied to
    `.house-job/in/`. `.house-job/` and `.worktrees/` are excluded from git.
    """
    from . import gitutil, signalread, signalstore
    repo, wt, branch = Path(job.repo), worktree_of(job), f"signal/{job.id}"
    gitutil.exclude(repo, ".worktrees/")
    if not wt.exists():
        subprocess.run(["git", "worktree", "add", "-q", "-b", branch, str(wt), "HEAD"], cwd=repo, check=True,
                       capture_output=True, text=True)
    gitutil.exclude(wt, ".house-job/")
    jd = wt / ".house-job"
    for sub in ("in", "out"):
        (jd / sub).mkdir(parents=True, exist_ok=True)
    events = [e for e in signalstore.iter_events(raw=False) if e.get("chat") == job.chat]
    now = datetime.now().astimezone()
    view = signalread.View(events, now)
    msgs = [m for m in view.msgs if (m.ts or 0) <= job.ts][-CONTEXT_MESSAGES:]
    (jd / "context.md").write_text("\n".join(line for m in msgs for line in view.line(m)) + "\n")
    (jd / "request.md").write_text(f"Autor: {job.author_name}\n\n{job.text}\n")
    trigger = next((e for e in signalstore.latest(events) if e.get("key") == job.key), None)
    for a in (trigger or {}).get("attachments") or []:
        src = Path(str(a.get("path") or ""))
        if a.get("path") and src.is_file():
            shutil.copyfile(src, jd / "in" / Path(str(a.get("filename") or src.name)).name)
    return wt


def job_env(job: Job, wt: Path, claude: str) -> dict[str, str]:
    """The complete environment of the agent process (started with `env -i`): nothing from the owner's env."""
    return {"PATH": f"{Path(claude).parent}:/usr/local/bin:/usr/bin:/bin", "HOME": str(Path.home()),
            "LANG": "C.UTF-8", "HOUSE_AGENT_JOB": job.id, "HOUSE_AGENT_WORKTREE": str(wt),
            "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
            "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"}


def job_settings(job: Job, wt: Path) -> dict:
    """Claude Code settings for the job: `dontAsk`, no web or MCP tools, sandbox that fails closed, job hook."""
    from . import HOUSE_ROOT
    return {
        "permissions": {"defaultMode": "dontAsk",
                        "allow": ["Read", "Edit", "Write", "Glob", "Grep", "Bash"],
                        "deny": ["WebFetch", "WebSearch", "mcp__*", "Read(~/.ssh/**)", "Read(~/.config/**)",
                                 "Read(~/.local/share/**)", "Read(~/.claude/**)"]},
        "sandbox": {"enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False,
                    "autoAllowBashIfSandboxed": True,
                    "filesystem": {"denyRead": ["~/"], "allowRead": [str(wt)], "allowWrite": [str(wt)]},
                    "network": {"allowedDomains": list(ALLOWED_DOMAINS)}},
        "hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
            {"type": "command", "command": f"{Path(HOUSE_ROOT).resolve()}/bin/house-hook pre-tool"}]}]},
    }


def job_command(job: Job, wt: Path, claude: str) -> list[str]:
    """`systemd-run --user` command for the job: one transient unit, 2 h limit, whole cgroup killed on stop."""
    files = job_files(job)
    env = [f"{k}={v}" for k, v in job_env(job, wt, claude).items()]
    return ["systemd-run", "--user", "--unit", f"house-agent-{job.id}", "--collect", "--quiet",
            "-p", f"RuntimeMaxSec={RUNTIME_MAX_S}", "-p", "KillMode=control-group", "-p", f"WorkingDirectory={wt}",
            "-p", f"StandardInput=file:{files['prompt']}", "-p", f"StandardOutput=append:{files['log']}",
            "-p", f"StandardError=append:{files['err']}",
            "--", "/usr/bin/env", "-i", *env,
            claude, "-p", "--permission-mode", "dontAsk", "--permission-prompts", "none",
            "--setting-sources", "local", "--settings", str(files["settings"]), "--strict-mcp-config",
            "--disallowedTools", "WebFetch", "WebSearch", "mcp__*", "--max-budget-usd", str(BUDGET_USD),
            "--output-format", "stream-json", "--verbose"]


def _prompt(job: Job, wt: Path) -> str:
    from . import HOUSE_ROOT
    jd = wt / ".house-job"
    files = ", ".join(sorted(p.name for p in (jd / "in").iterdir())) or "brak"
    return (Path(HOUSE_ROOT) / PROMPT_TEMPLATE).read_text().format(
        repo=Path(job.repo).name, branch=f"signal/{job.id}", author=job.author_name, request=job.text, files=files,
        quote="")


def launch(job: Job, run=subprocess.run, claude: str | None = None) -> str | None:
    """Prepare the workspace and start the job's unit; returns the unit name, or None (job marked failed)."""
    store = JobStore()
    claude = claude or shutil.which("claude")
    if not claude:
        store.update(job.id, status="failed", error="brak `claude` na tej maszynie")
        return None
    try:
        wt = prepare(job)
    except (OSError, subprocess.CalledProcessError) as e:
        store.update(job.id, status="failed", error=f"worktree: {e}"[:300])
        return None
    files = job_files(job)
    files["prompt"].write_text(_prompt(job, wt))
    files["settings"].write_text(json.dumps(job_settings(job, wt), indent=2))
    for p in (files["prompt"], files["settings"]):
        p.chmod(0o600)
    r = run(job_command(job, wt, claude), capture_output=True, text=True, check=False)
    if r.returncode != 0:
        store.update(job.id, status="failed", error=(r.stderr or "systemd-run").strip()[:300])
        return None
    unit = f"house-agent-{job.id}"
    store.update(job.id, status="running", unit=unit, branch=f"signal/{job.id}", worktree=str(wt),
                 started=datetime.now().astimezone().isoformat(timespec="seconds"))
    return unit
