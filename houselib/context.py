"""Estimate how much context a Claude Code session in a contest repo always carries.

What the model always sees: AGENTS.md/CLAUDE.md (contest + user), the name and description of every
model-invocable skill, and the description of every subagent. Bodies load only when used.
Estimate only: tokens ≈ characters / 4.
"""
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from .fm import parse_frontmatter


@dataclass
class SkillEntry:
    name: str
    source: str
    path: Path
    tokens: int
    model_invocable: bool


@dataclass
class ContextReport:
    files: list[tuple[str, int]] = field(default_factory=list)
    skills: list[SkillEntry] = field(default_factory=list)
    agents: list[tuple[str, int]] = field(default_factory=list)
    duplicates: dict[str, list[str]] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return (sum(t for _, t in self.files) + sum(e.tokens for e in self.skills if e.model_invocable)
                + sum(t for _, t in self.agents))


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def _frontmatter_block(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i])
    return ""


def _skill_entry(path: Path, source: str) -> SkillEntry:
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
    if not skills_dir.is_dir():
        return []
    return [_skill_entry(p, source) for p in sorted(skills_dir.glob("*/SKILL.md"))]


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def plugin_skill_dirs(home: Path) -> list[tuple[str, Path]]:
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
    try:
        meta, _ = parse_frontmatter(path.read_text(errors="replace"))
        return str(meta.get("name") or path.stem), estimate_tokens(str(meta.get("description") or ""))
    except ValueError:
        return path.stem, estimate_tokens(_frontmatter_block(path.read_text(errors="replace")))


def measure(contest: Path, home: Path) -> ContextReport:
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
