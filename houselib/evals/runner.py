"""Runners that execute one eval case and write the session's stream log for the grader.

Claude Code runs inside the `house-agent` Docker runtime (`houselib.agentdocker`), Codex runs locally under its own
sandbox. Either way the agent works in the contest of a `TempRoot` and its stdout (stream-json / JSONL events) goes
to `run.jsonl`, stderr to `run.err`, both in a fresh **out-dir** `<state>/house/eval-out/run-*` (`out_base()`,
`~/.local/state` by default, mode 0700): owned by the host process, outside `/tmp` and `$TMPDIR` (which a Codex
workspace-write sandbox may write), never mounted into the container and never inside the temp root.
`RunResult.out_dir` is that directory: the caller owns it and must remove it (`sandbox.remove_tree`) when the grader is
done with the log; `TempRoot.cleanup` does not know about it. (It is removed here when `run_case` raises, unless a container could not be removed.)

Safety rules (this module starts paid, tool-using agent sessions):
- The container gets the hardening of Signal jobs (`agentdocker.hardening_flags`: owner uid, no capabilities,
  `no-new-privileges`, read-only root, tmpfs home, memory and pids limits, only the internal network behind the
  allowlist proxy) and these mounts, in this order (Docker applies the more specific one over its parent): the eval's
  temp dir read-write (`data/`, `eval.log`), `<tmp>/house` read-only (code under test, guard, hooks), the fixture
  contest read-write (the workspace) and `<tmp>/origin.git` read-only. Neither the real repo nor the real home nor
  the Docker socket is mounted; the workdir is the fixture contest.
- The log is what the grader trusts, so the agent must not be able to edit it: log, stderr and the env-file live in
  the out-dir, outside the read-write mount (`HOUSE_EVAL_LOG`, which the agent's own house writes, stays inside the
  mount and is untrusted). Docker reads `--env-file` on the host and the host process captures stdout/stderr.
- The subscription token reaches the container only through a 0600 `--env-file` in the out-dir, never argv; the file
  is deleted when the run ends. The container environment is built from an allowlist of `TempRoot.env()` keys, so no
  other variable of the host leaks in.
- Claude runs with `--permission-mode auto --permission-prompts none` and `--disallowedTools "Bash(git push:*)"`,
  never `--dangerously-skip-permissions`; the prompt goes through stdin (see "Turns"). Codex runs
  with `--sandbox workspace-write` with `/tmp` and `$TMPDIR` writes excluded (`TMPDIR` is `<contest>/.tmp-eval`,
  git-excluded), the sandbox network off (`network_access=false`) and stdin `/dev/null`. Its environment is an
  allowlist (`codex_env`: `PATH`, `LANG`, `LC_*`, `TERM`, `TMPDIR`, the `HOUSE_*` of `CONTAINER_ENV_KEYS`, the eval
  git identity), not the host's; `HOME` and `CODEX_HOME` are throwaway directories in the temp root and
  `CODEX_HOME` holds only a private copy of `auth.json` (the whole login; `config.toml` is not needed). Known
  limits: workspace-write still lets Codex *read* the rest of the disk (hence `--allow-codex` in the CLI), and a
  same-uid process could still write to `/proc/<pid>/fd/1`, so trusting a child's stdout is not tamper-proof.
- Every run is its own process group (`start_new_session`); at the timeout, and on an interrupt or any exception
  while waiting, the group is killed with SIGKILL and, for Docker, the container is removed by its name
  (`docker rm -f house-eval-<session id>`, since killing the client does not stop it); an exception re-raises, so no
  agent outlives the temp root; if the container cannot be removed, the out-dir is kept and `run.err` there says so.
  The command inside the container is also wrapped in `timeout -k 10 <timeout_seconds + 30>`, so the agent stops
  by itself even if the host loses it. `term_as_interrupt` turns SIGTERM and SIGHUP into `KeyboardInterrupt` for the
  caller's run, and `sweep_stale` (called under `eval.lock`) removes what a hard kill (SIGKILL, power loss) left:
  `house-eval-*` containers and out-dirs and temp roots older than an hour.
- A case variant (`cases.Variant`) patches the temp root before anything is started (`apply_variant`, then
  `variant_prompt`; `variant_followups` swaps the `followup_text` slot): every path stays inside the contest after symlinks are resolved, a patch that cannot be applied
  (an edit matching nothing, a missing file, a path escaping the contest, a prompt over `MAX_PROMPT_CHARS`) is `infra`
  ("variant <name>: ...") and nothing is started. The patch is on the run's own temp root only, so variants never see
  each other's changes. Its `remove`/`edit` changes are committed in the contest (`chore: setup`) and the host baselines renewed, so the
  contest starts clean and graders compare with the patched state.
- Turns (the prompt and `Case.followups`): every Claude run uses `--input-format stream-json` and its stdin (a pipe
  fed by a thread) carries the prompt and every follow-up as `{"type":"user","message":{...}}` lines; the prompt is
  never in argv (a one-turn case is a feed with no follow-ups; a 500K prompt in argv would fail with `E2BIG`). Lines
  written together are folded into one turn by the CLI, so line k+1 is written only after the k-th `result` event
  appeared in the stream log and only if it was a `success`; stdin is closed after the last result or a failed one.
  `--max-turns` still limits the whole run. `RunResult.turns_sent` / `turns_ok` record the lines written and the
  `success` results of the log; the grader needs every scheduled turn to succeed (C4 of K5).
- Hidden tests (predicate `hidden_tests`, `run_hidden_tests`; C6/C7 of K5: counts of at most 9 digits, else the
  predicate's summary is `malformed`; at most 1 MB of each output stream kept on disk; a hidden container that cannot
  be removed sets `cleanup_failed` like the agent's): the tests live in `<tmp>/hidden/<dir>` (written by the
  fixture), outside the contest. The agent container mounts `<tmp>` but masks `<tmp>/hidden` with an empty tmpfs, so
  it can neither read nor change them. After the agent container is gone, a second container with the same hardening
  runs the predicate's command: `--network none`, no env-file (no token, no proxy), the contest and the hidden dir
  mounted read-only, `PYTHONDONTWRITEBYTECODE=1` and HOME/TMPDIR on tmpfs, wrapped in `timeout`; the host never
  executes agent-written code. It runs only when the agent run is gradable (no `infra`, container removed); a Docker
  failure of its own is `infra` for the run. The summary it prints is untrusted text (the code under test can print
  anything), so the counts are a measurement, not a proof.
- Anything wrong with the environment (no Docker, image, token, `codex`) is reported as `infra` before anything is
  started; after a run, `infra` is set only when the session produced no genuine agent event (see `classify`; synthetic
  API-error messages do not count), so a real agent failure is never blamed on the infrastructure.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path

from .. import adapters, agentdocker
from .. import spec as specmod
from .cases import DEFAULT_HIDDEN_TIMEOUT_SECONDS, Case, Variant
from .fixtures import HIDDEN_DIR, MATERIAL_DIR
from .results import house_reads
from .sandbox import EVAL_IDENTITY, TempRoot, remove_tree

LOG_NAME = "run.jsonl"
ERR_NAME = "run.err"
ENV_NAME = "container.env"
OUT_PREFIX = "run-"
TMP_DIR = ".tmp-eval"  # Codex TMPDIR inside the contest
REMOVE_TIMEOUT = 20
CONTAINER_GRACE = 30  # seconds the in-container `timeout` waits beyond the case's timeout (then `-k 10` kills)
STALE_AGE = 3600  # seconds after which a leftover out-dir or temp root is considered abandoned
TEMP_PREFIX = "house-eval-"  # `TempRoot`'s mkdtemp prefix
CONTAINER_PREFIX = "house-eval-"
DEFAULT_CODER_MODEL = "sonnet"
# `TempRoot.env()` keys that make sense inside the container (PATH and HOME are set separately).
CONTAINER_ENV_KEYS = ("HOUSE_EVAL", "HOUSE_DATA_DIR", "HOUSE_EVAL_LOG", "HOUSE_NOTIFY_CONFIG", "HOUSE_SIGNAL_CONFIG",
                      "HOUSE_SIGNAL_ATTACHMENTS", "HOUSE_DIR")
# Events that prove the agent actually ran: Claude `assistant`, Codex `item.completed`; see `is_agent_event`.
AGENT_EVENTS = ("assistant", "item.completed")
SYNTHETIC_MODEL = "<synthetic>"  # Claude Code's own messages for API errors (limits, auth, ...)
# Event types whose text is scanned for infra keywords (plus synthetic/error messages, stderr, non-JSON lines).
SCAN_EVENTS = ("result", "system", "error", "turn.failed")
SETTINGS_FILE = ".claude/settings.local.json"  # relative to the contest
MAX_PROMPT_CHARS = 2_000_000  # a variant prompt after `{file:...}` expansion
FILE_REF = re.compile(r"\{(file|material):([^{}]+)\}")
BASE_HOME = agentdocker.BASE_ENV["HOME"]  # the container's tmpfs home
FEED_POLL = 0.05  # seconds between looks at the stream log while waiting for a turn's result
FEED_READ = 1 << 16
HIDDEN_TMP = "/tmp"  # TMPDIR of the hidden-tests container: its tmpfs
HIDDEN_TAIL = 65536  # bytes of the end of the hidden tests' output that are read (the summary is last)
HIDDEN_KEEP = 1 << 20  # bytes of each hidden-tests output stream kept on disk (the last ones)
SUMMARY_DIGITS = 9  # longest count a test summary may hold; a longer one is a malformed summary
HIDDEN_DOCKER_FAILURE = (125, 126, 127)  # `docker run` itself failed, or the command could not be started
HIDDEN_TIMED_OUT = (124, 137)  # `timeout` in the container fired (SIGTERM, then SIGKILL)
INFRA_TEXT = re.compile(r"usage limit|rate limit|credit balance|authentication|oauth|docker:|\b40[13]\b",
                        re.IGNORECASE)


@dataclass
class RunResult:
    """What one run left behind.

    Attributes:
        log: The stream log (`<out_dir>/run.jsonl`, outside the agent's mount); exists (possibly empty) even when
            nothing was started.
        exit: Process exit code; None when nothing was started or the run timed out.
        duration: Seconds from start to exit or kill (0.0 when nothing was started).
        infra: None, or why the run says nothing about the agent (the result is then `infra_error`).
        timed_out: The case's `timeout_seconds` passed and the process group was killed.
        session_id: Claude session id passed with `--session-id`; for Codex the `thread_id` of its first event
            (empty when absent).
        out_dir: Directory holding the log and stderr; the caller removes it once the log has been graded, unless
            `cleanup_failed`.
        cleanup_failed: On a timeout the container could not be removed (`docker rm -f` failed; it may still run
            and write the contest): the caller does not grade the run, records `infra_error`, and keeps `out_dir`
            (its `run.err` says why) and the temp root.
        hidden: `hidden_tests` results by index in `case.outcome`: `{"exit": int | None, "passed": int, "failed":
            int}` (`exit` None: the tests timed out). Empty when the case has none or they were not run.
        turns_sent: User turns written to the Claude CLI's stdin (prompt included); 0 for Codex or when nothing was
            started.
        turns_ok: `result` events of the stream log that are a `success` (`count_successes`); 0 for Codex. The grader
            requires `1 + len(case.followups)` of them (and an exited process) before a run is gradable.
        leftover: Name of a container that could not be removed (with `cleanup_failed`: the agent's on a timeout, or
            a hidden-tests container); empty otherwise.
        house_reads: Tool calls of the log (main thread and sub-agents) whose input names the read-only house export
            outside the contest (`results.house_reads`); 0 when nothing was started.
    """
    log: Path
    exit: int | None
    duration: float
    infra: str | None
    timed_out: bool
    session_id: str
    out_dir: Path | None = None
    cleanup_failed: bool = False
    hidden: dict[int, dict] = field(default_factory=dict)
    turns_sent: int = 0
    turns_ok: int = 0
    leftover: str = ""
    house_reads: int = 0


# Infra texts that are really mistakes in the case or the commit under test (a profile that was not installed, an
# unresolvable model), not the environment: the CLI records them as `fail` with the note `setup`.
SETUP_PREFIXES = ("profile not installed", "model:")


def is_setup_error(infra: str | None) -> bool:
    """True when a `RunResult.infra` text names a problem of the case or commit (`SETUP_PREFIXES`), not the host."""
    return bool(infra) and infra.startswith(SETUP_PREFIXES)


class _Infra(Exception):
    """Internal: the environment (not the agent) prevents the run; the message becomes `RunResult.infra`."""


class _Stuck(_Infra):
    """Internal: a hidden-tests container could not be removed; the run keeps its evidence (`cleanup_failed`)."""

    def __init__(self, message: str, name: str):
        super().__init__(message)
        self.name = name


class MalformedSummary(ValueError):
    """A test summary whose counts are not believable numbers (more than `SUMMARY_DIGITS` digits)."""


def _bad(variant: Variant, text: str) -> _Infra:
    return _Infra(f"variant {variant.name}: {text}")


def _contest_path(root: TempRoot, variant: Variant, rel: str, follow: bool = True) -> Path:
    """Resolve a contest-relative path of a variant, refusing anything that ends outside the contest.

    The contest itself is not a valid target. Symlinks are resolved: `a/link/x` with `link` leaving the contest is
    refused, and so is a final symlink that points outside unless `follow` is False (removal acts on the link).

    Raises:
        _Infra: The path is absolute, holds a NUL byte, uses `..` to leave the contest, or resolves outside it.
    """
    contest = Path(os.path.realpath(root.contest))

    def inside(p: Path) -> bool:
        return contest in p.parents

    if os.path.isabs(rel) or "\0" in rel:  # a NUL byte makes `realpath` raise `ValueError`
        raise _bad(variant, f"{rel!r} is not a path inside the contest")
    lexical = Path(os.path.normpath(contest / rel))
    if not inside(lexical):
        raise _bad(variant, f"{rel!r} is not a path inside the contest")
    if not follow:
        parent = Path(os.path.realpath(lexical.parent))
        if parent != contest and not inside(parent):
            raise _bad(variant, f"{rel!r} leaves the contest through a symlink")
        return parent / lexical.name
    real = Path(os.path.realpath(lexical))
    if not inside(real):
        raise _bad(variant, f"{rel!r} leaves the contest through a symlink")
    return real


def _merge(base: dict, patch: dict, prefix: str = "") -> list[str]:
    """Deep-merge `patch` into `base` in place; a `None` value deletes the key, other values replace.

    Returns:
        The dotted paths of deletes whose key was not there (a missing intermediate object included); empty when
        every delete found its key.
    """
    missing: list[str] = []
    for key, value in patch.items():
        if value is None:
            if key not in base:
                missing.append(prefix + key)
            base.pop(key, None)
        elif isinstance(value, dict):
            node = base.get(key)
            if not isinstance(node, dict):
                node = base[key] = {}
            missing += _merge(node, value, f"{prefix}{key}.")
        else:
            base[key] = value
    return missing


def _material(root: TempRoot, variant: Variant, name: str, what: str) -> Path:
    """The regular file `<tmp>/material/<name>` after symlinks are resolved; `_Infra` when it is not one below there."""
    base = Path(os.path.realpath(root.tmp / MATERIAL_DIR))
    path = Path(os.path.realpath(base / name)) if "\0" not in name else base
    if base not in path.parents or not path.is_file():
        raise _bad(variant, f"{what}: material {name!r} is missing (no regular file below {MATERIAL_DIR}/)")
    return path


def apply_variant(root: TempRoot, variant: Variant) -> None:
    """Patch the run's contest as the variant says, before the container starts.

    Order: `remove`, `copy` (fixture material from `<tmp>/material/` into the contest), `edit` (the three committed
    in the contest as one `chore: setup` commit, `TempRoot.commit_patch`, which also renews the host baselines, so
    the contest starts clean), `settings`, `env` (never committed: the settings file is git-excluded); the prompt is
    resolved afterwards by `variant_prompt`. `settings` is
    deep-merged into `<contest>/.claude/settings.local.json` (created when missing) and `env` into its `env` block,
    both with JSON `null` deleting a key. The variant itself is never changed.

    Args:
        root: The entered temp root (`root.contest`, and `root.commit_patch()` after `remove`/`edit`).
        variant: The patch.

    Raises:
        _Infra: A path escapes the contest or does not exist, a `copy` names missing material or an existing
            target, an edit matches nothing or the file is not UTF-8,
            the settings file is not a JSON object, a `null` in `settings` or `env` names a key that is not there
            (a control arm that deletes nothing would silently equal the treatment), or the file system refuses a
            change (the message starts with `variant <name>:`).
    """
    try:
        _apply(root, variant)
    except OSError as e:
        raise _bad(variant, f"cannot patch the contest ({type(e).__name__}: {e.strerror or e})") from e


def _apply(root: TempRoot, variant: Variant) -> None:
    """The body of `apply_variant`; may raise `OSError`, which the caller turns into `_Infra`."""
    for rel in variant.remove:
        target = _contest_path(root, variant, rel, follow=False)
        if not os.path.lexists(target):
            raise _bad(variant, f"remove: {rel!r} does not exist")
        if target.is_dir() and not target.is_symlink():
            if not remove_tree(target):
                raise _bad(variant, f"remove: could not delete {rel!r}")
        else:
            target.unlink()
    for item in variant.copy:
        source = _material(root, variant, item.material, "copy")
        target = _contest_path(root, variant, item.to)
        if os.path.lexists(target):
            raise _bad(variant, f"copy: {item.to!r} exists (copy only adds files; use edit to change one)")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    committed = bool(variant.remove or variant.copy)
    for edit in variant.edit:
        target = _contest_path(root, variant, edit.path)
        try:
            text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            raise _bad(variant, f"edit: cannot read {edit.path!r} as UTF-8 text ({type(e).__name__})") from e
        try:
            new, count = re.subn(edit.pattern, edit.repl, text)
        except (re.error, IndexError) as e:
            raise _bad(variant, f"edit of {edit.path!r}: bad pattern or replacement ({e})") from e
        if count < 1:
            raise _bad(variant, f"edit of {edit.path!r} matched nothing")
        target.write_text(new, encoding="utf-8")
        committed = True
    if committed:
        try:
            root.commit_patch()  # one neutral commit; the host baselines are taken again from it
        except RuntimeError as e:
            raise _bad(variant, f"could not commit the patch ({e})") from e
    if variant.settings or variant.env:
        path = root.contest / SETTINGS_FILE
        try:
            settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, ValueError) as e:
            raise _bad(variant, f"{SETTINGS_FILE} is not readable JSON ({type(e).__name__})") from e
        if not isinstance(settings, dict):
            raise _bad(variant, f"{SETTINGS_FILE} is not a JSON object")
        missing = _merge(settings, variant.settings)
        if missing:
            raise _bad(variant, f"settings: {', '.join(missing)} not present")
        if variant.env:
            if not isinstance(settings.get("env", {}), dict):
                raise _bad(variant, f"{SETTINGS_FILE}: 'env' is not an object")
            missing = _merge(settings.setdefault("env", {}), variant.env)
            if missing:
                raise _bad(variant, f"env: {', '.join(missing)} not present")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def variant_followups(variant: Variant | None, followups: list[str]) -> list[str]:
    """The follow-up turns of a run: the case's, with the variant's `followup_text` slot replaced.

    Args:
        variant: The variant, or None.
        followups: The case's follow-ups (never changed).

    Returns:
        A new list of the same length.

    Raises:
        _Infra: The slot's index is outside `followups` (the message starts with `variant <name>:`).
    """
    slot = None if variant is None else variant.followup_text
    if slot is None:
        return list(followups)
    if not 0 <= slot.index < len(followups):
        raise _bad(variant, f"followup_text: index {slot.index} is outside the case's {len(followups)} follow-up(s)")
    return [slot.text if i == slot.index else text for i, text in enumerate(followups)]


def variant_prompt(root: TempRoot, variant: Variant | None, default: str) -> str:
    """The prompt of a run: the case prompt, or the variant's with every `{file:<path>}` and `{material:<name>}`
    expanded.

    `{file:}` reads a contest file, `{material:}` a file of `<tmp>/material/` (fixture material that is never in the
    contest or its history, e.g. the document of a `pasted` arm). The text is inserted as is and is not expanded
    again. Call it after `apply_variant`, so a file that the variant edited or removed is read in that state.

    Args:
        root: The entered temp root (only `root.contest` is used).
        variant: The variant, or None.
        default: The case prompt, returned when there is no variant prompt.

    Returns:
        The prompt to send.

    Raises:
        _Infra: A referenced file is missing, escapes the contest (or the material directory), is not UTF-8 text,
            or the expanded prompt is longer than `MAX_PROMPT_CHARS`.
    """
    if variant is None or variant.prompt is None:
        return default
    template = variant.prompt
    total = len(template)
    cache: dict[str, str] = {}

    def expand(match: re.Match) -> str:
        nonlocal total
        kind, rel = match.group(1), match.group(2)
        key = f"{kind}:{rel}"
        if key not in cache:
            target = _contest_path(root, variant, rel) if kind == "file" else _material(root, variant, rel, "prompt")
            try:
                if target.stat().st_size > 4 * MAX_PROMPT_CHARS:  # a character takes at most 4 bytes
                    raise _bad(variant, f"prompt: {rel!r} is longer than {MAX_PROMPT_CHARS} characters")
                cache[key] = target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                raise _bad(variant, f"prompt: cannot read {rel!r} as UTF-8 text ({type(e).__name__})") from e
        total += len(cache[key]) - len(match.group(0))
        if total > MAX_PROMPT_CHARS:
            raise _bad(variant, f"prompt: longer than {MAX_PROMPT_CHARS} characters after expanding files")
        return cache[key]

    return FILE_REF.sub(expand, template)


def out_base() -> Path:
    """`<state>/house/eval-out`: where out-dirs go (`$XDG_STATE_HOME`, default `~/.local/state`), never under `/tmp`."""
    state = os.environ.get("XDG_STATE_HOME")
    return (Path(state) if state else Path.home() / ".local/state") / "house/eval-out"


def make_out_dir() -> Path:
    """A fresh 0700 out-dir under `out_base()`."""
    base = out_base()
    base.mkdir(parents=True, exist_ok=True)
    base.chmod(0o700)
    return Path(tempfile.mkdtemp(prefix=OUT_PREFIX, dir=base))


def resolve_model(case: Case, root: TempRoot, override: str | None = None) -> str:
    """Pick the Claude model: `override`, else `case.model`, else the profile's tier as adapters resolve it.

    The `coder` profile follows the contest's `night.model` (default `sonnet`), like night's workers.

    Args:
        case: The case.
        root: The temp root (its `core/agents` profiles and the contest's spec).
        override: Explicit model (`house eval run --model`).

    Returns:
        A model alias or id for `claude --model`.

    Raises:
        _Infra: The case has neither model nor a known profile with an available tier.
    """
    if override or case.model:
        return override or case.model
    if not case.agent:
        raise _Infra("model: skill case without a model")
    profiles = adapters.load_profiles(root.root)
    profile = profiles.get(case.agent)
    if profile is None:
        raise _Infra(f"model: unknown agent profile {case.agent!r}")
    if case.agent == "coder":
        return specmod.get(specmod.load_spec(root.contest / ".house"), "night.model") or DEFAULT_CODER_MODEL
    tier = adapters.resolve_tier(profile, {}, profiles)
    if not tier:
        raise _Infra(f"model: no available tier for profile {case.agent!r}")
    return tier


def first_turn(case: Case) -> str:
    """The text of the first user turn: the prompt as is (`/<skill> <prompt>` for a skill case)."""
    return f"/{case.skill} {case.prompt}" if case.skill else case.prompt


def user_lines(case: Case) -> list[str]:
    """The stream-json input of a Claude run: one `{"type":"user",...}` line per turn, prompt first (a one-turn case is
    a feed with no follow-ups)."""
    return [json.dumps({"type": "user", "message": {"role": "user", "content": text}})
            for text in [first_turn(case), *case.followups]]


def claude_argv(case: Case, root: TempRoot, model: str, session_id: str) -> list[str]:
    """The `claude` command line run inside the container: `--agent` for an agent case, `/<skill> ...` for a skill
    case, neither for a main-session case (`session: main`, the coordinator of the contest's CLAUDE.md/AGENTS.md).

    The prompt is never in argv (a 500K-character prompt would exceed the kernel's per-argument limit, `E2BIG`):
    `--input-format stream-json` makes the CLI read every turn, the first one included, from stdin (`user_lines`).
    """
    argv = ["claude", "-p"]
    if case.agent:
        argv += ["--agent", case.agent]
    argv += ["--model", model, "--max-turns", str(case.max_turns), "--session-id", session_id,
             "--output-format", "stream-json", "--verbose",
             "--settings", str(root.contest / ".claude/settings.local.json"),
             "--permission-mode", "auto", "--permission-prompts", "none"]
    return argv + ["--input-format", "stream-json", "--disallowedTools", "Bash(git push:*)"]


def write_container_env(root: TempRoot, out_dir: Path) -> Path:
    """`<out_dir>/container.env` (0600): token, proxy, Claude settings and the allowlisted `TempRoot.env()` values."""
    env = root.env()
    lines = {"CLAUDE_CODE_OAUTH_TOKEN": agentdocker.read_token(), **agentdocker.proxy_env(), **agentdocker.BASE_ENV,
             **{k: env[k] for k in CONTAINER_ENV_KEYS if k in env},
             "PATH": f"{root.root / 'bin'}{os.pathsep}{agentdocker.IMAGE_PATH}",
             "GIT_AUTHOR_NAME": EVAL_IDENTITY[0], "GIT_AUTHOR_EMAIL": EVAL_IDENTITY[1],
             "GIT_COMMITTER_NAME": EVAL_IDENTITY[0], "GIT_COMMITTER_EMAIL": EVAL_IDENTITY[1]}
    return agentdocker.write_env_lines(out_dir / ENV_NAME, lines)


def container_argv(case: Case, root: TempRoot, model: str, session_id: str, env_file: Path) -> list[str]:
    """`docker run` for an eval: the shared hardening, the mounts of the module docstring, workdir = contest; the
    command is `timeout -k 10 <timeout_seconds + 30> claude ...`. The hidden tests dir and the material dir inside
    the temp dir are masked by empty read-only tmpfs mounts."""
    return ["docker", "run", *agentdocker.hardening_flags(CONTAINER_PREFIX + session_id, env_file),
            "--tmpfs", f"{hidden_root(root)}:ro,noexec,nosuid,size=4k",
            "--tmpfs", f"{root.tmp / MATERIAL_DIR}:ro,noexec,nosuid,size=4k",
            "-v", f"{root.tmp}:{root.tmp}", "-v", f"{root.root}:{root.root}:ro",
            "-v", f"{root.contest}:{root.contest}", "-v", f"{root.tmp / 'origin.git'}:{root.tmp / 'origin.git'}:ro",
            "-w", str(root.contest), agentdocker.image_name(),
            "timeout", "-k", "10", str(case.timeout_seconds + CONTAINER_GRACE),
            *claude_argv(case, root, model, session_id)]


def hidden_root(root: TempRoot) -> Path:
    """`<tmp>/hidden`: where fixtures put hidden tests (`fixtures.write_hidden`)."""
    return root.tmp / HIDDEN_DIR


def hidden_argv(root: TempRoot, name: str, pred: dict, hidden: Path) -> list[str]:
    """`docker run` for the hidden tests of one predicate: the agent's hardening minus its network and token.

    `--network none`, no env-file (no token, no proxy, no `HOUSE_*`), the contest and the hidden dir `hidden`
    (`<tmp>/hidden/<dir>`) read-only at their host paths, workdir = contest, `PYTHONDONTWRITEBYTECODE=1`, HOME and
    TMPDIR on tmpfs. The command is `timeout -k 10 <timeout + 30> <command>` with `{hidden}` in its items replaced by
    the container path of the hidden dir.
    """
    timeout = pred.get("timeout_seconds", DEFAULT_HIDDEN_TIMEOUT_SECONDS)
    env = {"PYTHONDONTWRITEBYTECODE": "1", "HOME": BASE_HOME, "TMPDIR": HIDDEN_TMP, "LANG": "C.UTF-8"}
    command = [item.replace("{hidden}", str(hidden)) for item in pred["command"]]
    return ["docker", "run", *agentdocker.hardening_flags(name, None, network="none"),
            *[x for k, v in env.items() for x in ("-e", f"{k}={v}")],
            "-v", f"{root.contest}:{root.contest}:ro", "-v", f"{hidden}:{hidden}:ro",
            "-w", str(root.contest), agentdocker.image_name(),
            "timeout", "-k", "10", str(timeout + CONTAINER_GRACE), *command]


def codex_argv(case: Case, root: TempRoot) -> list[str]:
    """`codex exec --json --sandbox workspace-write` (no `/tmp` or `$TMPDIR` writes, no network) `-C <contest> --
    <prompt>`."""
    return ["codex", "exec", "--json", "--sandbox", "workspace-write",
            "-c", "sandbox_workspace_write.exclude_slash_tmp=true",
            "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=true",
            "-c", "sandbox_workspace_write.network_access=false",
            "-C", str(root.contest), "--", case.prompt]


CODEX_ENV_KEYS = ("PATH", "LANG", "TERM")  # plus every `LC_*`


def real_codex_home() -> Path:
    """The owner's Codex home: `$CODEX_HOME`, else `~/.codex` (only `auth.json` is ever read from it)."""
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def codex_env(root: TempRoot, tmpdir: Path, codex_home: Path, home: Path) -> dict[str, str]:
    """The whole environment of a local Codex run: an allowlist, never the host's environment.

    Args:
        root: The temp root (`env()` supplies `PATH` with the house under test first and the `HOUSE_*` paths).
        tmpdir: `TMPDIR` (the contest's `.tmp-eval`).
        codex_home: Throwaway `CODEX_HOME` holding the copied `auth.json`.
        home: Throwaway `HOME`.

    Returns:
        `PATH`, `LANG`, `LC_*`, `TERM`, the `HOUSE_*` of `CONTAINER_ENV_KEYS`, the eval git identity, `TMPDIR`,
        `HOME` and `CODEX_HOME`; nothing else (no API keys, `GIT_CONFIG_*`, `SSH_*`, ...).
    """
    env = root.env()
    kept = {k: v for k, v in env.items() if k in CODEX_ENV_KEYS or k.startswith("LC_") or k in CONTAINER_ENV_KEYS}
    return {**kept, "GIT_AUTHOR_NAME": EVAL_IDENTITY[0], "GIT_AUTHOR_EMAIL": EVAL_IDENTITY[1],
            "GIT_COMMITTER_NAME": EVAL_IDENTITY[0], "GIT_COMMITTER_EMAIL": EVAL_IDENTITY[1],
            "TMPDIR": str(tmpdir), "HOME": str(home), "CODEX_HOME": str(codex_home)}


def prepare_codex_home(root: TempRoot) -> tuple[Path, Path]:
    """Create the throwaway `HOME` and `CODEX_HOME` in the temp root and copy only `auth.json` (0600) into the latter.

    Args:
        root: The temp root (both directories live in `root.tmp`, outside the contest the agent may write).

    Returns:
        `(home, codex_home)`.

    Raises:
        _Infra: The owner has no `auth.json` (no Codex login).
    """
    source = real_codex_home() / "auth.json"
    if not source.is_file():
        raise _Infra(f"codex: no login ({source} is missing; `codex login`)")
    home, codex_home = root.tmp / "codex-user", root.tmp / "codex-home"
    home.mkdir(mode=0o700, exist_ok=True)
    codex_home.mkdir(mode=0o700, exist_ok=True)
    target = codex_home / "auth.json"
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(source.read_bytes())
    return home, codex_home


def codex_tmpdir(root: TempRoot) -> Path:
    """`<contest>/.tmp-eval`, created and git-excluded: the only temp dir Codex may write."""
    tmp = root.contest / TMP_DIR
    tmp.mkdir(exist_ok=True)
    exclude = root.contest / ".git/info/exclude"
    if exclude.parent.is_dir():
        known = exclude.read_text().splitlines() if exclude.exists() else []
        if f"/{TMP_DIR}/" not in known:
            with exclude.open("a") as f:
                f.write(f"/{TMP_DIR}/\n")
    return tmp


def check_installed(case: Case, root: TempRoot) -> None:
    """Raise `_Infra` unless `house adapt` installed the case's profile or skill in the contest's `.claude/` (for a
    main-session case: the contest's `CLAUDE.md`, which the main session reads)."""
    if case.session:
        path = root.contest / "CLAUDE.md"
    elif case.agent:
        path = root.contest / ".claude/agents" / f"{case.agent}.md"
    else:
        path = root.contest / ".claude/skills" / str(case.skill) / "SKILL.md"
    if not path.is_file():
        raise _Infra(f"profile not installed: {path.relative_to(root.contest)} is missing in the contest")


def check_docker(run) -> None:
    """Raise `_Infra("docker: <what is missing>")` unless Docker, the image and the token are all there."""
    if not agentdocker.docker_usable(run):
        raise _Infra("docker: Docker is not usable (`docker info` failed)")
    if not agentdocker.image_present(run):
        raise _Infra(f"docker: image {agentdocker.image_name()} is missing (`house signal agent image`)")
    if not agentdocker.token_present():
        raise _Infra(f"docker: no agent token ({agentdocker.token_path()}; `claude setup-token`)")


def _events(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            out.append(event)
    return out


def is_agent_event(event: dict) -> bool:
    """True for an event that shows the agent really ran.

    A Claude `assistant` event counts unless it is one of Claude Code's synthetic API-error messages
    (`message.model == "<synthetic>"`) or carries an `error` field; a Codex `item.completed` counts unless its
    item is an `error`.
    """
    kind = event.get("type")
    if kind == "assistant":
        message = event.get("message")
        message = message if isinstance(message, dict) else {}
        return message.get("model") != SYNTHETIC_MODEL and not event.get("error") and not message.get("error")
    if kind == "item.completed":
        item = event.get("item")
        return not (isinstance(item, dict) and item.get("type") == "error")
    return False


def _scan_text(log_text: str, err_text: str) -> str:
    """The text searched for infra keywords: stderr, `result`/`system`/error events, synthetic and error agent
    messages and lines that are not JSON. Genuine agent output (what the agent said or ran) is left out."""
    parts = [err_text]
    for line in log_text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            parts.append(line)
            continue
        if isinstance(event, dict) and (event.get("type") in SCAN_EVENTS
                                        or (event.get("type") in AGENT_EVENTS and not is_agent_event(event))):
            parts.append(line)
    return "\n".join(parts)


def classify(exit_code: int | None, log_text: str, err_text: str, tool: str) -> str | None:
    """Decide whether a finished run says nothing about the agent.

    Infra means: no genuine agent event (`is_agent_event`) in the log AND (a non-zero exit OR the scanned text
    mentions a usage or rate limit, a credit problem, 401/403, authentication, OAuth or `docker:`). The scanned
    text is stderr, the `result`/`system` events (an `is_error` result carries the limit or auth message), synthetic
    API-error assistant messages, Codex error items and non-JSON lines, not the agent's own output. A run that
    produced genuine agent events is never infra, whatever its exit code.

    Args:
        exit_code: Exit code of the process.
        log_text: The stream log.
        err_text: Captured stderr.
        tool: `claude` or `codex` (for the message).

    Returns:
        The reason, or None.
    """
    if any(is_agent_event(e) for e in _events(log_text)):
        return None
    mention = INFRA_TEXT.search(_scan_text(log_text, err_text))
    if exit_code == 0 and not mention:
        return None
    detail = mention.group(0).lower() if mention else next((ln.strip() for ln in err_text.splitlines()
                                                            if ln.strip()), "no output")
    return f"{tool} exit {exit_code} without any assistant event: {detail}"


def _thread_id(log_text: str) -> str:
    return next((str(e["thread_id"]) for e in _events(log_text) if e.get("thread_id")), "")


@contextmanager
def term_as_interrupt():
    """Make SIGTERM and SIGHUP raise `KeyboardInterrupt` for the duration of the block, then restore the handlers.

    A kill of `house eval run` (terminal closed, `kill`) then unwinds through `run_case`, which kills the agent
    process group and removes the container, and through `TempRoot`, instead of leaving them running. Does nothing
    off the main thread (where signal handlers cannot be installed).
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def handler(signum, frame):
        raise KeyboardInterrupt

    old = {sig: signal.signal(sig, handler) for sig in (signal.SIGTERM, signal.SIGHUP)}
    try:
        yield
    finally:
        for sig, previous in old.items():
            signal.signal(sig, previous)


def _old_dirs(parent: Path, prefix: str, cutoff: float) -> list[Path]:
    """Real directories (never symlinks) in `parent` named `prefix*` whose mtime is before `cutoff`."""
    try:
        entries = list(parent.iterdir())
    except OSError:
        return []
    out = []
    for d in entries:
        try:
            if d.name.startswith(prefix) and d.is_dir() and not d.is_symlink() and d.stat().st_mtime < cutoff:
                out.append(d)
        except OSError:
            continue
    return out


def sweep_stale(run=subprocess.run, now: float | None = None, max_age: float = STALE_AGE,
                tmp_base: Path | None = None) -> list[str]:
    """Remove what a killed eval left behind; call it while holding `eval.lock`, so nothing here is in use.

    Removes every `house-eval-*` container (`docker ps -a`, `docker rm -f`), `run-*` directories in `out_base()` and
    `house-eval-*` temp roots in the system temp dir that are older than `max_age`. Docker being unusable is not an
    error (the directories are still swept); a removal that fails is reported, not raised.

    Args:
        run: `subprocess.run` replacement for the `docker` commands.
        now: Current time as `time.time()` would say (default: now).
        max_age: Age in seconds past which directories count as abandoned (default one hour).
        tmp_base: Where temp roots live (default: `tempfile.gettempdir()`).

    Returns:
        One line per removed container or directory, and per failure ("could not remove ...").
    """
    done: list[str] = []
    try:
        listed = run(["docker", "ps", "-a", "--format", "{{.Names}}"], capture_output=True, text=True, check=False,
                     timeout=REMOVE_TIMEOUT)
        names = [n.strip() for n in listed.stdout.splitlines()] if listed.returncode == 0 else []
    except (OSError, subprocess.SubprocessError):
        names = []
    for name in (n for n in names if n.startswith(CONTAINER_PREFIX)):
        try:
            r = run(["docker", "rm", "-f", name], capture_output=True, text=True, check=False, timeout=REMOVE_TIMEOUT)
            ok = r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
        done.append(f"removed stale container {name}" if ok else f"could not remove stale container {name}")
    cutoff = (time.time() if now is None else now) - max_age
    stale = (_old_dirs(out_base(), OUT_PREFIX, cutoff)
             + _old_dirs(tmp_base or Path(tempfile.gettempdir()), TEMP_PREFIX, cutoff))
    for d in stale:
        done.append(f"removed stale directory {d}" if remove_tree(d) else f"could not remove stale directory {d}")
    return done


@contextmanager
def _term_ignored():
    """Ignore SIGTERM and SIGHUP for the duration of the block (main thread only), then restore the handlers.

    A second kill while `_abort` runs would otherwise raise `KeyboardInterrupt` in the middle of `docker rm -f`.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    old = {sig: signal.signal(sig, signal.SIG_IGN) for sig in (signal.SIGTERM, signal.SIGHUP)}
    try:
        yield
    finally:
        for sig, previous in old.items():
            signal.signal(sig, previous)


def _abort(proc, docker_name: str | None, run, err: Path) -> bool:
    """Stop everything an interrupted run started: the group and the container (`docker rm -f` by name, which also
    works while the container is still being created). SIGTERM and SIGHUP are ignored while it runs.

    Returns:
        True when nothing is left running; False when the container could not be removed (the problem is written
        to `err` and to stderr).
    """
    with _term_ignored():
        return _abort_now(proc, docker_name, run, err)


def _abort_now(proc, docker_name: str | None, run, err: Path) -> bool:
    """The body of `_abort`."""
    if proc is not None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    problem = None
    if docker_name:
        try:
            r = run(["docker", "rm", "-f", docker_name], capture_output=True, text=True, check=False,
                    timeout=REMOVE_TIMEOUT)
            if r.returncode != 0 and "No such container" not in (r.stderr or ""):
                problem = f"exit {r.returncode}: {(r.stderr or '').strip()}"
        except (OSError, subprocess.SubprocessError) as e:
            problem = str(e)
    if problem:
        with err.open("a") as f:
            f.write(f"\nhouse eval: `docker rm -f {docker_name}` failed ({problem}); the container may still run\n")
        print(f"house eval: `docker rm -f {docker_name}` failed ({problem}); details in {err}", file=sys.stderr)
    if proc is not None:
        try:
            proc.wait(timeout=REMOVE_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            pass
    return problem is None


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


_UNITTEST_RAN = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)
_UNITTEST_COUNTS = re.compile(r"^(?:OK|FAILED) \((.*)\)\s*$", re.MULTILINE)
_PYTEST_SUMMARY = re.compile(r"^=+ (.*\bin \d[\d.]*s.*?) =+$", re.MULTILINE)


def _count(digits: str) -> int:
    """A summary count; `MalformedSummary` when it has more than `SUMMARY_DIGITS` digits (never a huge `int()`) or a
    character `int()` refuses (`str.isdigit` also accepts superscripts such as `²`)."""
    if len(digits) > SUMMARY_DIGITS:
        raise MalformedSummary(f"a count of {len(digits)} digits")
    try:
        return int(digits)
    except ValueError:
        raise MalformedSummary("a count that is not a number") from None


def parse_test_summary(text: str) -> tuple[int, int]:
    """`(passed, failed)` from the summary of `python -m unittest` or pytest; `(0, 0)` when there is none.

    unittest: `Ran N tests`, then `OK` or `FAILED (failures=a, errors=b, ...)`, with `skipped=` and `expected
    failures=` possibly in the brackets: passed = N - failures - errors - skipped - unexpected successes, failed =
    failures + errors + unexpected successes (expected failures count as passed). pytest (a `=== 3 passed, 1 failed in
    0.1s ===` line): the `passed` count, and `failed` + `error(s)`. The last summary in the text wins.

    Raises:
        MalformedSummary: A count of the summary that wins has more than `SUMMARY_DIGITS` digits (the text is
            untrusted output of agent-written code).
    """
    ran = list(_UNITTEST_RAN.finditer(text))
    if ran:
        last = ran[-1]
        counts = {}
        tail = _UNITTEST_COUNTS.search(text, last.end())
        for item in tail.group(1).split(",") if tail else ():
            name, _, number = item.strip().rpartition("=")
            if number.isdigit():
                counts[name] = _count(number)
        failed = counts.get("failures", 0) + counts.get("errors", 0) + counts.get("unexpected successes", 0)
        return max(_count(last.group(1)) - failed - counts.get("skipped", 0), 0), failed
    summaries = list(_PYTEST_SUMMARY.finditer(text))
    if summaries:
        found = {kind: _count(n) for n, kind in re.findall(r"(\d+) (passed|failed|errors?)\b", summaries[-1].group(1))}
        return found.get("passed", 0), found.get("failed", 0) + found.get("error", 0) + found.get("errors", 0)
    return 0, 0


def _drain(stream, path: Path, keep: int = HIDDEN_KEEP) -> threading.Thread:
    """Read a child's output pipe to its end in a thread and write its last `keep` bytes to `path`.

    The hidden tests run agent-written code that can print without end; holding only the tail (where the summary is)
    caps both memory and the file on disk. The file is written when the pipe closes (the container is gone).
    """
    def run() -> None:
        tail = bytearray()
        try:
            while True:
                chunk = stream.read1(FEED_READ) if hasattr(stream, "read1") else stream.read(FEED_READ)
                if not chunk:
                    break
                tail += chunk
                if len(tail) > 2 * keep:
                    del tail[:-keep]
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except (OSError, ValueError):
                pass
            try:
                path.write_bytes(bytes(tail[-keep:]))
            except OSError:
                pass

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def _tail(path: Path, limit: int = HIDDEN_TAIL) -> str:
    """The last `limit` bytes of a file as text (empty when unreadable)."""
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(f.tell() - limit, 0))
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _hidden_dir(root: TempRoot, pred: dict) -> Path:
    """The host directory of a predicate's hidden tests; `_Infra` unless it is a real directory below `<tmp>/hidden`."""
    base = Path(os.path.realpath(hidden_root(root)))
    path = Path(os.path.realpath(base / pred["dir"]))
    if path == base or base not in path.parents or not path.is_dir():
        raise _Infra(f"hidden tests: dir {pred['dir']!r} is not a directory below {HIDDEN_DIR}/ of the temp root")
    return path


def run_hidden_tests(root: TempRoot, case: Case, out_dir: Path, run=subprocess.run, popen=subprocess.Popen,
                     stuck: list[str] | None = None) -> dict:
    """Run the case's `hidden_tests` predicates, each in its own container (`hidden_argv`), after the agent is gone.

    The host never runs agent-written code: the command runs in a container with `--network none`, no token and the
    contest read-only. Its stdout and stderr are drained through pipes (`_drain`): only the last `HIDDEN_KEEP` bytes
    of each reach `<out_dir>/hidden-<i>.out|err`, and their tail is parsed (`parse_test_summary`). A run that does not
    finish within the predicate's `timeout_seconds` (or that the in-container `timeout` stops) is recorded with
    `exit: None`; a summary with unbelievable counts (`MalformedSummary`) with `malformed: True` and zero counts,
    so only that predicate fails.

    Args:
        root: The entered temp root (`<tmp>/hidden/<dir>` written by the fixture).
        case: The case; predicates are found by index in `case.outcome`.
        out_dir: The run's out-dir.
        run: `subprocess.run` replacement for the `docker` helper commands.
        popen: `subprocess.Popen` replacement.
        stuck: When given, the name of a container that could not be removed is appended to it (also when an
            interrupt is re-raised), so the caller keeps the out-dir.

    Returns:
        `{index: {"exit": int | None, "passed": int, "failed": int}}` (plus `"malformed": True` for a malformed
        summary); empty when the case has no such predicate.

    Raises:
        _Infra: Docker or the image is missing, a hidden dir is missing or outside `<tmp>/hidden`, `docker run` cannot
            be started or fails itself (exit 125-127); `_Stuck` (an `_Infra`) when a timed-out container could not
            be removed.
        BaseException: Whatever interrupted the run, after the container was removed (or recorded in `stuck`).
    """
    preds = [(i, p) for i, p in enumerate(case.outcome) if p["type"] == "hidden_tests"]
    if not preds:
        return {}
    if not agentdocker.docker_usable(run) or not agentdocker.image_present(run):
        raise _Infra(f"hidden tests: docker: Docker or the image {agentdocker.image_name()} is not available")
    results: dict[int, dict] = {}
    for i, pred in preds:
        hidden = _hidden_dir(root, pred)
        name = f"{CONTAINER_PREFIX}hidden-{uuid.uuid4()}"
        out, err = out_dir / f"hidden-{i}.out", out_dir / f"hidden-{i}.err"
        proc, code, timed_out, drains = None, None, False, []
        try:
            try:
                proc = popen(hidden_argv(root, name, pred, hidden), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, start_new_session=True, cwd=root.tmp)
            except OSError as e:
                raise _Infra(f"hidden tests: cannot start docker: {e}") from e
            drains = [_drain(proc.stdout, out), _drain(proc.stderr, err)]
            try:
                code = proc.wait(timeout=pred.get("timeout_seconds", DEFAULT_HIDDEN_TIMEOUT_SECONDS))
            except subprocess.TimeoutExpired:
                timed_out = True
                if not _abort(proc, name, run, out_dir / ERR_NAME):
                    if stuck is not None:
                        stuck.append(name)
                    raise _Stuck(f"hidden tests: container not removed ({name})", name) from None
        except _Infra:
            raise
        except BaseException:
            if not _abort(proc, name, run, out_dir / ERR_NAME) and stuck is not None:
                stuck.append(name)
            raise
        finally:
            for d in drains:
                d.join(timeout=REMOVE_TIMEOUT)
        if code in HIDDEN_DOCKER_FAILURE and not timed_out:
            detail = next((ln.strip() for ln in _tail(err).splitlines() if ln.strip()), "no output")
            raise _Infra(f"hidden tests: docker exit {code}: {detail[:200]}")
        if timed_out or code in HIDDEN_TIMED_OUT:
            results[i] = {"exit": None, "passed": 0, "failed": 0}
            continue
        try:
            passed, failed = parse_test_summary(_tail(err) + "\n" + _tail(out))
        except MalformedSummary:
            results[i] = {"exit": code, "passed": 0, "failed": 0, "malformed": True}
            continue
        results[i] = {"exit": code, "passed": passed, "failed": failed}
    return results


def count_successes(log_text: str) -> int:
    """The number of `result` events of a Claude stream log that are a `success` (not flagged `is_error`)."""
    return sum(1 for line in log_text.splitlines() if _result_subtype(line.encode("utf-8", "replace")) == "success")


def _result_subtype(raw: bytes) -> str | None:
    """The `subtype` of a stream line that is a Claude `result` event (`"error"` for one flagged `is_error`), else None."""
    try:
        event = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(event, dict) or event.get("type") != "result":
        return None
    return "error" if event.get("is_error") is True else str(event.get("subtype"))


@dataclass
class _Feed:
    """A running feeder: its thread and the number of user lines written so far."""
    thread: threading.Thread
    sent: int = 0


def _feed(proc, lines: list[str], log: Path, stop: threading.Event) -> _Feed:
    """Feed the stream-json turns to the process's stdin, one per finished turn, from a thread.

    The CLI folds lines that arrive together into one turn, so only the first line is written at once; the next one
    is written after the next `result` event shows up in the stream log (`log`, tailed from an offset: the file is
    never re-read), and only when that result's `subtype` is `success`. After the last turn's result, a result that is
    not a success (`error_max_turns`, ...), or `stop` being set (the run ended or was killed), stdin is closed. A
    broken pipe just ends the thread. It is a thread, so a child that never answers, or never reads, cannot hold the run
    past its timeout.

    Args:
        proc: The started process (`stdin` is a pipe).
        lines: The user lines, prompt first (`user_lines`).
        log: The file the process's stdout goes to.
        stop: Set by the caller when the process is gone; the thread then closes stdin and ends.

    Returns:
        The feed; `sent` counts the lines written (read it after joining the thread).
    """
    def send(line: str) -> None:
        proc.stdin.write((line + "\n").encode("utf-8"))
        proc.stdin.flush()
        feed.sent += 1

    def run() -> None:
        pending = list(lines)
        buffer = b""
        try:
            send(pending.pop(0))
            with log.open("rb") as f:
                while not stop.is_set():
                    chunk = f.read(FEED_READ)
                    if not chunk:
                        stop.wait(FEED_POLL)
                        continue
                    *complete, buffer = (buffer + chunk).split(b"\n")
                    for raw in complete:
                        subtype = _result_subtype(raw)
                        if subtype is None:
                            continue
                        if subtype != "success" or not pending:
                            return
                        send(pending.pop(0))
        except (OSError, ValueError):
            pass
        finally:
            try:
                proc.stdin.close()
            except (OSError, ValueError):
                pass

    feed = _Feed(threading.Thread(target=run, daemon=True))
    feed.thread.start()
    return feed


def run_case(case: Case, root: TempRoot, model: str | None = None, run=subprocess.run, popen=subprocess.Popen,
             clock=time.monotonic, variant: Variant | None = None) -> RunResult:
    """Run one case in its temp root and return the log and how the run ended.

    Args:
        case: The case (`tool` picks Claude-in-Docker or local Codex).
        root: An entered `TempRoot` holding the fixture contest (adapted: the case's profile or skill is installed
            under the contest's `.claude/`, else `infra` says "profile not installed").
        model: Overrides the model (Claude only); default `case.model`, then the profile's tier.
        run: `subprocess.run` replacement for the `docker` helper commands.
        popen: `subprocess.Popen` replacement for the agent process.
        clock: Monotonic clock for `duration`.
        variant: A variant of the case, applied to the contest first (`apply_variant`; its `prompt` replaces the
            case prompt). A patch that cannot be applied is `infra` ("variant <name>: ...") and nothing is started.

    Returns:
        The `RunResult` (its `out_dir` is created here and left to the caller to remove); `infra` is set (and
        nothing started) when Docker, the image, the token, `codex` or the case's profile is missing, and after the
        run per `classify`. A timeout is `timed_out=True`, `exit=None`, never infra; `cleanup_failed` when its
        container could not be removed. When the case has `hidden_tests` predicates and the run is gradable (no
        `infra`, container removed; a timed-out run included) their tests run afterwards in a second container
        (`run_hidden_tests`; `RunResult.hidden`); a Docker problem of that step is `infra`, and a hidden container
        that cannot be removed is `infra` with `cleanup_failed` and `leftover`, exactly like the agent's.

    Raises:
        BaseException: Whatever interrupted the run (`KeyboardInterrupt` included) after the agent process group
            and the container were killed and the out-dir was removed (kept, with a note in `run.err`, when the
            container could not be removed); also unexpected errors before the start.
    """
    out_dir = make_out_dir()
    log, err = out_dir / LOG_NAME, out_dir / ERR_NAME
    session_id = str(uuid.uuid4()) if case.tool == "claude" else ""
    container = CONTAINER_PREFIX + session_id if case.tool == "claude" else None
    env_file, proc = None, None
    stuck: list[str] = []  # hidden-tests containers that could not be removed
    leftover = ""
    try:
        log.touch()
        err.touch()
        try:
            if variant is not None:
                apply_variant(root, variant)
                case = replace(case, prompt=variant_prompt(root, variant, case.prompt),
                               followups=variant_followups(variant, case.followups))
            if case.tool == "codex":
                if not shutil.which("codex"):
                    raise _Infra("codex: `codex` is not on PATH")
                home, codex_home = prepare_codex_home(root)
                argv = codex_argv(case, root)
                kwargs = {"cwd": root.contest, "env": codex_env(root, codex_tmpdir(root), codex_home, home)}
            else:
                check_installed(case, root)
                claude_model = resolve_model(case, root, model)
                check_docker(run)
                agentdocker.ensure_networks(run)
                agentdocker.ensure_proxy(run)
                env_file = write_container_env(root, out_dir)
                argv = container_argv(case, root, claude_model, session_id, env_file)
                kwargs = {"cwd": root.tmp}
        except _Infra as e:
            return RunResult(log, None, 0.0, str(e), False, session_id, out_dir)
        timed_out, code, cleanup_failed = False, None, False
        start = clock()
        with log.open("w") as out, err.open("w") as errf:
            try:
                piped = case.tool == "claude"  # stream-json input: every turn goes through stdin
                proc = popen(argv, stdin=subprocess.PIPE if piped else subprocess.DEVNULL, stdout=out, stderr=errf,
                             start_new_session=True, **kwargs)
            except OSError as e:
                return RunResult(log, None, 0.0, f"{case.tool}: cannot start: {e}", False, session_id, out_dir)
            stop = threading.Event()
            feeder = _feed(proc, user_lines(case), log, stop) if piped else None
            try:
                code = proc.wait(timeout=case.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                # same group kill and `docker rm -f` as an interrupt; on failure the out-dir stays as evidence
                cleanup_failed = not _abort(proc, container, run, err)
                leftover = (container or "") if cleanup_failed else ""
            finally:
                stop.set()
            if feeder:
                feeder.thread.join(timeout=REMOVE_TIMEOUT)
        duration = clock() - start
        log_text = _read(log)
        infra = None if timed_out else classify(code, log_text, _read(err), case.tool)
        hidden: dict[int, dict] = {}
        if infra is None and not cleanup_failed:
            try:
                hidden = run_hidden_tests(root, case, out_dir, run, popen, stuck)
            except _Stuck as e:  # like a stuck agent container: not graded, the evidence is kept
                infra, cleanup_failed, leftover = str(e), True, e.name
            except _Infra as e:
                infra = str(e)
    except BaseException:
        if _abort(proc, container, run, err) and not stuck:
            remove_tree(out_dir)  # on a failed container removal run.err stays as the evidence
        raise
    finally:
        if env_file:
            env_file.unlink(missing_ok=True)
    if case.tool == "codex":
        session_id = _thread_id(log_text)
    return RunResult(log, code, duration, infra, timed_out, session_id, out_dir, cleanup_failed, hidden,
                     turns_sent=feeder.sent if feeder else 0,
                     turns_ok=count_successes(log_text) if case.tool == "claude" else 0, leftover=leftover,
                     house_reads=house_reads(log, case.tool, root.root, root.contest))
