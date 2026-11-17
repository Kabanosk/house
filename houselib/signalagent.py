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
import shlex
import shutil
import stat
import subprocess
import unicodedata
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

TRIGGER_RE = re.compile(r"(?<![\w@.])@claude\b(?:@([A-Za-z0-9-]+))?", re.IGNORECASE)  # `@claude@dom`: host
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
        host: Host named with `@claude@<host>` (lower case), else None (see `signalhost`).
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
    host: str | None = None


@dataclass
class Command:
    """A chat command handled by house without a model: status, lb, stop, agent-off, agent-on.

    `host` is the host named with `/status@<host>` or `/agent@<host> on`, else None (see `signalhost`).
    """

    name: str
    key: str
    chat: str
    author: str
    own: bool
    quote_ts: int | None
    host: str | None = None


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
    if not event.get("raw") or (event.get("meta") or {}).get("migrated"):
        return None  # migrated or reconstructed events: no envelope, no receive time, never a trigger
    sent = _sent_text(event)
    if sent.lstrip().startswith("🤖"):
        return None
    sender = event.get("sender") or {}
    author = str(sender.get("uuid") or sender.get("number") or "?")
    own = event.get("dir") == "own"
    chat = str(event.get("chat"))
    quote_ts = (event.get("quote") or {}).get("ts")
    words = sent.strip().lower().split()
    head, _, cmd_host = (words[0] if words else "").partition("@")
    cmd_host = cmd_host or None
    if head in COMMANDS:
        return Command(COMMANDS[head], str(event["key"]), chat, author, own, quote_ts, cmd_host)
    if head == "/agent" and words[1:2] in (["off"], ["on"]):
        if own and chat == SELF:
            return Command(f"agent-{words[1]}", str(event["key"]), chat, author, own, quote_ts, cmd_host)
        return None
    trigger = TRIGGER_RE.search(sent)
    if not trigger:
        return None
    shown = str(event.get("text") or sent)
    full = " ".join(TRIGGER_RE.sub(" ", shown, count=1).split()).strip(" ,:@")
    hint = ALIAS_HINT_RE.match(full)
    env = (event.get("raw") or {}).get("envelope") or {}
    received = env.get("serverReceivedTimestamp")
    return Request(
        key=str(event["key"]), chat=chat, chat_alias=str(event.get("chat_alias") or chat), ts=int(event["ts"]),
        author=author, author_name=str(sender.get("name") or author), own=own,
        text=hint[2].strip() if hint else full, full_text=full, alias_hint=hint[1].lower() if hint else None,
        quote_ts=int(quote_ts) if quote_ts is not None else None,
        received_ms=int(received) if str(received or "").isdigit() else None,
        host=trigger[1].lower() if trigger[1] else None)


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
    no_bash: bool = False
    gitdir: str | None = None
    gitfile: str | None = None
    runtime: str = "local"


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
        job_id = hashlib.sha256(req.key.encode()).hexdigest()[:16]
        if self.by_key(req.key) is not None or self.get(job_id) is not None:
            return None
        job = Job(id=job_id, key=req.key, chat=req.chat,
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
    if req.received_ms is None:
        return "waiting-approval", "nieznany czas odbioru"
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

BUDGET_USD = None  # Q5 (Wojtek): jobs run on the subscription; the limit is RUNTIME_MAX_S, not dollars
ALLOWED_DOMAINS = ["pypi.org", "files.pythonhosted.org", "download.pytorch.org"]  # Q3
RUNTIME_MAX_S = 7200
CONTEXT_MESSAGES = 30
JOB_MEMORY_MAX = "8G"
JOB_TASKS_MAX = 512
COMMIT_FILE_MAX = 50 * 1024 * 1024
REPLY_READ_MAX = 64 * 1024
SANDBOX_PLACEHOLDERS = {".env", ".env.local", ".env.development", ".env.development.local", ".env.production",
                        ".env.production.local", ".env.test", ".env.test.local", ".gitmodules", ".npmrc", ".yarnrc",
                        ".yarnrc.yml", "bunfig.toml", "package.json", "package-lock.json", "pnpm-lock.yaml",
                        "yarn.lock"}
PROMPT_TEMPLATE = "core/templates/signal-agent-prompt.md"
CODE_ROOT = Path(__file__).resolve().parents[1]  # the house checkout running this code (template, house-hook)


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
    gitdir = subprocess.run(["git", "-C", str(wt), "rev-parse", "--absolute-git-dir"], capture_output=True,
                            text=True, check=True).stdout.strip()
    JobStore().update(job.id, gitdir=gitdir, gitfile=(wt / ".git").read_text())
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


def sandbox_usable(run=subprocess.run) -> bool:
    """Whether Claude Code's Bash sandbox can start here: it needs a user namespace inside a user namespace, which
    Ubuntu's AppArmor (`kernel.apparmor_restrict_unprivileged_userns=1`) refuses by default."""
    try:
        r = run(["bwrap", "--ro-bind", "/", "/", "--unshare-user", "--unshare-net", "--",
                 "bwrap", "--ro-bind", "/", "/", "--unshare-user", "true"], capture_output=True, text=True,
                check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def _tools(job: Job, wt: Path) -> list[str]:
    """Permission rules the job may use without asking: file tools scoped to the worktree, Glob and Grep (the hook
    keeps them inside it). Bash is never listed: it runs only when sandboxed (`autoAllowBashIfSandboxed`), so a
    sandbox that does not engage means no Bash, not unsandboxed Bash."""
    return [f"Read(/{wt}/**)", f"Edit(/{wt}/**)", f"Write(/{wt}/**)", "Glob", "Grep"]


def job_settings(job: Job, wt: Path) -> dict:
    """Claude Code settings for the job: `dontAsk`, no web or MCP tools, sandbox that fails closed, job hook.

    Without a usable sandbox (`job.no_bash`) Bash is denied altogether: the agent reads and edits code only.
    """
    return {
        "permissions": {"defaultMode": "dontAsk",
                        "allow": _tools(job, wt),
                        "deny": ["WebFetch", "WebSearch", "mcp__*", "Read(~/.ssh/**)", "Read(~/.config/**)",
                                 "Read(~/.local/share/**)", "Read(~/.claude/**)", *(["Bash"] if job.no_bash else [])]},
        "sandbox": {"enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False,
                    "autoAllowBashIfSandboxed": True,
                    "filesystem": {"denyRead": ["~/"], "allowRead": [str(wt), str(Path(job.repo) / ".git")],
                                   "allowWrite": [str(wt)]},
                    "network": {"allowedDomains": list(ALLOWED_DOMAINS)}},
        "hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
            {"type": "command", "command": f"{shlex.quote(str(CODE_ROOT / 'bin/house-hook'))} pre-tool"}]}]},
    }


def docker_settings(job: Job, wt: Path) -> dict:
    """Settings inside the job container: the container is the boundary, so Bash is allowed and Claude Code's own
    sandbox is off; file rules stay scoped to the worktree and the job hook (fail closed) stays on."""
    return {
        "permissions": {"defaultMode": "dontAsk", "allow": [*_tools(job, wt), "Bash"],
                        "deny": ["WebFetch", "WebSearch", "mcp__*"]},
        "sandbox": {"enabled": False},
        "hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
            {"type": "command", "command": "/house/bin/house-hook pre-tool"}]}]},
    }


def _claude_argv(job: Job, wt: Path, claude: str, settings: str, tools: list[str]) -> list[str]:
    return [claude, "-p", "--permission-mode", "dontAsk", "--permission-prompts", "none",
            "--setting-sources", "local", "--settings", settings, "--strict-mcp-config",
            "--allowedTools", *tools, "--disallowedTools", "WebFetch", "WebSearch", "mcp__*",
            *(["--max-budget-usd", str(BUDGET_USD)] if BUDGET_USD else []),
            "--output-format", "stream-json", "--verbose"]


def _unit_prefix(job: Job, wt: Path) -> list[str]:
    files = job_files(job)
    return ["systemd-run", "--user", "--unit", f"house-agent-{job.id}", "--collect", "--quiet",
            "-p", f"RuntimeMaxSec={RUNTIME_MAX_S}", "-p", "KillMode=control-group", "-p", f"WorkingDirectory={wt}",
            "-p", f"StandardInput=file:{files['prompt']}", "-p", f"StandardOutput=append:{files['log']}",
            "-p", f"StandardError=append:{files['err']}", "-p", f"MemoryMax={JOB_MEMORY_MAX}",
            "-p", f"TasksMax={JOB_TASKS_MAX}"]


def docker_job_command(job: Job, wt: Path) -> list[str]:
    """`systemd-run` around `docker run` for the job (the unit's 2 h limit and `/stop` remove the container)."""
    from . import agentdocker
    files = job_files(job)
    claude = _claude_argv(job, wt, "claude", "/job/settings.json", [*_tools(job, wt), "Bash"])
    inner = agentdocker.container_command(job.id, wt, Path(job.repo), files["prompt"], files["settings"],
                                          files["dir"] / "container.env", claude)
    return [*_unit_prefix(job, wt), "-p", f"ExecStopPost=docker rm -f house-agent-{job.id}", "--", *inner]


def job_command(job: Job, wt: Path, claude: str) -> list[str]:
    """`systemd-run --user` command for a local job: one transient unit, 2 h limit, whole cgroup killed on stop."""
    files = job_files(job)
    env = [f"{k}={v}" for k, v in job_env(job, wt, claude).items()]
    return [*_unit_prefix(job, wt), "--", "/usr/bin/env", "-i", *env,
            *_claude_argv(job, wt, claude, str(files["settings"]), _tools(job, wt))]


def _prompt(job: Job, wt: Path) -> str:
    jd = wt / ".house-job"
    files = ", ".join(sorted(p.name for p in (jd / "in").iterdir())) or "brak"
    text = (CODE_ROOT / PROMPT_TEMPLATE).read_text().format(
        repo=Path(job.repo).name, branch=f"signal/{job.id}", author=job.author_name, request=job.text, files=files,
        quote="")
    if job.no_bash:
        text += ("\nUwaga: na tej maszynie pracujesz bez uruchamiania kodu (brak Basha, sandbox niedostępny). Czytaj "
                 "i edytuj pliki; napisz w reply.md, co trzeba uruchomić i sprawdzić.\n")
    return text


def launch(job: Job, run=subprocess.run, claude: str | None = None) -> str | None:
    """Prepare the workspace and start the job's unit; returns the unit name, or None (job marked failed)."""
    store = JobStore()
    claude = claude or shutil.which("claude") or ""
    try:
        wt = prepare(job)
        job = store.get(job.id)
    except Exception as e:  # noqa: BLE001 — a broken workspace fails this job only
        store.update(job.id, status="failed", error=f"worktree: {e}"[:300])
        return None
    from . import agentdocker
    files = job_files(job)
    if agentdocker.runtime(run) == "docker":
        agentdocker.ensure_networks(run)
        agentdocker.ensure_proxy(run)
        job = store.update(job.id, runtime="docker", no_bash=False)
        agentdocker.write_env_file(files["dir"], job.id, wt)
        settings, command = docker_settings(job, wt), docker_job_command(job, wt)
    else:
        if not claude:
            store.update(job.id, status="failed", error="brak `claude` na tej maszynie")
            return None
        if not sandbox_usable(run):
            job = store.update(job.id, no_bash=True)
        settings, command = job_settings(job, wt), job_command(job, wt, claude)
    files["prompt"].write_text(_prompt(job, wt))
    files["settings"].write_text(json.dumps(settings, indent=2))
    for p in (files["prompt"], files["settings"]):
        p.chmod(0o644 if job.runtime == "docker" else 0o600)  # the container user reads them through ro mounts
    r = run(command, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        store.update(job.id, status="failed", error=(r.stderr or "systemd-run").strip()[:300])
        return None
    unit = f"house-agent-{job.id}"
    store.update(job.id, status="running", unit=unit, branch=f"signal/{job.id}", worktree=str(wt),
                 started=datetime.now().astimezone().isoformat(timespec="seconds"))
    return unit


# --- replies --------------------------------------------------------------------------------------------------------

REPLY_MAX = 1500
REPLY_FILES = 3
REPLY_FILE_MAX = 25 * 1024 * 1024
REPLY_TYPES = {".png", ".jpg", ".jpeg", ".pdf", ".csv", ".txt", ".md", ".json", ".log"}
DAILY_ALARM_USD = None  # Q5: subscription; spend is only recorded (estimate) for `agent status`


def _redact(text: str) -> str:
    from .export import SECRET_RES
    for r in SECRET_RES:
        text = r.sub("[REDACTED]", text)
    return text.replace(str(Path.home()), "~")


def _real_dir(path: Path, expected: Path) -> bool:
    """A directory that is not a symlink and resolves exactly to `expected` (the agent cannot redirect it)."""
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode) and path.resolve() == expected
    except OSError:
        return False


def _read_nofollow(path: Path, limit: int) -> bytes | None:
    """Read at most `limit` bytes of a regular file without following a symlink; None otherwise."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        chunks, left = [], limit
        while left > 0:
            chunk = os.read(fd, min(left, 1 << 20))
            if not chunk:
                break
            chunks.append(chunk)
            left -= len(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def build_reply(job: Job, wt: Path) -> tuple[str, list[Path], list[str]]:
    """What goes back to the chat: the agent's `reply.md` (redacted, ≤ 1500 chars, the rest as `wynik.md`) and
    at most 3 safe files from `.house-job/out/`, copied into the house-owned job dir before they are checked.

    `.house-job/` and `out/` must be real directories of this worktree (no symlinks). Files are read without
    following symlinks and refused when they are not regular, not of an allowed type, over 25 MB, look like
    `.env*`, or contain something that looks like a secret.

    Returns:
        (text, files to attach — copies under the job dir, notes about what was left out).
    """
    from .export import has_secret
    root = wt.resolve()
    jd, out = wt / ".house-job", wt / ".house-job" / "out"
    files: list[Path] = []
    notes: list[str] = []
    if not _real_dir(jd, root / ".house-job"):
        return "zakończone bez odpowiedzi", [], ["pominięto .house-job (podmieniony katalog)"]
    raw = _read_nofollow(jd / "reply.md", REPLY_READ_MAX) or b""
    text = _redact(raw.decode("utf-8", errors="replace").strip()) or \
        "zakończone bez odpowiedzi (agent nie zapisał reply.md)"
    send = job_files(job)["dir"] / "send"
    if not _real_dir(out, root / ".house-job" / "out"):
        notes.append("pominięto out/ (podmieniony katalog)")
    else:
        if send.exists():
            shutil.rmtree(send)
        send.mkdir(mode=0o700)
        for p in sorted(out.iterdir()):
            why = None
            data = None
            if p.name.lower().startswith(".env") or p.suffix.lower() not in REPLY_TYPES:
                why = "niedozwolony typ"
            elif len(files) >= REPLY_FILES:
                why = f"limit {REPLY_FILES} plików"
            else:
                data = _read_nofollow(p, REPLY_FILE_MAX + 1)
                if data is None:
                    why = "nie zwykły plik"
                elif len(data) > REPLY_FILE_MAX:
                    why = "za duży"
            if why is None:
                copy = send / p.name
                fd = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    os.write(fd, data)
                finally:
                    os.close(fd)
                if has_secret(copy):
                    copy.unlink()
                    why = "wygląda na sekret"
                else:
                    files.append(copy)
            if why:
                notes.append(f"pominięto {p.name} ({why})")
    if len(text) > REPLY_MAX:
        full = job_files(job)["dir"] / "wynik.md"
        full.write_text(text + "\n")
        full.chmod(0o600)
        files.append(full)
        text = text[:REPLY_MAX].rstrip() + "… (całość w wynik.md)"
    return text, files, notes


# --- dispatch -------------------------------------------------------------------------------------------------------

def _prefix(contest: str | None) -> str:
    from . import notify
    return notify.house_prefix(contest)


def _say(sig, job_or_chat, text: str, *, contest: str | None = None, quote=None, files=(), meta=None) -> int | None:
    """Send one message from house to a chat (prefix, logged by `Signal.send`)."""
    chat = job_or_chat.chat if isinstance(job_or_chat, Job) else job_or_chat
    contest = job_or_chat.contest if isinstance(job_or_chat, Job) else contest
    return sig.send(f"{_prefix(contest)} {text}", group_id=None if chat == SELF else chat,
                    attachments=[str(p) for p in files], quote=quote,
                    meta={"origin": "agent", "contest": _contest_name(contest), **(meta or {})})


def _contest_name(contest: str | None) -> str | None:
    if not contest:
        return None
    from . import notify
    return notify.contest_name(Path(contest))


def _snippet(text: str, width: int = 40) -> str:
    from .signalread import snippet
    return snippet(text, width)


def unit_active(job: Job, run=subprocess.run) -> bool | None:
    """Whether the job's systemd unit is still running; None when systemd could not be asked (skip this round)."""
    r = run(["systemctl", "--user", "show", str(job.unit), "-p", "ActiveState", "-p", "SubState"],
            capture_output=True, text=True, check=False)
    state = dict(line.split("=", 1) for line in (r.stdout or "").splitlines() if "=" in line)
    if r.returncode != 0 or "ActiveState" not in state:
        return None
    return state["ActiveState"] in ("active", "activating", "deactivating", "reloading")


def _result(job: Job) -> dict | None:
    """The last `{"type": "result"}` line of the job's stream-json log, or None (killed, timed out, crashed)."""
    log = job_files(job)["log"]
    if not log.exists():
        return None
    found = None
    for line in log.read_text(errors="replace").splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            found = obj
    return found


def _big_files(wt: Path) -> list[str]:
    """Paths (relative to the worktree) of regular files over COMMIT_FILE_MAX, never followed through links."""
    big = []
    for top, dirs, names in os.walk(wt, followlinks=False):
        dirs[:] = [d for d in dirs if d not in (".git", ".house-job")]
        for name in names:
            p = Path(top) / name
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode) and st.st_size > COMMIT_FILE_MAX:
                big.append(str(p.relative_to(wt)))
    return big


def _commit(job: Job, wt: Path) -> str:
    """Commit what the agent left in its worktree on the job branch (house does it, never the agent).

    The agent controlled the worktree, so git is pinned: the git dir recorded at `prepare` (the `.git` file must be
    unchanged), no system or global config, no hooks, no fsmonitor, `--no-verify`; `.house-job/` and files over
    50 MB are left out.

    Returns:
        The diff summary ("2 files changed, …"), "bez zmian w repo", or why nothing was committed.
    """
    gitfile = wt / ".git"
    try:
        intact = stat.S_ISREG(os.lstat(gitfile).st_mode) and gitfile.read_text() == job.gitfile and job.gitdir
    except OSError:
        intact = False
    if not intact:
        return "nie commituję: plik .git w worktree został zmieniony"
    who = {k: subprocess.run(["git", "-C", job.repo, "config", f"user.{k}"], capture_output=True, text=True,
                             check=False).stdout.strip() for k in ("name", "email")}
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(Path.home()), "GIT_DIR": job.gitdir,
           "GIT_WORK_TREE": str(wt), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "LANG": "C.UTF-8"}
    git = ["git", "-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
           "-c", f"user.name={who['name'] or 'house'}", "-c", f"user.email={who['email'] or 'house@localhost'}"]
    def run(*a: str) -> subprocess.CompletedProcess:
        return subprocess.run([*git, *a], cwd=wt, env=env, capture_output=True, text=True, check=False)
    untracked = run("ls-files", "--others", "--exclude-standard", "-z").stdout.split("\0")
    for rel in untracked:  # empty mount points the sandbox runtime creates in the working directory
        p = wt / rel
        if rel and Path(rel).name in SANDBOX_PLACEHOLDERS and p.is_file() and not p.is_symlink() \
                and p.stat().st_size == 0:
            p.unlink()
    big = _big_files(wt)

    run("add", "-A", "--", ".", ":(exclude).house-job", *[f":(exclude){p}" for p in big])
    if run("diff", "--cached", "--quiet").returncode == 0:
        summary = "bez zmian w repo"
    else:
        r = run("commit", "-q", "--no-verify", "-m", f"signal: {_snippet(job.text, 60)} ({job.author_name[:40]})")
        if r.returncode != 0:
            return f"commit nie wyszedł: {(r.stderr or '').strip()[:120]}"
        summary = run("diff", "--shortstat", "HEAD~1", "HEAD").stdout.strip() or "zmiany zapisane"
    return summary + (f"; pominięte duże pliki: {', '.join(big[:3])}" if big else "")


def _progress(job: Job, line: str) -> None:
    """One line in the contest's `.house/PROGRESS.md` (contests only; the house repo has none)."""
    if not job.contest:
        return
    path = Path(job.contest) / ".house/PROGRESS.md"
    if path.parent.is_dir():
        clean = " ".join(line.split())[:300]  # one line: chat text must not become Markdown structure
        with path.open("a") as f:
            f.write(f"- {datetime.now():%Y-%m-%d %H:%M} · signal · {clean}\n")


def finish(job: Job, sig, store: "JobStore") -> None:
    """Close a job whose unit has ended: commit, record the outcome and cost, reply to the chat."""
    (job_files(job)["dir"] / "container.env").unlink(missing_ok=True)  # holds the token
    wt = Path(job.worktree or worktree_of(job))
    result = _result(job)
    ok = bool(result) and result.get("subtype") == "success" and not result.get("is_error")
    cost = float(result.get("total_cost_usd") or 0) if result else None
    diff = _commit(job, wt) if wt.exists() else "brak worktree"
    if result is None:
        why = "przerwane (limit 2 h, /stop albo awaria)"
    else:
        why = None if ok else f"błąd: {result.get('subtype')}"
    text, files, notes = build_reply(job, wt) if wt.exists() else ("", [], [])
    head = "✓ gotowe" if ok else f"✗ {why}"
    body = f"{head}: „{_snippet(job.text)}”\n{text}\n\ngałąź: {job.branch or 'signal/' + job.id} · {diff}"
    if notes:
        body += "\n" + "; ".join(notes)
    if job.no_bash:
        body += "\n(uwaga: bez uruchamiania kodu, sandbox niedostępny na tej maszynie: `house doctor`)"
    store.update(job.id, status="done" if ok else "failed", ended=datetime.now().astimezone().isoformat(
        timespec="seconds"), cost=cost, result=head, error=why, reply_pending=True)
    _progress(job, f"zlecenie od {job.author_name[:40]} (cytat z czatu, dane): „{_snippet(job.text)}” → {head}, "
                   f"gałąź {job.branch}")
    (job_files(job)["dir"] / "reply.txt").write_text(json.dumps({"text": body, "files": [str(p) for p in files]}))
    if ok:
        store.record_success()
    elif store.record_failure():
        _say(sig, SELF, f"{BREAKER} nieudane zlecenia z rzędu, wyłączam S7. Włączenie: /agent on (tutaj).")
    if cost:
        _track_spend(sig, store, cost)
    _send_reply(store.get(job.id), sig, store)


def _send_reply(job: Job, sig, store: "JobStore") -> None:
    try:
        saved = json.loads((job_files(job)["dir"] / "reply.txt").read_text())
    except (OSError, ValueError):
        store.update(job.id, reply_pending=False, error=(job.error or "") + " (odpowiedź utracona)")
        return
    ts = _say(sig, job, saved["text"], quote=(job.ts, job.author), files=[Path(p) for p in saved["files"]],
              meta={"job": job.id})
    if ts is not None:
        store.update(job.id, reply_pending=False, reply_ts=ts)


def _track_spend(sig, store: "JobStore", cost: float) -> None:
    today = f"{datetime.now():%Y-%m-%d}"
    state = store.state()
    spent = {**(state.get("spent") or {})}
    spent[today] = round(float(spent.get(today) or 0) + cost, 4)
    store.set_state(spent=spent)
    if DAILY_ALARM_USD and spent[today] > DAILY_ALARM_USD and state.get("alarmed") != today:
        store.set_state(alarmed=today)
        _say(sig, SELF, f"S7 wydał dziś już {spent[today]:.2f} USD (próg alarmu {DAILY_ALARM_USD} USD). Nic nie "
                        "blokuję; wyłączenie: /agent off.")


def _accept(req: Request, sig, store: "JobStore", chats: dict, out_events: dict, now: datetime,
            forced_quote: int | None = None) -> None:
    """Turn a new request into a job (or a question / refusal) and tell the chat."""
    if req.chat not in chats or store.by_key(req.key) is not None:
        return
    if forced_quote is not None:
        req.quote_ts = forced_quote
    where = route(req, chats, out_events)
    if where.repo is None:
        from . import notify
        for contest, text in where.asks:
            _say(sig, req.chat, text, contest=str(contest), quote=(req.ts, req.author),
                 meta={"origin": "agent-ask", "contest": notify.contest_name(contest), "request": req.key})
        return
    req.text = where.text
    status, why = admit(req, store, now)
    job = store.add(req, where.repo, where.contest, status if status != "refused" else "failed")
    if job is None:
        return
    if status == "refused":
        store.update(job.id, error=why)
        _say(sig, job, f"nie uruchamiam: {why}", quote=(req.ts, req.author))
        return
    if status == "waiting-approval":
        _say(sig, job, f"czekam na zgodę Wojtka ({why})", quote=(req.ts, req.author))
        ts = _say(sig, SELF, f"Zlecenie od {req.author_name[:40]} (id {req.author[:8]}…) w „{req.chat_alias}”: "
                             f"„{_snippet(req.text, 80)}” ({why}). 👍 na tę wiadomość = uruchom.", contest=job.contest,
                  meta={"approve": job.id})
        if ts is None:
            store.update(job.id, status="failed", error="nie udało się zapytać Wojtka o zgodę")
        else:
            store.update(job.id, approve_ts=ts)
        return
    position = len(store.queued(where.repo)) - 1 + len([j for j in store.running() if j.repo == str(where.repo)])
    ack = f"przyjąłem: „{_snippet(req.text)}” · repo {Path(where.repo).name}"
    ack += f" · kolejka: {position}" if position else " · zaczynam"
    store.update(job.id, ack_ts=_say(sig, job, ack, quote=(req.ts, req.author)))


def tick(sig, now: datetime | None = None, run=subprocess.run, claude: str | None = None) -> list[str]:
    """One dispatcher round (after every timer pull): read new events, answer, finish ended jobs, start queued ones.

    The first round only sets the cursor (no historical `@claude` is ever executed). Never waits for another
    tick: a busy `agent.lock` returns at once.

    Args:
        sig: A `Signal` client (needed to reply); None → nothing happens.
        now: Reference time. Defaults to now.
        run: Runner for `systemd-run` / `systemctl` (signature of `subprocess.run`).
        claude: Path of the `claude` binary. Defaults to `shutil.which("claude")`.

    Returns:
        Log lines.
    """
    from . import signalchats, signalstore
    if sig is None:
        return ["agent: Signal wyłączony"]
    now = now or datetime.now().astimezone()
    store = JobStore()
    log: list[str] = []
    with store.lock() as held:
        if not held:
            return ["agent: inny tick trwa"]
        cursor = store.state().get("cursor")
        if cursor is None:
            store.set_state(cursor=signalstore.last_seq())
            return ["agent: kursor ustawiony, historia pominięta"]
        events = list(signalstore.iter_events())
        latest = signalstore.latest(events)
        out_events = {e["ts"]: e for e in latest if e.get("dir") == "out" and e.get("ts")}
        by_key = {e["key"]: e for e in latest}
        chats = signalchats.allowed(None)
        top = int(cursor)
        for e in sorted((e for e in latest if int(e.get("first_seq") or 0) > int(cursor)),
                        key=lambda e: e["first_seq"]):
            top = max(top, int(e["first_seq"]))
            approved = approval(e, store)
            if approved:
                store.update(approved, status="queued")
                log.append(f"agent: zatwierdzone {approved}")
                continue
            item = parse(e)
            if isinstance(item, (Request, Command)) and _elsewhere(item, e, sig, chats, latest, now):
                continue
            if isinstance(item, Request):
                _accept(item, sig, store, chats, out_events, now)
            elif isinstance(item, Command) and item.chat in chats:
                log += handle_command(item, sig, store, run=run, chats=chats, now=now)
            elif e.get("kind") == "message" and (e.get("quote") or {}).get("ts") in out_events:
                meta = out_events[e["quote"]["ts"]].get("meta") or {}
                original = by_key.get(str(meta.get("request") or ""))
                if meta.get("origin") == "agent-ask" and original is not None:
                    first = parse(original)
                    if isinstance(first, Request):
                        _accept(first, sig, store, chats, out_events, now, forced_quote=e["quote"]["ts"])
        store.set_state(cursor=top)
        for job in store.running():
            if unit_active(job, run) is not False:
                continue  # still running, or systemd unreachable this round
            try:
                finish(job, sig, store)
                log.append(f"agent: zakończone {job.id}")
            except Exception as e:  # noqa: BLE001 — one poisoned job must not wedge the queue
                store.update(job.id, status="failed", reply_pending=False, error=f"finish: {type(e).__name__}")
                _say(sig, job, f"✗ nie udało się zakończyć zlecenia ({type(e).__name__}); gałąź {job.branch}",
                     quote=(job.ts, job.author))
                log.append(f"agent: finish {job.id} nieudany ({type(e).__name__})")
        for job in [j for j in store.all() if j.reply_pending and j.status in ("done", "failed", "stopped")]:
            _send_reply(job, sig, store)
        if store.enabled():
            _greet(sig, store, chats)
            busy = {j.repo for j in store.running()}
            for repo in sorted({j.repo for j in store.all() if j.status == "queued"} - busy):
                job = store.queued(Path(repo))[0]
                try:
                    unit = launch(job, run=run, claude=claude)
                except Exception as e:  # noqa: BLE001
                    store.update(job.id, status="failed", error=f"launch: {type(e).__name__}")
                    unit = None
                log.append(f"agent: start {job.id}" if unit else f"agent: start {job.id} nieudany")
                if unit is None:
                    failed = store.get(job.id)
                    _say(sig, failed, f"✗ nie udało się uruchomić: {failed.error}", quote=(job.ts, job.author))
    return log


STATUS_EVERY = timedelta(seconds=30)
KNOWN_HOSTS_DAYS = 30


def target_host(item: "Request | Command", event: dict) -> str | None:
    """Host an event is addressed to: named (`@claude@X`, `/cmd@X`), else the host of the quoted house message,
    else None (the default `agent_host`; also for a quoted house message from before hosts were named)."""
    from . import signalhost
    if item.host:
        return item.host
    quoted = signalhost.parse_prefix((event.get("quote") or {}).get("text"))
    return quoted[1] if quoted else None


def known_hosts(events: list[dict], now: datetime) -> list[str]:
    """This host, `agent_host` and every host named in a house message of the last 30 days, sorted."""
    from . import signalhost
    cutoff = (now - timedelta(days=KNOWN_HOSTS_DAYS)).timestamp() * 1000
    hosts = {signalhost.this_host(), signalhost.agent_host()}
    for e in events:
        if e.get("dir") in ("out", "own") and int(e.get("ts") or 0) >= cutoff:
            parsed = signalhost.parse_prefix(e.get("text"))
            hosts.add(parsed[1] if parsed else None)
    return sorted(h for h in hosts if h)


def _elsewhere(item: "Request | Command", event: dict, sig, chats: dict, events: list[dict], now: datetime) -> bool:
    """True when another host handles this event (this one only moves the cursor).

    A host name nobody knows is answered by the default host, so a typo is never swallowed silently.
    """
    from . import signalhost
    if signalhost.addressed_to_me(target_host(item, event)):
        return False
    if item.host and item.chat in chats and signalhost.addressed_to_me(None):
        hosts = known_hosts(events, now)
        if item.host not in hosts:
            ts = item.ts if isinstance(item, Request) else int(item.key.rsplit(":", 1)[1])
            _say(sig, item.chat, f"nie znam hosta {item.host}; znane: {', '.join(hosts)}", quote=(ts, item.author))
    return True


def stop_job(job: Job, store: "JobStore", run=subprocess.run) -> Job:
    """Stop a running job's unit (or drop a queued one); the worktree and branch stay for inspection."""
    if job.status == "running" and job.unit:
        run(["systemctl", "--user", "stop", job.unit], capture_output=True, text=True, check=False)
    return store.update(job.id, status="stopped", ended=datetime.now().astimezone().isoformat(timespec="seconds"),
                        result="zatrzymane")


def _elapsed(job: Job, now: datetime) -> str:
    from .signalread import parse_iso
    start = parse_iso(job.started)
    return f"{int((now - start).total_seconds() // 60)} min" if start else "?"


def _leaderboard(cmd: Command, sig, store: "JobStore", chats: dict | None, now: datetime, quote) -> list[str]:
    """`/lb`: per contest of the chat, changes since this chat's previous `/lb` (state `lb_seen`), never a fetch."""
    from . import leaderboard
    from .signalread import parse_iso
    contests = list((chats or {}).get(cmd.chat).contests) if (chats or {}).get(cmd.chat) else []
    if not contests:
        _say(sig, cmd.chat, "brak konkursu dla tej rozmowy", quote=quote)
        return []
    for c in contests:
        key = f"{cmd.chat}|{c}"
        try:
            if leaderboard.config(c) is None:
                _say(sig, cmd.chat, "brak konfiguracji rankingu (spec.yaml: leaderboard.cmd)", contest=str(c),
                     quote=quote)
                continue
            prev = (store.state().get("lb_seen") or {}).get(key) or {}
            answer = leaderboard.summary(c, prev.get("at"), parse_iso(prev.get("asked")), now)
        except Exception as e:  # noqa: BLE001 — a broken leaderboard file must not break the dispatcher
            _say(sig, cmd.chat, f"błąd odczytu rankingu: {e}"[:300], contest=str(c), quote=quote)
            continue
        own = (prev["ts"], sig.account) if answer.sentences and prev.get("ts") else quote
        ts = _say(sig, cmd.chat, answer.text, contest=str(c), quote=own)
        if ts is not None and answer.at:
            seen = dict(store.state().get("lb_seen") or {})
            seen[key] = {"at": answer.at, "ts": ts, "asked": now.isoformat()}
            store.set_state(lb_seen=seen)
    return []


def handle_command(cmd: Command, sig, store: "JobStore", run=subprocess.run, chats: dict | None = None,
                   now: datetime | None = None) -> list[str]:
    """Answer a chat command without a model: /status, /lb, /stop (this chat's jobs only), /agent off|on (owner)."""
    now = now or datetime.now().astimezone()
    quote = (int(cmd.key.rsplit(":", 1)[1]), cmd.author)
    from . import signalhost
    host = signalhost.this_host()
    if cmd.name == "agent-off":
        store.set_enabled(False)
        _say(sig, SELF, f"S7 wyłączony na {host}: nowe zlecenia nie startują (działające trwają; `house signal agent "
                        "off --kill` je zatrzyma).", quote=quote)
        return ["agent: wyłączony z Signala"]
    if cmd.name == "agent-on":
        store.set_enabled(True)
        _say(sig, SELF, f"S7 włączony na {host}.", quote=quote)
        return ["agent: włączony z Signala"]
    mine = [j for j in store.all() if j.chat == cmd.chat]
    contest = next((j.contest for j in mine if j.contest), None)
    if cmd.name == "status":
        last = (store.state().get("status_at") or {}).get(cmd.chat)
        from .signalread import parse_iso
        if last and now - parse_iso(last) < STATUS_EVERY:
            return []
        store.set_state(status_at={**(store.state().get("status_at") or {}), cmd.chat: now.isoformat()})
        running = [f"„{_snippet(j.text)}” ({j.author_name}, {_elapsed(j, now)})" for j in mine if j.status == "running"]
        queued = [f"„{_snippet(j.text)}”" for j in mine if j.status in ("queued", "waiting-approval")]
        default = " (domyślny)" if signalhost.agent_host() in (None, host) else ""
        lines = [f"host: {host}{default}", f"w toku: {', '.join(running) or 'nic'}",
                 f"kolejka: {', '.join(queued) or 'pusta'}"]
        if not store.enabled():
            lines.append("S7 wyłączony")
        _say(sig, cmd.chat, "\n".join(lines), contest=contest, quote=quote)
        return []
    if cmd.name == "lb":
        return _leaderboard(cmd, sig, store, chats, now, quote)
    if cmd.name == "stop":
        target = None
        if cmd.quote_ts is not None:
            target = next((j for j in mine if cmd.quote_ts in (j.ack_ts, j.ts)), None)
        if target is None:
            target = next((j for j in sorted(mine, key=lambda j: j.created, reverse=True)
                           if j.status == "running"), None)
        if target is None or target.status not in ("running", "queued", "waiting-approval"):
            _say(sig, cmd.chat, "nic do zatrzymania", contest=contest, quote=quote)
            return []
        was = target.status
        stop_job(target, store, run)
        what = "zatrzymane" if was == "running" else "usunięte z kolejki"
        _say(sig, target, f"{what}: „{_snippet(target.text)}” · gałąź {target.branch or '—'} zostaje", quote=quote)
        return [f"agent: {what} {target.id}"]
    return []


GC_AFTER = timedelta(days=7)


def gc(now: datetime | None = None) -> list[str]:
    """Remove worktrees of jobs that ended over 7 days ago (their branches stay for merging)."""
    from .signalread import parse_iso
    now = now or datetime.now().astimezone()
    store, removed = JobStore(), []
    for job in store.all():
        ended = parse_iso(job.ended)
        wt = worktree_of(job)
        if job.status in ("done", "failed", "stopped") and ended and now - ended > GC_AFTER and wt.exists():
            subprocess.run(["git", "-C", job.repo, "worktree", "remove", "--force", str(wt)], capture_output=True,
                           text=True, check=False)
            if not wt.exists():
                removed.append(job.id)
    return removed


HELLO_TEMPLATE = "core/templates/signal-hello.md"


def _greet(sig, store: "JobStore", chats: dict) -> None:
    """Send the S7 greeting once to every allowed group (never Note to Self) while S7 is on."""
    greeted = set(store.state().get("greeted") or [])
    text = (CODE_ROOT / HELLO_TEMPLATE).read_text().strip()
    for chat in chats.values():
        if chat.id == SELF or chat.id in greeted:
            continue
        contest = str(chat.contests[0]) if len(chat.contests) == 1 else None
        if _say(sig, chat.id, text, contest=contest, meta={"origin": "agent-hello"}) is not None:
            greeted.add(chat.id)
            store.set_state(greeted=sorted(greeted))


def _host_lines() -> list[str]:
    """`house doctor` lines about hosts: this host and the default, or why routing may misfire."""
    from . import signalhost
    lines = [f"! S7: {p}" for p in signalhost.problems()]
    default = signalhost.agent_host()
    if default:
        return [f"✓ S7: host: {signalhost.this_host()} · domyślny: {default}", *lines]
    return [f"! S7: brak agent_host w signal.toml (host: {signalhost.this_host()}); przy kilku komputerach na "
            "jednym koncie Signal każdy weźmie zlecenie bez wskazanego hosta, wpisz ten sam `agent_host` na "
            "każdym", *lines]


def doctor_lines(run=subprocess.run, which=shutil.which) -> list[str]:
    """`house doctor` lines about S7: on/off, host routing, sandbox (bubblewrap usable), systemd-run, jobs,
    breaker."""
    store = JobStore()
    if not store.enabled():
        return ["- S7: wyłączony"]
    running = len(store.running())
    lines = [f"✓ S7: włączony · w toku: {running} · porażki z rzędu: {store.state().get('failures', 0)}"]
    lines += _host_lines()
    from . import agentdocker
    docker = agentdocker.doctor_lines(run)
    lines += docker
    if agentdocker.runtime(run) == "docker":
        return lines  # jobs run in containers; the local sandbox checks below do not matter
    if not which("bwrap"):
        lines.append("✗ S7: brak bwrap (sandbox Claude Code); `sudo apt install bubblewrap`")
    elif sandbox_usable(run):
        lines.append("✓ S7: sandbox (bwrap, zagnieżdżone userns) działa")
    else:
        lines.append("✗ S7: bwrap nie działa w sandboxie Claude Code (AppArmor blokuje zagnieżdżone userns); zlecenia "
                     "idą bez uruchamiania kodu. Naprawa wymaga sudo (profil AppArmor albo "
                     "kernel.apparmor_restrict_unprivileged_userns=0)")
    if not which("systemd-run"):
        lines.append("✗ S7: brak systemd-run")
    if not which("socat"):
        lines.append("✗ S7: brak socat (proxy sieci sandboxa); `sudo apt install socat`")
    if not which("claude"):
        lines.append("✗ S7: brak `claude`")
    return lines
