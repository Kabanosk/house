"""Eval case format: `evals/cases/<name>.json`, its validating loader and `case_hash`.

A case describes one scenario for an agent profile (`core/agents/`) or a skill: a fixture contest, a prompt,
the tool calls expected and forbidden in the session log, and predicates on the final state. The loader checks
the whole file up front so a typo fails when the case is loaded, not halfway through a paid run.

Used by: `house eval run|status` (selects cases by `--tag`/`--name`, hashes the file into every result row).
The schema is documented in `evals/cases/README.md`.

Rules an agent should know:
- Every error is a `ValueError` whose message starts with the file name and names the offending field,
  e.g. `merge.json: expect[0].input_match: invalid regex ...`.
- `expect` has superset semantics (other calls are allowed); `forbid` is violated by any matching call. A call with
  `ok: true` counts only when its tool result says it succeeded.
- Labels are unique within a case across `expect` and `forbid`; a call without `label` is named
  `<tool>#<i>`, where `i` is its position in `expect` followed by `forbid`. A label (of a call or an outcome
  predicate) may not start with `outcome[<digits>]`, the form of generated outcome names.
- A call may carry `from_turn` (integer >= 1, at most `1 + len(followups)`): only calls made during that user turn
  or later count (Claude only).
- `case_hash` is the sha256 of the raw file bytes, so any edit (even whitespace) starts a new history.
- `followups` (optional) are further user turns sent after `prompt` in the same session (Claude only; rejected for
  `tool: codex`). The predicate `hidden_tests` (also Claude only) runs a command of the case's own tests in a second container after the
  run (see `runner.run_hidden_tests`); its tests live outside the contest, so the agent never sees them.
- A case names exactly one of `agent` (a profile run with `--agent`), `skill` (the prompt is sent as `/<skill> ...`)
  and `session` (only `"main"`: the main session that reads the contest's CLAUDE.md/AGENTS.md, i.e. the coordinator,
  run without `--agent` and with the prompt as is; it needs `model`).
- `final_answer` may carry `key` (`[A-Za-z0-9._-]{1,32}`): `match` is then applied only to the last line of the
  answer that starts with `<key>:` (see `grade.final_answer_holds`).
- An outcome predicate may carry `label`; it is then the predicate's name in grades, results rows and `house eval
  compare` (`outcome_name`), else `outcome[<i>] <type>`. Labels and those default names are unique across `expect`,
  `forbid` and `outcome`.
- `variants` (optional) names patches of the run's temp root (`settings`, `env`, `edit`, `remove`, `copy`, `prompt`;
  see `Variant`), so one case can be run in several configurations and compared. The name `default` is reserved for "no
  patch"; the patches are applied by `runner.apply_variant` and are only validated here. A variant may also carry
  `followup_text` (`{index, text}`, at most one): it replaces follow-up `index` (0-based) for that variant, so every
  arm keeps the same number of turns; `from_turn` numbering is unchanged.
"""
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

KINDS = ("regression", "capability")
TOOLS = ("claude", "codex")
CODEX_AGENT = "coder-codex"
TASK_STATUSES = ("todo", "doing", "review", "merged", "done", "blocked")
PREDICATES = ("git_branch", "git_merge_commit_on", "git_clean", "git_branch_ahead", "file_exists",
              "file_unchanged", "task_status", "check_state", "open_questions", "final_answer", "hidden_tests")
CHECK_STATES = ("GREEN", "RED", "incomplete", "no check result")
DEFAULT_MAX_TURNS = 40
DEFAULT_TIMEOUT_SECONDS = 900
DEFAULT_HIDDEN_TIMEOUT_SECONDS = 300  # a `hidden_tests` predicate without its own `timeout_seconds`

SESSIONS = ("main",)  # `session`: the main session (coordinator), no `--agent`
_TOP_LEVEL_KEYS = {"agent", "skill", "session", "model", "fixture", "prompt", "expect", "forbid", "order", "outcome",
                   "kind", "max_turns", "timeout_seconds", "tags", "tool", "variants", "followups"}
TEST_ONLY_FIXTURE_PARAMS = ("reference",)  # unit-test-only builder options (`chain_big`, `spec_huge`)
DEFAULT_VARIANT = "default"  # reserved: "no variant", never a key of `variants`
_VARIANT_NAME = re.compile(r"[a-z0-9-]{1,32}")
_VARIANT_KEYS = {"settings", "env", "edit", "remove", "copy", "prompt", "followup_text"}
_EDIT_KEYS = {"path", "pattern", "repl"}
_COPY_KEYS = {"material", "to"}
_FOLLOWUP_TEXT_KEYS = {"index", "text"}
_CALL_KEYS = {"label", "tool", "input_match", "min", "max", "ok", "from_turn"}
_RESERVED_LABEL = re.compile(r"outcome\[\d+\]")  # the start of a generated outcome name (`outcome_name`)
_ANSWER_KEY = re.compile(r"[A-Za-z0-9._-]{1,32}")  # `final_answer.key`: the line `<key>: <answer>`


@dataclass
class Call:
    """An expected or forbidden tool call, as it appears in the session log.

    Attributes:
        label: Name unique within the case, used by `order` and in reports.
        tool: Tool name as in the log (`Bash`, `Edit`, `Skill`, `Agent`, ...).
        input_match: Regex matched with `search` against `json.dumps(viewed_input, sort_keys=True)`.
            The empty string matches every call of the tool.
        min: Least number of matching calls (expect), or the number at which a forbid is violated.
        max: Greatest number of matching calls; None means unbounded. Only allowed in `expect`.
        regex: `input_match` compiled once at load.
        ok: Only calls known to have succeeded count: their `tool_result` exists and its `is_error` is False
            (case field `ok: true`; `false` is not a valid value).
        from_turn: Only calls made during user turn >= `from_turn` count (turn 1 is the prompt, turn k the k-th user
            line; the grader places a call by the `result` events before it); None counts every call.
    """
    label: str
    tool: str
    input_match: str
    min: int
    max: int | None
    regex: re.Pattern = field(repr=False, compare=False)
    ok: bool = False
    from_turn: int | None = None

    def matches(self, viewed_input: Any) -> bool:
        """Tell whether a call's viewed input matches this call's `input_match`.

        Args:
            viewed_input: The tool input as parsed from the session log (JSON-serializable).

        Returns:
            True when the regex finds a match in the sorted-key JSON of the input.
        """
        return self.regex.search(json.dumps(viewed_input, sort_keys=True)) is not None


@dataclass
class Edit:
    """One `re.subn` of a file of the contest.

    Attributes:
        path: File path relative to the contest (no `..`, not absolute).
        pattern: Regex (validated at load, compiled by the runner).
        repl: Replacement text as for `re.sub` (backreferences allowed).
    """
    path: str
    pattern: str
    repl: str


@dataclass
class Copy:
    """One copy of fixture material into the contest.

    Attributes:
        material: Name below `<tmp>/material/` (written by the fixture with `fixtures.write_material`; relative, no
            `..`).
        to: Contest-relative target path; it must not exist yet.
    """
    material: str
    to: str


@dataclass
class FollowupText:
    """A variant's replacement of one follow-up turn (the turn count of the run is unchanged).

    Attributes:
        index: 0-based index into the case's `followups` (turn `index + 2` of the run).
        text: The user turn sent instead, non-empty (for example `/compact` to compact at a work boundary).
    """
    index: int
    text: str


@dataclass
class Variant:
    """A named patch of the run's temp root; applied in the order `remove`, `copy`, `edit`, `settings`, `env`,
    `prompt`.

    Attributes:
        name: Name `[a-z0-9-]{1,32}`, never `default`.
        settings: Deep merge into the contest's `.claude/settings.local.json`; a JSON `null` deletes the key.
        env: Merged into the `env` block of those settings with the same rule (`null` deletes the key).
        edit: `re.subn` edits of contest files; an edit that matches nothing is an `infra_error`.
        remove: Contest-relative paths (files or directory trees) deleted before the run.
        copy: Fixture material copied into the contest (committed with `remove`/`edit`), see `Copy`.
        prompt: Replaces the case prompt; every `{file:<contest-relative path>}` in it is replaced by that file's
            text from the built fixture and every `{material:<name>}` by the text of `<tmp>/material/<name>`
            (material is outside the contest and never in its history). None keeps the case prompt.
        followup_text: Replaces one follow-up turn, see `FollowupText`; None keeps the case follow-ups.
    """
    name: str
    settings: dict = field(default_factory=dict)
    env: dict = field(default_factory=dict)
    edit: list[Edit] = field(default_factory=list)
    remove: list[str] = field(default_factory=list)
    prompt: str | None = None
    copy: list[Copy] = field(default_factory=list)
    followup_text: FollowupText | None = None


@dataclass
class Case:
    """One validated eval case.

    Attributes:
        name: File stem, the case id in results and on the command line.
        path: The case file.
        agent: Profile id from `core/agents/`; exactly one of `agent`, `skill` and `session` is set.
        skill: Skill name from `adapters/claude-code/skills/`.
        model: Model for the run. Required for a skill or session case; None for an agent case means "from the
            profile's `model_tier`", resolved by the runner.
        fixture: `{"name": <builder>, **params}` for the fixture builder.
        prompt: Text sent to the agent.
        expect: Calls that must occur (superset semantics).
        forbid: Calls that must not occur.
        order: `(before, after)` label pairs, compared by first occurrence in the log.
        outcome: Final-state predicates, each `{"type": <kind>, **params}` with an optional `label` (its name,
            see `outcome_name`).
        kind: `regression` (blocks) or `capability` (never blocks).
        max_turns: Turn limit; exceeding it is a `fail`.
        timeout_seconds: Wall-clock limit; exceeding it is a `fail`.
        tags: Selection tags such as `smoke`.
        tool: CLI that runs the agent, `claude` or `codex` (codex only for `coder-codex`).
        followups: User turns sent after `prompt` in the same session (stream-json input); empty for a one-turn case.
        variants: Named patches of the temp root, `{name: Variant}`; empty when the case has none (then only the
            `default` variant, i.e. no patch, can be run).
        session: `"main"` for a main-session case (the coordinator: no `--agent`, the prompt sent as is), else None.
    """
    name: str
    path: Path
    agent: str | None
    skill: str | None
    model: str | None
    fixture: dict
    prompt: str
    expect: list[Call] = field(default_factory=list)
    forbid: list[Call] = field(default_factory=list)
    order: list[tuple[str, str]] = field(default_factory=list)
    outcome: list[dict] = field(default_factory=list)
    kind: str = "capability"
    max_turns: int = DEFAULT_MAX_TURNS
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    tags: list[str] = field(default_factory=list)
    tool: str = "claude"
    variants: dict[str, Variant] = field(default_factory=dict)
    followups: list[str] = field(default_factory=list)
    session: str | None = None


def outcome_name(i: int, pred: dict) -> str:
    """The name of outcome predicate `i` in grades and results: its `label`, else `outcome[<i>] <type>`."""
    return pred.get("label") or f"outcome[{i}] {pred['type']}"


class _Invalid(Exception):
    """Internal: a field-level validation failure, turned into a `ValueError` naming the file by `load`."""

    def __init__(self, field_name: str, message: str):
        super().__init__(f"{field_name}: {message}")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _Invalid(where, "must be a non-empty string")
    return value


def _integer(value: Any, where: str, minimum: int) -> int:
    if not _is_int(value) or value < minimum:
        raise _Invalid(where, f"must be an integer >= {minimum}")
    return value


def _regex(value: Any, where: str) -> re.Pattern:
    if not isinstance(value, str):
        raise _Invalid(where, "must be a string (regex)")
    try:
        return re.compile(value)
    except re.error as exc:
        raise _Invalid(where, f"invalid regex {value!r}: {exc}") from exc


def _unknown_keys(data: dict, allowed: set[str], where: str) -> None:
    extra = sorted(set(data) - allowed)
    if extra:
        raise _Invalid(where, f"unknown key(s) {', '.join(extra)}; allowed: {', '.join(sorted(allowed))}")


def _parse_calls(raw: Any, section: str, offset: int) -> list[Call]:
    if not isinstance(raw, list):
        raise _Invalid(section, "must be a list")
    is_forbid = section == "forbid"
    calls = []
    for i, item in enumerate(raw):
        where = f"{section}[{i}]"
        if not isinstance(item, dict):
            raise _Invalid(where, "must be an object")
        _unknown_keys(item, _CALL_KEYS, where)
        if "tool" not in item:
            raise _Invalid(where, "missing required field 'tool'")
        tool = _string(item["tool"], f"{where}.tool")
        label = _label(item["label"], f"{where}.label") if "label" in item else f"{tool}#{offset + i}"
        input_match = item.get("input_match", "")
        regex = _regex(input_match, f"{where}.input_match")
        minimum = _integer(item.get("min", 1), f"{where}.min", 1 if is_forbid else 0)
        maximum = item.get("max")
        if is_forbid and maximum is not None:
            raise _Invalid(f"{where}.max", "not allowed in forbid (any match is a violation)")
        if maximum is not None:
            maximum = _integer(maximum, f"{where}.max", 1)
            if maximum < minimum:
                raise _Invalid(f"{where}.max", f"must be >= min ({minimum})")
        if "ok" in item and item["ok"] is not True:
            raise _Invalid(f"{where}.ok", "must be true (or left out)")
        from_turn = _integer(item["from_turn"], f"{where}.from_turn", 1) if "from_turn" in item else None
        calls.append(Call(label, tool, input_match, minimum, maximum, regex, ok="ok" in item, from_turn=from_turn))
    return calls


def _label(value: Any, where: str) -> str:
    """A user-given label: a non-empty string that does not start like a generated outcome name (`outcome[<i>]`)."""
    text = _string(value, where)
    if _RESERVED_LABEL.match(text):
        raise _Invalid(where, f"{text!r} starts like a generated outcome name (outcome[<i>] ...); choose another")
    return text


def _check_params(pred: dict, where: str, kind: str, required: dict[str, type], optional: dict[str, type]) -> None:
    _unknown_keys(pred, {"type", *required, *optional}, where)
    for key, expected in {**required, **optional}.items():
        if key not in pred:
            if key in required:
                raise _Invalid(f"{where}.{key}", f"missing required param for {kind}")
            continue
        value = pred[key]
        if expected is int:
            _integer(value, f"{where}.{key}", 0)
        elif expected is bool:
            if not isinstance(value, bool):
                raise _Invalid(f"{where}.{key}", "must be true or false")
        elif expected is list:
            if not isinstance(value, list) or not value:
                raise _Invalid(f"{where}.{key}", "must be a non-empty list of strings")
            for i, item in enumerate(value):
                _string(item, f"{where}.{key}[{i}]")
        else:
            _string(value, f"{where}.{key}")


def _validate_predicate(raw: Any, where: str) -> dict:
    if not isinstance(raw, dict):
        raise _Invalid(where, "must be an object")
    pred = {k: v for k, v in raw.items() if k != "label"}  # `label` is allowed for every type
    if "label" in raw:
        _label(raw["label"], f"{where}.label")
    kind = pred.get("type")
    if kind == "git_branch":
        _check_params(pred, where, kind, {"name": str, "exists": bool}, {})
    elif kind == "git_merge_commit_on":
        _check_params(pred, where, kind, {"branch": str}, {"message_match": str})
        if "message_match" in pred:
            _regex(pred["message_match"], f"{where}.message_match")
    elif kind == "git_clean":
        _check_params(pred, where, kind, {}, {})
    elif kind == "file_exists":
        _check_params(pred, where, kind, {"path": str, "exists": bool}, {})
    elif kind == "git_branch_ahead":
        _check_params(pred, where, kind, {"branch": str, "min": int}, {})
        _integer(pred["min"], f"{where}.min", 1)
    elif kind == "file_unchanged":
        _check_params(pred, where, kind, {"path": str}, {"ref": str, "ref_optional": bool, "worktree": str})
        if "ref" in pred and "worktree" in pred:
            raise _Invalid(f"{where}.worktree", "cannot be combined with ref (use two predicates)")
        if "ref_optional" in pred and "ref" not in pred:
            raise _Invalid(f"{where}.ref_optional", "needs ref")
    elif kind == "task_status":
        _check_params(pred, where, kind, {"task": str, "status": str}, {})
        if pred["status"] not in TASK_STATUSES:
            raise _Invalid(f"{where}.status", f"must be one of {', '.join(TASK_STATUSES)}")
    elif kind == "check_state":
        _check_params(pred, where, kind, {"task": str, "state": str}, {})
        if pred["state"] not in CHECK_STATES:
            raise _Invalid(f"{where}.state", f"must be one of {', '.join(CHECK_STATES)}")
    elif kind == "final_answer":
        _check_params(pred, where, kind, {"match": str}, {"key": str})
        _regex(pred["match"], f"{where}.match")
        if "key" in pred and not _ANSWER_KEY.fullmatch(pred["key"]):
            raise _Invalid(f"{where}.key", "must be 1-32 letters, digits, '.', '_' or '-' (the line is `<key>: ...`)")
    elif kind == "hidden_tests":
        _check_params(pred, where, kind, {"dir": str, "command": list, "min_passed": int}, {"timeout_seconds": int})
        _contest_relative(pred["dir"], f"{where}.dir", "the hidden tests directory")
        _integer(pred["min_passed"], f"{where}.min_passed", 1)
        if "timeout_seconds" in pred:
            _integer(pred["timeout_seconds"], f"{where}.timeout_seconds", 1)
    elif kind == "open_questions":
        _check_params(pred, where, kind, {}, {"count": int, "min": int, "max": int})
        if "count" in pred and ("min" in pred or "max" in pred):
            raise _Invalid(f"{where}.count", "cannot be combined with min or max")
        if not ({"count", "min", "max"} & set(pred)):
            raise _Invalid(f"{where}.count", "open_questions needs count, or min and/or max")
        if "min" in pred and "max" in pred and pred["min"] > pred["max"]:
            raise _Invalid(f"{where}.min", "must be <= max")
    else:
        raise _Invalid(f"{where}.type", f"unknown predicate {kind!r}; known: {', '.join(PREDICATES)}")
    return dict(raw)


def _contest_relative(value: Any, where: str, inside: str = "the contest") -> str:
    """A non-empty relative path that stays inside `inside` (lexical check; the runner resolves symlinks)."""
    text = _string(value, where)
    parts = text.replace("\\", "/").split("/")
    if text.startswith(("/", "\\")) or re.match(r"[A-Za-z]:", text) or ".." in parts:
        raise _Invalid(where, f"must be a path inside {inside} (relative, no '..'): {text!r}")
    if all(p in ("", ".") for p in parts):
        raise _Invalid(where, f"must name a file or directory inside {inside}, not {inside} itself")
    return text


def _null_map(value: Any, where: str, strings_only: bool) -> dict:
    if not isinstance(value, dict):
        raise _Invalid(where, "must be an object")
    for key, item in value.items():
        if not key:
            raise _Invalid(where, "keys must be non-empty")
        if strings_only and item is not None and not isinstance(item, str):
            raise _Invalid(f"{where}.{key}", "must be a string (or null to delete the key)")
    return dict(value)


def _parse_followup_text(raw: Any, where: str) -> FollowupText:
    if not isinstance(raw, dict):
        raise _Invalid(where, "must be an object {index, text} (at most one per variant)")
    _unknown_keys(raw, _FOLLOWUP_TEXT_KEYS, where)
    for key in sorted(_FOLLOWUP_TEXT_KEYS):
        if key not in raw:
            raise _Invalid(f"{where}.{key}", "missing required field")
    index = _integer(raw["index"], f"{where}.index", 0)
    _string(raw["text"], f"{where}.text")
    return FollowupText(index, raw["text"])


def _parse_variants(raw: Any) -> dict[str, Variant]:
    if not isinstance(raw, dict):
        raise _Invalid("variants", "must be an object {name: {settings, env, edit, remove, copy, prompt, "
                                  "followup_text}}")
    out: dict[str, Variant] = {}
    for name, spec in raw.items():
        where = f"variants.{name}"
        if name == DEFAULT_VARIANT:
            raise _Invalid(where, "'default' is reserved for \"no variant\"")
        if not _VARIANT_NAME.fullmatch(name):
            raise _Invalid(where, "name must match [a-z0-9-]{1,32}")
        if not isinstance(spec, dict):
            raise _Invalid(where, "must be an object")
        _unknown_keys(spec, _VARIANT_KEYS, where)
        settings = _null_map(spec.get("settings", {}), f"{where}.settings", False)
        env = _null_map(spec.get("env", {}), f"{where}.env", True)
        raw_edit = spec.get("edit", [])
        if not isinstance(raw_edit, list):
            raise _Invalid(f"{where}.edit", "must be a list")
        edits = []
        for i, item in enumerate(raw_edit):
            at = f"{where}.edit[{i}]"
            if not isinstance(item, dict):
                raise _Invalid(at, "must be an object {path, pattern, repl}")
            _unknown_keys(item, _EDIT_KEYS, at)
            for key in sorted(_EDIT_KEYS):
                if key not in item:
                    raise _Invalid(f"{at}.{key}", "missing required field")
            pattern = item["pattern"]
            _regex(pattern, f"{at}.pattern")
            if not pattern:
                raise _Invalid(f"{at}.pattern", "must not be empty")
            if not isinstance(item["repl"], str):
                raise _Invalid(f"{at}.repl", "must be a string")
            edits.append(Edit(_contest_relative(item["path"], f"{at}.path"), pattern, item["repl"]))
        raw_remove = spec.get("remove", [])
        if not isinstance(raw_remove, list):
            raise _Invalid(f"{where}.remove", "must be a list of paths")
        remove = [_contest_relative(p, f"{where}.remove[{i}]") for i, p in enumerate(raw_remove)]
        raw_copy = spec.get("copy", [])
        if not isinstance(raw_copy, list):
            raise _Invalid(f"{where}.copy", "must be a list of {material, to}")
        copies = []
        for i, item in enumerate(raw_copy):
            at = f"{where}.copy[{i}]"
            if not isinstance(item, dict):
                raise _Invalid(at, "must be an object {material, to}")
            _unknown_keys(item, _COPY_KEYS, at)
            for key in sorted(_COPY_KEYS):
                if key not in item:
                    raise _Invalid(f"{at}.{key}", "missing required field")
            copies.append(Copy(_contest_relative(item["material"], f"{at}.material", "the material directory"),
                               _contest_relative(item["to"], f"{at}.to")))
        prompt = spec.get("prompt")
        if prompt is not None:
            _string(prompt, f"{where}.prompt")
        out[name] = Variant(name, settings, env, edits, remove, prompt, copies,
                            _parse_followup_text(spec["followup_text"], f"{where}.followup_text")
                            if "followup_text" in spec else None)
    return out


def _parse(data: Any, path: Path) -> Case:
    if not isinstance(data, dict):
        raise _Invalid("(file)", "top level must be a JSON object")
    _unknown_keys(data, _TOP_LEVEL_KEYS, "(file)")

    agent, skill, session = data.get("agent"), data.get("skill"), data.get("session")
    if [agent, skill, session].count(None) != 2:
        raise _Invalid("agent/skill/session", "exactly one of 'agent', 'skill' and 'session' is required")
    if agent is not None:
        _string(agent, "agent")
    if skill is not None:
        _string(skill, "skill")
    if session is not None and session not in SESSIONS:
        raise _Invalid("session", f"must be one of {', '.join(map(repr, SESSIONS))} (the main session)")
    model = data.get("model")
    if model is not None:
        _string(model, "model")
    if skill is not None and model is None:
        raise _Invalid("model", "required for a skill case")
    if session is not None and model is None:
        raise _Invalid("model", "required for a session case")

    if "prompt" not in data:
        raise _Invalid("prompt", "missing required field")
    prompt = _string(data["prompt"], "prompt")
    if "fixture" not in data:
        raise _Invalid("fixture", "missing required field")
    fixture = data["fixture"]
    if not isinstance(fixture, dict):
        raise _Invalid("fixture", "must be an object like {\"name\": <builder>, ...params}")
    _string(fixture.get("name"), "fixture.name")
    for key in TEST_ONLY_FIXTURE_PARAMS:
        if key in fixture:
            raise _Invalid(f"fixture.{key}", "a test-only builder option (the reference solution), never in a case")

    expect = _parse_calls(data.get("expect", []), "expect", 0)
    forbid = _parse_calls(data.get("forbid", []), "forbid", len(expect))
    labels: set[str] = set()
    for call in expect + forbid:
        if call.label in labels:
            raise _Invalid("label", f"duplicate label {call.label!r} (labels are shared by expect and forbid)")
        labels.add(call.label)

    raw_order = data.get("order", [])
    if not isinstance(raw_order, list):
        raise _Invalid("order", "must be a list")
    order = []
    for i, item in enumerate(raw_order):
        where = f"order[{i}]"
        if not isinstance(item, dict):
            raise _Invalid(where, "must be an object {before, after}")
        _unknown_keys(item, {"before", "after"}, where)
        pair = []
        for side in ("before", "after"):
            if side not in item:
                raise _Invalid(f"{where}.{side}", "missing required field")
            label = _string(item[side], f"{where}.{side}")
            if label not in labels:
                raise _Invalid(f"{where}.{side}", f"label {label!r} is not defined in expect/forbid")
            pair.append(label)
        order.append((pair[0], pair[1]))

    raw_outcome = data.get("outcome", [])
    if not isinstance(raw_outcome, list):
        raise _Invalid("outcome", "must be a list")
    outcome = [_validate_predicate(pred, f"outcome[{i}]") for i, pred in enumerate(raw_outcome)]
    for i, pred in enumerate(outcome):
        name = outcome_name(i, pred)
        if name in labels:
            raise _Invalid(f"outcome[{i}].label", f"duplicate label {name!r} (labels are shared by expect, forbid "
                           "and outcome)")
        labels.add(name)

    kind = data.get("kind", "capability")
    if kind not in KINDS:
        raise _Invalid("kind", f"must be one of {', '.join(KINDS)}")
    max_turns = _integer(data.get("max_turns", DEFAULT_MAX_TURNS), "max_turns", 1)
    timeout = _integer(data.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS), "timeout_seconds", 1)

    tags = data.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(t, str) and t for t in tags):
        raise _Invalid("tags", "must be a list of non-empty strings")

    tool = data.get("tool", "claude")
    if tool not in TOOLS:
        raise _Invalid("tool", f"must be one of {', '.join(TOOLS)}")
    if tool == "codex" and agent != CODEX_AGENT:
        raise _Invalid("tool", f"'codex' is only allowed for agent {CODEX_AGENT!r}")

    variants = _parse_variants(data["variants"]) if "variants" in data else {}

    followups = data.get("followups", [])
    if not isinstance(followups, list):
        raise _Invalid("followups", "must be a list of non-empty strings")
    for i, text in enumerate(followups):
        _string(text, f"followups[{i}]")
    for name, variant in variants.items():
        if variant.followup_text is not None and variant.followup_text.index >= len(followups):
            raise _Invalid(f"variants.{name}.followup_text.index",
                           f"must be < {len(followups)} (the case has {len(followups)} follow-up(s))")
    if tool == "codex":
        for i, pred in enumerate(outcome):
            if pred["type"] == "hidden_tests":
                raise _Invalid(f"outcome[{i}]", "hidden_tests is not supported with tool 'codex' (Codex runs on the "
                               "host and can read the whole disk, so the tests would not be hidden)")
    if followups and tool == "codex":
        raise _Invalid("followups", "not supported with tool 'codex' (only Claude takes several user turns)")
    turns = 1 + len(followups)
    for section, calls in (("expect", expect), ("forbid", forbid)):
        for i, call in enumerate(calls):
            if call.from_turn is not None and call.from_turn > turns:
                raise _Invalid(f"{section}[{i}].from_turn", f"must be <= {turns} (the case has {turns} user turn(s))")
            if call.from_turn is not None and tool == "codex":
                raise _Invalid(f"{section}[{i}].from_turn", "not supported with tool 'codex' (one turn only)")

    return Case(name=path.stem, path=path, agent=agent, skill=skill, model=model, fixture=dict(fixture),
                prompt=prompt, expect=expect, forbid=forbid, order=order, outcome=outcome, kind=kind,
                max_turns=max_turns, timeout_seconds=timeout, tags=list(tags), tool=tool, variants=variants,
                followups=list(followups), session=session)


def load(path: Path) -> Case:
    """Read and validate one case file.

    Args:
        path: Path to `evals/cases/<name>.json`.

    Returns:
        The validated `Case`, with every regex compiled.

    Raises:
        ValueError: The file is unreadable, is not valid JSON or breaks the schema; the message starts with
            the file name and names the offending field.
    """
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"{path.name}: cannot read file: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path.name}: invalid JSON: {exc}") from exc
    try:
        return _parse(data, path)
    except _Invalid as exc:
        raise ValueError(f"{path.name}: {exc}") from exc


def load_all(root: Path, tag: str | None = None, name: str | None = None) -> list[Case]:
    """Load every `*.json` case in a directory, optionally filtered.

    With `name`, only that file is read, so an unrelated broken case does not block the run.

    Args:
        root: Directory holding the case files (normally `evals/cases`).
        tag: Keep only cases carrying this tag.
        name: Keep only the case with this name (file stem).

    Returns:
        Matching cases sorted by name.

    Raises:
        ValueError: `root` is not a directory, or a selected case file is invalid.
    """
    root = Path(root)
    if not root.is_dir():
        raise ValueError(f"{root}: not a directory")
    paths = sorted(p for p in root.glob("*.json") if p.is_file() and (name is None or p.stem == name))
    found = [load(p) for p in paths]
    return [c for c in found if tag is None or tag in c.tags]


def case_hash(path: Path) -> str:
    """Hash a case file; results are aggregated only across runs with the same hash.

    Args:
        path: Path to the case file.

    Returns:
        The sha256 hex digest of the file bytes.
    """
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
