"""Estimate how much context a Claude Code session in a contest repo always carries.

What the model always sees: AGENTS.md/CLAUDE.md (contest + user), the name and description of every
model-invocable skill, and the description of every subagent. Bodies load only when used.
Estimate only: tokens ≈ characters / 4.

Used by: `house doctor <contest> --context`, which prints `format_report(measure(...))`. Rules for agents that
write skills or agent profiles: keep `description` short (it is paid in every session), put the detail in the
body, and avoid the same skill name in several places (the report lists duplicates).
"""
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from .fm import parse_frontmatter


@dataclass
class SkillEntry:
    """One skill found on disk.

    Attributes:
        name: Skill name (frontmatter `name`, else its directory name).
        source: Where it lives: "contest", "user" or "plugin:<name>".
        path: Path of its `SKILL.md`.
        tokens: Estimated tokens of name + description (what is always loaded).
        model_invocable: False when `disable-model-invocation: true`; such skills cost nothing in context.
    """
    name: str
    source: str
    path: Path
    tokens: int
    model_invocable: bool


@dataclass
class ContextReport:
    """Result of `measure`.

    Attributes:
        files: `(label, tokens)` of each existing AGENTS.md / CLAUDE.md / ~/.claude/CLAUDE.md.
        skills: Every skill found, in all sources.
        agents: `(name, tokens)` of each subagent description.
        duplicates: Skill name to the sorted sources that define it, for names defined in several places.
    """
    files: list[tuple[str, int]] = field(default_factory=list)
    skills: list[SkillEntry] = field(default_factory=list)
    agents: list[tuple[str, int]] = field(default_factory=list)
    duplicates: dict[str, list[str]] = field(default_factory=dict)

    @property
    def total(self) -> int:
        """Return the estimated always-loaded tokens: files, model-invocable skills and agent descriptions."""
        return (sum(t for _, t in self.files) + sum(e.tokens for e in self.skills if e.model_invocable)
                + sum(t for _, t in self.agents))


def estimate_tokens(text: str) -> int:
    """Estimate the token count of a text.

    Args:
        text: Any text.

    Returns:
        ceil(characters / 4).
    """
    return math.ceil(len(text) / 4)


def _frontmatter_block(text: str) -> str:
    """Return the raw text between the `---` lines at the top of the text, or "" when there is none."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i])
    return ""


def _skill_entry(path: Path, source: str) -> SkillEntry:
    """Build a `SkillEntry` for a SKILL.md; unparseable frontmatter counts as the whole header."""
    text = path.read_text(errors="replace")
    name = path.parent.name
    try:
        meta, _ = parse_frontmatter(text)
        name = str(meta.get("name") or name)
        tokens = estimate_tokens(name + str(meta.get("description") or ""))
        invocable = meta.get("disable-model-invocation") is not True
    except ValueError:  # YAML beyond the flat subset (folded strings etc.): count the whole header
        block = _frontmatter_block(text)
        tokens = estimate_tokens(block)
        invocable = "disable-model-invocation: true" not in block
    return SkillEntry(name, source, path, tokens, invocable)


def skill_entries(skills_dir: Path, source: str) -> list[SkillEntry]:
    """List the skills in a directory.

    Args:
        skills_dir: Directory holding `<skill>/SKILL.md` folders; a missing directory yields no skills.
        source: Label stored in each entry ("contest", "user", "plugin:<name>").

    Returns:
        One `SkillEntry` per skill, sorted by path.
    """
    if not skills_dir.is_dir():
        return []
    return [_skill_entry(p, source) for p in sorted(skills_dir.glob("*/SKILL.md"))]


def _load_json(path: Path) -> dict:
    """Read a JSON object from a file; {} when missing, unreadable or not an object."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def plugin_skill_dirs(home: Path) -> list[tuple[str, Path]]:
    """Find the skills directories of enabled Claude Code plugins.

    Args:
        home: Home directory holding `.claude/plugins/installed_plugins.json` and `.claude/settings.json`.

    Returns:
        `("plugin:<name>", <install path>/skills)` for every installed plugin that is enabled in settings.
    """
    installed = _load_json(home / ".claude/plugins/installed_plugins.json").get("plugins", {})
    enabled = _load_json(home / ".claude/settings.json").get("enabledPlugins", {})
    out = []
    for pid, installs in sorted(installed.items() if isinstance(installed, dict) else []):
        if enabled.get(pid) is not True or not installs or not isinstance(installs, list):
            continue
        path = installs[-1].get("installPath") if isinstance(installs[-1], dict) else None
        if path:
            out.append((f"plugin:{pid.split('@')[0]}", Path(path) / "skills"))
    return out


def _agent_tokens(path: Path) -> tuple[str, int]:
    """Return `(agent name, estimated tokens of its description)` for an agent profile file."""
    try:
        meta, _ = parse_frontmatter(path.read_text(errors="replace"))
        return str(meta.get("name") or path.stem), estimate_tokens(str(meta.get("description") or ""))
    except ValueError:
        return path.stem, estimate_tokens(_frontmatter_block(path.read_text(errors="replace")))


def measure(contest: Path, home: Path) -> ContextReport:
    """Measure the always-loaded context of a contest.

    Args:
        contest: Path of the contest checkout (AGENTS.md, CLAUDE.md, `.claude/skills`, `.claude/agents`).
        home: Home directory to read user-level CLAUDE.md, skills and plugins from (usually `Path.home()`).

    Returns:
        A `ContextReport`.
    """
    r = ContextReport()
    for label, path in (("AGENTS.md", contest / "AGENTS.md"), ("CLAUDE.md", contest / "CLAUDE.md"),
                        ("~/.claude/CLAUDE.md", home / ".claude/CLAUDE.md")):
        if path.is_file():
            r.files.append((label, estimate_tokens(path.read_text(errors="replace"))))
    sources = [("contest", contest / ".claude/skills"), ("user", home / ".claude/skills"),
               *plugin_skill_dirs(home)]
    for source, d in sources:
        r.skills.extend(skill_entries(d, source))
    agents_dir = contest / ".claude/agents"
    if agents_dir.is_dir():
        r.agents = [_agent_tokens(p) for p in sorted(agents_dir.glob("*.md"))]
    seen: dict[str, set[str]] = {}
    for e in r.skills:
        seen.setdefault(e.name, set()).add(e.source)
    r.duplicates = {n: sorted(s) for n, s in sorted(seen.items()) if len(s) > 1}
    return r


def format_report(r: ContextReport) -> str:
    """Render a report as text for `house doctor --context`.

    Args:
        r: The report from `measure`.

    Returns:
        Multi-line text: files, visible skills per source, hidden skills, agents, duplicates and the total.
    """
    lines = ["Always loaded (files):"]
    lines += [f"  {t:>6}  {label}" for label, t in r.files] or ["  (none)"]
    invocable = [e for e in r.skills if e.model_invocable]
    hidden = [e for e in r.skills if not e.model_invocable]
    lines.append(f"Skills visible to the model ({len(invocable)}, name + description):")
    by_source: dict[str, list[SkillEntry]] = {}
    for e in invocable:
        by_source.setdefault(e.source, []).append(e)
    for source, entries in by_source.items():
        lines.append(f"  {sum(e.tokens for e in entries):>6}  {source}: " + ", ".join(e.name for e in entries))
    lines.append(f"Skills hidden from the model (disable-model-invocation): {len(hidden)}")
    lines.append(f"Agents ({len(r.agents)}, descriptions): {sum(t for _, t in r.agents)}")
    lines.append("Duplicates (same skill name in several places):")
    lines += [f"  {n}: {', '.join(s)}" for n, s in r.duplicates.items()] or ["  (none)"]
    lines.append(f"TOTAL ≈ {r.total} tokens (estimate: chars/4)")
    return "\n".join(lines)
