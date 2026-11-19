"""Tool-independent events read from agent transcripts (spec A0).

What an agent should know:
  * Sources: Claude Code `claude -p --output-format stream-json --verbose` logs (`claude_stream`, source `stream`),
    Codex `codex exec --json` logs (`codex_stream`, source `codex`) and interactive Claude Code session files
    `~/.claude/projects/<encoded cwd>/<session>.jsonl` plus their `<session>/subagents/agent-<id>.jsonl`
    (`claude_session`, source `session`; `contest_sessions` finds the files that belong to a contest directory).
    `load()` runs the parsers over a list of sources and de-duplicates `usage` globally.
  * Codex reports `input_tokens` including the cached part; `codex_stream` normalises it to Claude's convention
    (`input` excludes `cache_read` and cache writes), so `usage` events of both sources add up the same way.
  * One model call is one `usage` event: the stream repeats the same `message.usage` on every content-block event of
    the call (early records with a partial usage), so usage is kept once per (message id, request id), the best copy
    by the same rules as `load` (`_better`), while the content blocks still yield their own `tool_call` / `message`
    events.
  * Privacy rule: events carry sizes and names, but `raw` and `tool_call.input` hold the source record and can hold
    prompts, file contents and command output. Everything that leaves the process (reports, evidence, anything
    serialised) goes through `view`, the only serialisable form of an event: it keeps a whitelist of tool input
    fields (Bash commands redacted and cut), sizes and numbers, and never `raw`.
    `result_text` reads a tool result's text back from `raw` for in-memory checks only; it is never part of `view`.
  * Never raises on input (deeply nested JSON included): a line that is not a JSON object counts in `Parsed.broken`,
    a record of an unknown type in `Parsed.unknown` (by `type`, or `system/<subtype>` for unknown system subtypes), and a record with an odd shape is
    skipped. Files are read line by line, never whole.
"""
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .guard import redact

KINDS = ("tool_call", "tool_result", "message", "usage", "compaction", "session_total")
# Stream records and `system` subtypes that are understood but carry nothing we keep (some hold content: hook output,
# task prompts and summaries; those are deliberately never read).
_IGNORED_TYPES = {"rate_limit_event"}
_IGNORED_SYSTEM = {"hook_started", "hook_response", "thinking_tokens", "task_started", "task_progress",
                   "task_notification", "background_tasks_changed", "task_updated", "status"}


@dataclass
class Event:
    """One thing that happened in a session, independent of the tool that logged it.

    `data` depends on `kind`:
      tool_call: id, name, input.
      tool_result: id, is_error (True / False / None when unknown), size (length of the result as text).
      message: role, length.
      usage: key (message id, request id), input, cache_read, cache_write_5m, cache_write_1h, output, complete.
      compaction: trigger, pre_tokens, post_tokens, duration_ms.
      session_total: models ({model: input, cache_read, cache_write, output, cost_usd}, plus cache_write_5m and
        cache_write_1h when a stream `result` reports the TTL split), cost_usd, since_ms.
        `since_ms` (epoch ms, int or None) says since when the totals count. An interactive session's `cost-state`
        covers only its last process run, starting at `since_ms`, so compare it with the `usage` events whose `ts`
        is at or after that time, not with the whole file. None (a stream log is one process) means the whole log.
    A Codex `usage` event is one turn (`turn.completed`), which may hold several model calls; it is not one call and
    its input is not a context size (K0 must not read it as one).
    `raw` is the source record, kept in memory only and never serialised; `raw` and `data` are left out of `repr`, so
    printing or logging an event cannot leak prompts or command text.
    """
    kind: str
    ts: str | None
    session_id: str
    source: str
    model: str | None = None
    agent_id: str = ""
    parent_tool_use_id: str = ""
    agent_type: str = ""  # sub-agent type from `agent-<id>.meta.json` (session files only)
    sidechain: bool = False
    cwd: str | None = None
    git_branch: str | None = None
    cli_version: str | None = None
    data: dict = field(default_factory=dict, repr=False)
    raw: dict | None = field(default=None, repr=False)


@dataclass
class Parsed:
    """Result of parsing one transcript: events, counts of unknown record types, and unparsable lines."""
    events: list[Event] = field(default_factory=list)
    unknown: dict[str, int] = field(default_factory=dict)
    broken: int = 0


def _int(v) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def _num(v) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def _str(v) -> str:
    return v if isinstance(v, str) else ""


def _dict(v) -> dict:
    return v if isinstance(v, dict) else {}


def _text_size(content) -> int:
    """Length of a tool result's content as text (a string, or the text parts of a list)."""
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(b["text"]) for b in content if isinstance(b, dict) and isinstance(b.get("text"), str))
    return 0


def _tool_error(block: dict, result) -> bool | None:
    """Whether a tool call failed: the block's bool `is_error`, else derived from the record's tool result.

    Real sessions set `is_error` on the `tool_result` block only for some tools (Bash); for the others it is absent.
    The record-level `toolUseResult` (session) / `tool_use_result` (stream) is then a string for an error, and a
    dict or a list for a success; anything else (including a missing field) leaves the outcome unknown.
    """
    err = block.get("is_error")
    if isinstance(err, bool):
        return err
    if isinstance(result, str):
        return True
    return False if isinstance(result, (dict, list)) else None


def _usage_data(key: tuple[str, str], u: dict) -> dict:
    cc = u.get("cache_creation")
    if isinstance(cc, dict):
        w5, w1 = _int(cc.get("ephemeral_5m_input_tokens")), _int(cc.get("ephemeral_1h_input_tokens"))
    else:  # no split reported: the whole write counts as the default 5 minute tier
        w5, w1 = _int(u.get("cache_creation_input_tokens")), 0
    return {"key": key, "input": _int(u.get("input_tokens")), "cache_read": _int(u.get("cache_read_input_tokens")),
            "cache_write_5m": w5, "cache_write_1h": w1, "output": _int(u.get("output_tokens")), "complete": True}


class _Stream:
    """State of one `claude_stream` pass: output, de-duplication and context seen in `system/init`."""

    def __init__(self) -> None:
        self.out = Parsed()
        self.usage_at: dict[tuple[str, str], int] = {}  # usage key -> index of its event in `out.events`
        self.cwd: str | None = None
        self.version: str | None = None
        self.last_result: dict | None = None
        self.last_session = ""

    def make(self, kind: str, rec: dict, data: dict, model: str | None = None) -> Event:
        parent = _str(rec.get("parent_tool_use_id"))
        sid = _str(rec.get("session_id")) or _str(rec.get("sessionId")) or self.last_session
        return Event(
            kind=kind, ts=rec.get("timestamp") if isinstance(rec.get("timestamp"), str) else None, session_id=sid,
            source="stream", model=model, agent_id=_str(rec.get("agent_id")), parent_tool_use_id=parent,
            sidechain=bool(parent) or rec.get("isSidechain") is True,
            cwd=_str(rec.get("cwd")) or self.cwd or None, git_branch=_str(rec.get("gitBranch")) or None,
            cli_version=self.version, data=data, raw=rec)

    def event(self, kind: str, rec: dict, data: dict, model: str | None = None) -> None:
        self.out.events.append(self.make(kind, rec, data, model))

    def count_unknown(self, name: str) -> None:
        self.out.unknown[name] = self.out.unknown.get(name, 0) + 1

    def record(self, rec: dict) -> None:
        kind = rec.get("type")
        if isinstance(rec.get("session_id"), str):
            self.last_session = rec["session_id"]
        if kind == "assistant":
            self.assistant(rec)
        elif kind == "user":
            self.user(rec)
        elif kind == "system":
            self.system(rec)
        elif kind == "result":
            self.last_result = rec
        elif kind not in _IGNORED_TYPES:
            self.count_unknown(kind if isinstance(kind, str) and kind else "?")

    def assistant(self, rec: dict) -> None:
        msg = _dict(rec.get("message"))
        model = _str(msg.get("model")) or None
        mid = _str(msg.get("id"))
        key = (mid, _str(rec.get("request_id")) or _str(rec.get("requestId")))
        if isinstance(msg.get("usage"), dict):
            # the stream repeats one call on every content-block record, an early one with a partial usage: keep
            # the best copy with the same rules as the session parser and `load` (`_better`)
            data = _usage_data(key, msg["usage"])
            data["complete"] = msg.get("stop_reason") is not None
            ev = self.make("usage", rec, data, model)
            at = self.usage_at.get(key) if mid else None
            if at is None:
                self.usage_at[key] = len(self.out.events)
                self.out.events.append(ev)
            elif _better((len(self.out.events), ev), (at, self.out.events[at])):
                self.out.events[at] = ev
        content = msg.get("content")
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                self.event("tool_call", rec, {"id": _str(block.get("id")), "name": _str(block.get("name")),
                                              "input": _dict(block.get("input"))}, model)
            elif block.get("type") == "text":
                self.event("message", rec, {"role": "assistant", "length": len(_str(block.get("text")))}, model)

    def user(self, rec: dict) -> None:
        content = _dict(rec.get("message")).get("content")
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                self.event("tool_result", rec, {"id": _str(block.get("tool_use_id")),
                                                "is_error": _tool_error(block, rec.get("tool_use_result")),
                                                "size": _text_size(block.get("content"))})

    def system(self, rec: dict) -> None:
        sub = rec.get("subtype")
        if sub == "init":
            self.cwd = _str(rec.get("cwd")) or self.cwd
            self.version = _str(rec.get("claude_code_version")) or self.version
        elif sub == "compact_boundary":
            meta = _dict(rec.get("compact_metadata"))
            self.event("compaction", rec, {"trigger": _str(meta.get("trigger")),
                                           "pre_tokens": _int(meta.get("pre_tokens")),
                                           "post_tokens": _int(meta.get("post_tokens")),
                                           "duration_ms": _int(meta.get("duration_ms"))})
        elif sub not in _IGNORED_SYSTEM:
            self.count_unknown(f"system/{sub}" if isinstance(sub, str) and sub else "system")

    def finish(self) -> None:
        """Emit the session total from the last `result` (its figures are cumulative)."""
        rec = self.last_result
        if rec is None:
            return
        models = {}
        for name, u in _dict(rec.get("modelUsage")).items():
            u = _dict(u)
            models[name] = {"input": _int(u.get("inputTokens")), "cache_read": _int(u.get("cacheReadInputTokens")),
                            "cache_write": _int(u.get("cacheCreationInputTokens")),
                            "output": _int(u.get("outputTokens")), "cost_usd": _num(u.get("costUSD"))}
            if "cacheCreationEphemeral5mInputTokens" in u or "cacheCreationEphemeral1hInputTokens" in u:
                models[name]["cache_write_5m"] = _int(u.get("cacheCreationEphemeral5mInputTokens"))
                models[name]["cache_write_1h"] = _int(u.get("cacheCreationEphemeral1hInputTokens"))
        self.event("session_total", rec, {"models": models, "cost_usd": _num(rec.get("total_cost_usd")),
                                              "since_ms": None})


def _read(path: Path, st) -> Parsed:
    """Feed a JSONL file line by line to `st.record`, then `st.finish`; never raises on input."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except (ValueError, RecursionError):  # not JSON, or nested too deep for the parser
                    rec = None
                if not isinstance(rec, dict):
                    st.out.broken += 1
                    continue
                try:
                    st.record(rec)
                except Exception:  # an odd record must never stop the read
                    st.out.broken += 1
    except OSError:
        return st.out
    try:
        st.finish()
    except Exception:
        st.out.broken += 1
    return st.out


def claude_stream(path: Path) -> Parsed:
    """Parse a Claude Code `stream-json` log into events.

    Args:
        path: The file written by `claude -p --output-format stream-json --verbose`.

    Returns:
        The events in file order (the `session_total` last), counts of unknown record types, and the number of
        lines that were not JSON objects. An unreadable file yields an empty result.
    """
    return _read(path, _Stream())


# Codex records understood but not kept: turn start/failure, error notices, in-flight item updates; and item types
# without a counterpart event (reasoning text is never read).
_CODEX_IGNORED = {"turn.started", "turn.failed", "error", "item.started", "item.updated"}
_CODEX_IGNORED_ITEMS = {"reasoning"}


class _CodexStream:
    """State of one `codex_stream` pass: thread id, turn counter and the running token sum."""

    def __init__(self) -> None:
        self.out = Parsed()
        self.thread = ""  # events before `thread.started` carry ""
        self.model: str | None = None
        self.turns = 0
        self.sum = {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0}

    def event(self, kind: str, rec: dict, data: dict) -> None:
        self.out.events.append(Event(kind=kind, ts=None, session_id=self.thread, source="codex", model=self.model,
                                     data=data, raw=rec))

    def count_unknown(self, name: str) -> None:
        self.out.unknown[name] = self.out.unknown.get(name, 0) + 1

    def record(self, rec: dict) -> None:
        kind = rec.get("type")
        if _str(rec.get("model")):
            self.model = rec["model"]
        if kind == "thread.started":
            self.thread = _str(rec.get("thread_id")) or self.thread
        elif kind == "item.completed":
            self.item(rec, _dict(rec.get("item")))
        elif kind == "turn.completed":
            self.turn(rec)
        elif kind not in _CODEX_IGNORED:
            self.count_unknown(kind if isinstance(kind, str) and kind else "?")

    def item(self, rec: dict, item: dict) -> None:
        kind = item.get("type")
        if kind == "command_execution":
            iid = _str(item.get("id"))
            code = item.get("exit_code")
            if isinstance(code, int) and not isinstance(code, bool):
                failed = code != 0
            else:  # no exit code: only an explicit failed status says something
                failed = True if item.get("status") == "failed" else None
            self.event("tool_call", rec, {"id": iid, "name": "Bash", "input": {"command": _str(item.get("command"))}})
            self.event("tool_result", rec, {"id": iid, "size": len(_str(item.get("aggregated_output"))),
                                            "is_error": failed})
        elif kind == "agent_message":
            self.event("message", rec, {"role": "assistant", "length": len(_str(item.get("text")))})
        elif kind == "file_change":
            self.file_change(rec, item)
        elif kind not in _CODEX_IGNORED_ITEMS:
            self.count_unknown(f"item.completed/{kind}" if isinstance(kind, str) and kind else "item.completed")

    def file_change(self, rec: dict, item: dict) -> None:
        """One `Edit` call and its result per changed file; the result says whether the whole change completed."""
        changes = item.get("changes")
        status = item.get("status")
        failed = False if status == "completed" else True if status == "failed" else None
        for i, change in enumerate(changes if isinstance(changes, list) else []):
            path = _str(_dict(change).get("path"))
            if not path:
                continue
            cid = f"{_str(item.get('id'))}:{i}"
            self.event("tool_call", rec, {"id": cid, "name": "Edit", "input": {"file_path": path}})
            self.event("tool_result", rec, {"id": cid, "size": 0, "is_error": failed})

    def turn(self, rec: dict) -> None:
        u = rec.get("usage")
        if not isinstance(u, dict):
            return
        cached, write = _int(u.get("cached_input_tokens")), _int(u.get("cache_write_input_tokens"))
        data = {"key": (self.thread, self.turns), "input": max(0, _int(u.get("input_tokens")) - cached - write),
                "cache_read": cached, "cache_write_5m": write, "cache_write_1h": 0,
                "output": _int(u.get("output_tokens")), "complete": True}
        self.turns += 1
        self.event("usage", rec, data)
        self.sum["input"] += data["input"]
        self.sum["cache_read"] += cached
        self.sum["cache_write"] += write
        self.sum["output"] += data["output"]

    def finish(self) -> None:
        """Emit the session total (sum of the turns) when at least one turn reported usage."""
        if self.turns:
            self.event("session_total", {}, {"models": {self.model or "unknown": {**self.sum, "cost_usd": None}},
                                             "cost_usd": None, "since_ms": None})


def codex_stream(path: Path) -> Parsed:
    """Parse a `codex exec --json` log into events.

    `session_id` is the `thread.started` thread id; events before it (normally none) carry "". Codex logs no
    timestamps, so `ts` is None. Token counts are normalised to Claude's convention (see the module docstring), the
    model is set only when a record carries a `model` field (usually none, so the total's model is "unknown"), and
    costs are unknown (None). A `file_change` item becomes one `Edit` tool call (input `file_path`) plus one tool
    result per changed file; the result's `is_error` is False for status `completed`, True for `failed`, else None.

    Args:
        path: The file written by `codex exec --json` (run with stdin closed).

    Returns:
        The events in file order (the `session_total` last, only when some turn reported usage), counts of unknown
        record types (`item.completed/<type>` for unknown item types), and the number of lines that were not JSON
        objects (a truncated last line counts here). An unreadable file yields an empty result.
    """
    return _read(path, _CodexStream())


# Interactive session records understood but not kept; most of them hold content (prompts, tool lists, request
# bodies) that is deliberately never read.
_SESSION_IGNORED = {"file-history-snapshot", "file-history-delta", "api-request-blob", "api-request",
                    "api-request-shape", "queue-operation", "session-owner", "atis-latch", "tools-used", "mode",
                    "permission-mode", "ai-title", "last-prompt", "attachment", "summary",
                    "bridge-session", "pr-link", "agent-setting", "agent-name", "continued-in", "fork-context-ref"}
_SESSION_IGNORED_SYSTEM = {"turn_duration", "away_summary", "local_command", "informational", "scheduled_task_fire",
                           "bridge_status", "agents_killed"}


class _SessionStream:
    """State of one interactive session file (the main file or one sub-agent file)."""

    def __init__(self, session_id: str, agent_id: str = "", parent: str = "", agent_type: str = "") -> None:
        self.out = Parsed()
        self.session_id = session_id
        self.agent_id, self.parent, self.agent_type = agent_id, parent, agent_type
        self.usage_at: dict[tuple[str, str], int] = {}  # usage key -> index of its event in `out.events`
        self.last_cost: dict | None = None

    def make(self, kind: str, rec: dict, data: dict, model: str | None = None) -> Event:
        return Event(
            kind=kind, ts=rec.get("timestamp") if isinstance(rec.get("timestamp"), str) else None,
            session_id=_str(rec.get("sessionId")) or self.session_id, source="session", model=model,
            agent_id=self.agent_id, parent_tool_use_id=self.parent, agent_type=self.agent_type,
            sidechain=bool(self.agent_id) or rec.get("isSidechain") is True, cwd=_str(rec.get("cwd")) or None,
            git_branch=_str(rec.get("gitBranch")) or None, cli_version=_str(rec.get("version")) or None, data=data,
            raw=rec)

    def event(self, kind: str, rec: dict, data: dict, model: str | None = None) -> None:
        self.out.events.append(self.make(kind, rec, data, model))

    def count_unknown(self, name: str) -> None:
        self.out.unknown[name] = self.out.unknown.get(name, 0) + 1

    def record(self, rec: dict) -> None:
        kind = rec.get("type")
        if kind == "assistant":
            self.assistant(rec)
        elif kind == "user":
            self.user(rec)
        elif kind == "system":
            self.system(rec)
        elif kind == "cost-state":
            self.last_cost = rec
        elif kind not in _SESSION_IGNORED:
            self.count_unknown(kind if isinstance(kind, str) and kind else "?")

    def assistant(self, rec: dict) -> None:
        msg = _dict(rec.get("message"))
        model = _str(msg.get("model")) or None
        mid = _str(msg.get("id"))
        if isinstance(msg.get("usage"), dict) and model != "<synthetic>":
            key = (mid, _str(rec.get("requestId")) or _str(rec.get("request_id")))
            data = _usage_data(key, msg["usage"])
            data["complete"] = msg.get("stop_reason") is not None
            ev = self.make("usage", rec, data, model)
            at = self.usage_at.get(key) if mid else None
            if at is None:
                self.usage_at[key] = len(self.out.events)
                self.out.events.append(ev)
            elif _better((len(self.out.events), ev), (at, self.out.events[at])):  # the same rules `load` applies
                self.out.events[at] = ev
        content = msg.get("content")
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                self.event("tool_call", rec, {"id": _str(block.get("id")), "name": _str(block.get("name")),
                                              "input": _dict(block.get("input"))}, model)
            elif block.get("type") == "text":
                self.event("message", rec, {"role": "assistant", "length": len(_str(block.get("text")))}, model)

    def user(self, rec: dict) -> None:
        content = _dict(rec.get("message")).get("content")
        if isinstance(content, str):
            self.event("message", rec, {"role": "user", "length": len(content)})
            return
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                self.event("tool_result", rec, {"id": _str(block.get("tool_use_id")),
                                                "is_error": _tool_error(block, rec.get("toolUseResult")),
                                                "size": _text_size(block.get("content"))})
            elif block.get("type") == "text":
                self.event("message", rec, {"role": "user", "length": len(_str(block.get("text")))})

    def system(self, rec: dict) -> None:
        sub = rec.get("subtype")
        if sub == "compact_boundary":
            meta = _dict(rec.get("compactMetadata"))
            self.event("compaction", rec, {"trigger": _str(meta.get("trigger")),
                                           "pre_tokens": _int(meta.get("preTokens")),
                                           "post_tokens": _int(meta.get("postTokens")),
                                           "duration_ms": _int(meta.get("durationMs"))})
        elif sub not in _SESSION_IGNORED_SYSTEM:
            self.count_unknown(f"system/{sub}" if isinstance(sub, str) and sub else "system")

    def finish(self) -> None:
        """Emit the session total from the last `cost-state` record (main file only).

        Its figures cover only the last process run, which began at the record's `startTime` (`since_ms`); a session
        resumed many times has far more usage before that.
        """
        rec = self.last_cost
        if rec is None or self.agent_id:
            return
        models = {}
        for name, u in _dict(rec.get("modelUsage")).items():
            u = _dict(u)
            models[name] = {"input": _int(u.get("inputTokens")), "cache_read": _int(u.get("cacheReadInputTokens")),
                            "cache_write": _int(u.get("cacheCreationInputTokens")),
                            "output": _int(u.get("outputTokens")), "cost_usd": _num(u.get("costUSD"))}
        since = rec.get("startTime")
        self.event("session_total", rec, {"models": models, "cost_usd": _num(rec.get("totalCostUSD")),
                                          "since_ms": since if isinstance(since, int) and not isinstance(since, bool)
                                          else None})


def _sub_meta(path: Path) -> dict:
    """Content of `agent-<id>.meta.json` next to a sub-agent file; {} when missing or malformed."""
    try:
        with open(path.with_name(path.name.removesuffix(".jsonl") + ".meta.json"), encoding="utf-8") as fh:
            return _dict(json.load(fh))
    except (OSError, ValueError, RecursionError):
        return {}


def claude_session(path: Path) -> Parsed:
    """Parse an interactive Claude Code session file and its sub-agent files into events.

    Reads `<session>.jsonl` and `<session>/subagents/agent-<id>.jsonl` (never `<session>/tool-results/`). Sub-agent
    events carry `agent_id` (from the file name), `parent_tool_use_id` and `agent_type` (from the neighbouring
    `agent-<id>.meta.json`, "" when it is missing or malformed) and `sidechain=True`.

    One model call (message id, request id) yields one `usage` event per file even when several records repeat it:
    the one with a `stop_reason` (`complete`) wins, otherwise the later one. De-duplication across files is left to
    the caller. `<synthetic>` records yield no usage. The `session_total` comes from the last `cost-state` record of
    the main file and is absent when there is none; it counts only the last process run, since `since_ms` (the
    record's `startTime`, None when missing), so compare it with usage events whose `ts` is at or after that time.

    Args:
        path: The main session file.

    Returns:
        The events (main file first, then sub-agents sorted by file name), counts of unknown record types summed over
        all files, and the number of unparsable lines. An unreadable main file yields no events of its own.
    """
    out = _read(path, _SessionStream(path.stem))
    subs = path.with_suffix("") / "subagents"
    try:
        files = sorted(f for f in subs.glob("agent-*.jsonl") if f.is_file())
    except OSError:
        files = []
    for f in files:
        meta = _sub_meta(f)
        part = _read(f, _SessionStream(path.stem, f.stem.removeprefix("agent-"), _str(meta.get("toolUseId")),
                                       _str(meta.get("agentType"))))
        out.events.extend(part.events)
        out.broken += part.broken
        for name, n in part.unknown.items():
            out.unknown[name] = out.unknown.get(name, 0) + n
    return out


def encode_path(path: Path) -> str:
    """Directory name Claude Code uses under `~/.claude/projects` for a working directory."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def project_dirs(contest: Path, projects: Path = Path("~/.claude/projects").expanduser()) -> list[Path]:
    """Project directories that may hold sessions of a contest directory.

    A directory matches when its name starts with the encoded path of the contest or of its `realpath` (so a
    symlinked contest is found under both, and `<contest>/.worktrees/...` directories match by prefix). The prefix
    also matches siblings such as `<contest>-old`; `contest_sessions` tells them apart by `cwd`.

    Args:
        contest: The contest directory (may be a symlink).
        projects: The directory holding the per-project directories.

    Returns:
        The matching directories, sorted; empty when `projects` is missing.
    """
    prefixes = {encode_path(Path(os.path.abspath(contest))), encode_path(Path(os.path.realpath(contest)))}
    try:
        return sorted(d for d in projects.iterdir() if d.is_dir() and d.name.startswith(tuple(prefixes)))
    except OSError:
        return []


def first_cwd(path: Path) -> str | None:
    """The `cwd` of the first record that has one, None when there is none; never raises."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                if isinstance(rec, dict) and isinstance(rec.get("cwd"), str) and rec["cwd"]:
                    return rec["cwd"]
    except OSError:
        pass
    return None


def contest_sessions(contest: Path, projects: Path = Path("~/.claude/projects").expanduser()) -> list[Path]:
    """Main session files that belong to a contest directory.

    Looks at `*.jsonl` directly in each of `project_dirs` (sub-agent files live deeper and are read through their
    main file). A file belongs to the contest when the first record with a `cwd` resolves, symlinks included, to the
    contest root or a directory below it (worktrees under the contest included).

    Args:
        contest: The contest directory (may be a symlink).
        projects: The directory holding the per-project directories.

    Returns:
        The session files, sorted by directory and name.
    """
    root = Path(os.path.realpath(contest))
    found = []
    for d in project_dirs(contest, projects):
        try:
            files = sorted(f for f in d.glob("*.jsonl") if f.is_file())
        except OSError:
            continue
        for f in files:
            cwd = first_cwd(f)
            if cwd is None:
                continue
            real = Path(os.path.realpath(cwd))
            if real == root or root in real.parents:
                found.append(f)
    return found


_LOADERS = {"stream": claude_stream, "codex": codex_stream, "session": claude_session}


def _better(new: tuple[int, Event], old: tuple[int, Event]) -> bool:
    """Whether the usage event `new` (input position, event) should replace the duplicate `old`.

    Order of rules: a non-sidechain copy beats a sidechain one; a complete record beats an incomplete one; the later
    one wins (by `ts`, by input position when either has no `ts` or they are equal); remaining ties go to the higher
    `output`.
    """
    (ni, n), (oi, o) = new, old
    if n.sidechain != o.sidechain:
        return not n.sidechain
    nc, oc = bool(n.data.get("complete")), bool(o.data.get("complete"))
    if nc != oc:
        return nc
    if n.ts and o.ts:
        if n.ts != o.ts:
            return n.ts > o.ts
        if _int(n.data.get("output")) != _int(o.data.get("output")):
            return _int(n.data.get("output")) > _int(o.data.get("output"))
    return ni > oi


def collect(sources: list[tuple[str, Path]]) -> Parsed:
    """Parse several transcript sources without the global `usage` de-duplication.

    Args:
        sources: As for `load`.

    Returns:
        The events of all sources in input order (a `session` item whose id equals the id of a `stream` item is
        dropped as in `load`), `unknown` and `broken` summed. Each source is already de-duplicated by its own parser,
        but a call that several sources hold is still there once per source; `dedupe` collapses those.
    """
    parsed: list[tuple[str, Path, Parsed]] = []
    out = Parsed()
    for kind, path in sources:
        loader = _LOADERS.get(kind)
        if loader is None:
            name = f"source/{kind}"
            out.unknown[name] = out.unknown.get(name, 0) + 1
            continue
        parsed.append((kind, Path(path), loader(Path(path))))
    stream_ids = {e.session_id for kind, _, p in parsed if kind == "stream" for e in p.events if e.session_id}
    for kind, path, p in parsed:
        if kind == "session" and path.stem in stream_ids:
            continue
        out.events.extend(p.events)
        out.broken += p.broken
        for name, n in p.unknown.items():
            out.unknown[name] = out.unknown.get(name, 0) + n
    return out


def dedupe(events: list[Event]) -> list[Event]:
    """Count every model call once: keep the best `usage` event per key (see `_better`) and every other event.

    Args:
        events: Events in input order, from one session or from many.

    Returns:
        The events without the losing `usage` copies, in input order. A `usage` event without a message / thread id
        in its key is never dropped.
    """
    best: dict = {}
    for i, e in enumerate(events):
        if not _deduped(e):
            continue  # no message id / thread id: the parsers cannot tell calls apart either, so nothing collapses
        key = e.data["key"]
        if key not in best or _better((i, e), best[key]):
            best[key] = (i, e)
    keep = {id(e) for _, e in best.values()}
    return [e for e in events if id(e) in keep or not _deduped(e)]


def load(sources: list[tuple[str, Path]]) -> Parsed:
    """Parse several transcript sources and count every model call once.

    Args:
        sources: Items of (kind, path) with kind `stream` (`claude_stream`), `codex` (`codex_stream`) or `session`
            (`claude_session`). A `session` item whose session id (the file name without `.jsonl`) equals the session
            id of a `stream` item is the same run seen twice and is dropped entirely, including its unknown and broken
            counts. An item of another kind is skipped and counted in `unknown` as `source/<kind>`.

    Returns:
        The events of all sources in input order, `unknown` and `broken` summed. `usage` events are de-duplicated
        by `data["key"]` across all sources: a non-sidechain copy beats a sidechain one, then a complete record beats
        an incomplete one, then the later one wins (by `ts`, falling back to input order), then the higher `output`.
        The other events are never de-duplicated.
    """
    out = collect(sources)
    out.events = dedupe(out.events)
    return out


def _deduped(e: Event) -> bool:
    key = e.data.get("key") if e.kind == "usage" else None
    return isinstance(key, tuple) and bool(key) and bool(key[0])


def _result_block_text(content) -> str:
    """A tool result's content as text: the string itself, or the joined text parts of a list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b["text"] for b in content if isinstance(b, dict) and isinstance(b.get("text"), str))
    return ""


def result_text(event: Event) -> str:
    """The text of a `tool_result` event, read back from its raw source record.

    In memory only, for callers that must look into a result (for example to check an error message); `view` never
    uses it. The text is the tool's own output and can hold prompts, file contents and secrets: it is content, so
    never write it out unredacted (pass it through `guard.redact` and cut it first).

    Args:
        event: Any event. Only a `tool_result` that still has its `raw` record yields text.

    Returns:
        For `stream` and `session` events the `content` of the tool_result block (a string, or the text parts of a
        list joined); for `codex` events the item's `aggregated_output`. "" for any other event, a missing or odd
        `raw`, or content that is not text.
    """
    raw = event.raw
    if event.kind != "tool_result" or not isinstance(raw, dict):
        return ""
    if event.source == "codex":
        return _str(_dict(raw.get("item")).get("aggregated_output"))
    content = _dict(raw.get("message")).get("content")
    blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"] \
        if isinstance(content, list) else []
    want = _dict(event.data).get("id")
    block = next((b for b in blocks if b.get("tool_use_id") == want), blocks[0] if len(blocks) == 1 else None)
    return _result_block_text(block.get("content")) if block else ""


_BASH_LIMIT = 300
_PATH_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "Read"}


def _s(v) -> str:
    """A string field as it may leave the process: redacted; "" for anything that is not a string."""
    return redact(v) if isinstance(v, str) else ""


def _s_or_none(v) -> str | None:
    return redact(v) if isinstance(v, str) else None


def _scalar(v):
    """A number or bool as it is, None for everything else (a hand-built event can hold anything)."""
    return v if v is None or isinstance(v, (int, float, bool)) else None


def _count(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _flag(v) -> bool | None:
    return v if isinstance(v, bool) else None


def _tool_input(name: str, inp, bash_limit: int | None = _BASH_LIMIT) -> dict:
    """The whitelisted part of a tool call's input, every string redacted; never prompts, file contents or edits.

    Beyond strings, only counts reach the view: `Read` keeps `offset`, `limit` (integers) and `allow_large` (bool) when
    given, `Agent`/`Task` get `prompt_chars`, the length of the brief (a number, the text stays out).

    A Bash command is cut to `bash_limit` characters after redaction (None: whole command)."""
    inp = _dict(inp)

    def pick(*fields: str) -> dict:
        return {f: redact(inp[f]) for f in fields if isinstance(inp.get(f), str)}

    if name == "Bash":
        cmd = inp.get("command")
        return {"command": redact(cmd)[:bash_limit]} if isinstance(cmd, str) else {}
    if name in _PATH_TOOLS:
        path = inp.get("file_path")
        if not isinstance(path, str) and name == "NotebookEdit":
            path = inp.get("notebook_path")
        out = {"file_path": redact(path)} if isinstance(path, str) else {}
        if name == "Read" and out:  # how much of the file is read: numbers and a flag only (K3 eval forbids)
            out.update({f: inp[f] for f in ("offset", "limit") if _count(inp.get(f)) is not None})
            if _flag(inp.get("allow_large")) is not None:
                out["allow_large"] = inp["allow_large"]
        return out
    if name in ("Grep", "Glob"):
        return pick("pattern", "path")
    if name in ("Agent", "Task"):
        out = pick("subagent_type", "description")
        if isinstance(inp.get("prompt"), str):  # the size of the brief, never its text (K3 eval forbids)
            out["prompt_chars"] = len(inp["prompt"])
        return out
    if name == "Skill":
        skill = next((inp[f] for f in ("skill", "name") if isinstance(inp.get(f), str)), None)
        return {"skill": redact(skill)} if skill is not None else {}
    return {}


def _usage_view(d: dict) -> dict:
    key = d.get("key")
    ok = isinstance(key, (tuple, list)) and all(
        isinstance(k, str) or (isinstance(k, int) and not isinstance(k, bool)) for k in key)
    data = {f: _scalar(d.get(f)) for f in ("input", "cache_read", "cache_write_5m", "cache_write_1h", "output")}
    data["complete"] = _flag(d.get("complete"))
    data["key"] = [redact(k) if isinstance(k, str) else k for k in key] if ok else None
    return data


def _view_data(kind: str, d: dict, bash_limit: int | None = _BASH_LIMIT) -> dict:
    if kind == "tool_call":
        name = _s(d.get("name"))
        return {"id": _s(d.get("id")), "name": name, "input": _tool_input(_str(d.get("name")), d.get("input"), bash_limit)}
    if kind == "tool_result":
        return {"id": _s(d.get("id")), "is_error": _flag(d.get("is_error")), "size": _count(d.get("size"))}
    if kind == "message":
        return {"role": _s(d.get("role")), "length": _count(d.get("length"))}
    if kind == "usage":
        return _usage_view(d)
    if kind == "compaction":
        return {"trigger": _s(d.get("trigger")), "pre_tokens": _scalar(d.get("pre_tokens")),
                "post_tokens": _scalar(d.get("post_tokens")), "duration_ms": _scalar(d.get("duration_ms"))}
    if kind == "session_total":
        fields = ("input", "cache_read", "cache_write", "output", "cost_usd")
        return {"models": {redact(m): {f: _scalar(_dict(v).get(f)) for f in fields}
                           for m, v in _dict(d.get("models")).items() if isinstance(m, str)},
                "cost_usd": _scalar(d.get("cost_usd")), "since_ms": _count(d.get("since_ms"))}
    return {}


def view(event: Event, bash_limit: int | None = _BASH_LIMIT) -> dict:
    """The only serialisable form of an event: the privacy boundary.

    Contains nothing but the fields below, built from explicit keys, so neither `raw` nor an unexpected `data` key
    can get through, and always JSON-serialisable whatever a hand-built event holds. Every string goes through
    `guard.redact`; numbers and booleans pass, anything of another type becomes "" (ids, names) or None (numbers).
    `data` by kind: `tool_call` id, name and a whitelisted `input` (see `_tool_input`; Bash commands are redacted
    first and cut to 300 characters after, so a cut never exposes half a secret; `Read` also `offset`/`limit`/`allow_large`
    and `Agent`/`Task` also `prompt_chars`, numbers and a flag only); `tool_result` id, is_error, size;
    `message` role, length; `usage` the token counts, `complete` and the key as a list; `compaction` trigger and token
    counts; `session_total` per-model counts and costs and `since_ms`; an unknown kind yields an empty `data`.

    Args:
        event: Any event produced by the parsers in this module.
        bash_limit: Characters kept of a Bash command (after redaction); None keeps the whole redacted command.
            Only for in-memory matching (the eval grader): never serialise a view made with None.

    Returns:
        A JSON-serialisable dict with `kind`, `ts`, `session_id`, `source`, `model`, `agent_id`,
        `parent_tool_use_id`, `agent_type`, `cli_version` and `data`.
    """
    return {"kind": _s(event.kind), "ts": _s_or_none(event.ts), "session_id": _s(event.session_id),
            "source": _s(event.source), "model": _s_or_none(event.model), "agent_id": _s(event.agent_id),
            "parent_tool_use_id": _s(event.parent_tool_use_id), "agent_type": _s(event.agent_type),
            "cli_version": _s_or_none(event.cli_version),
            "data": _view_data(event.kind, _dict(event.data), bash_limit)}
