"""Translate the neutral core into tool-specific files (Claude Code, Codex)."""
import json
import shutil
from pathlib import Path

from . import HOUSE_ROOT, doctor
from .fm import parse_frontmatter
from .scaffold import install_core

CLAUDE_MODELS = {"opus": "opus", "sonnet": "sonnet", "haiku": "haiku", "codex": "haiku", "local": "haiku"}

CODEX_DRIVER = """You are a thin driver around Codex CLI (separate usage pool). Do not do the work yourself.
1. Write a prompt file `$HOUSE_DIR/tmp/{id}-<timestamp>.md` containing the instructions below plus your brief
   (or use the task file directly if the brief names one).
2. Run `{house_root}/bin/codex-task <prompt-or-task-file> <workdir>` (add `--read-only` when nothing should be
   edited, e.g. ideation). Wait for it to finish.
3. Verify the result exists (report / ideas rows / acceptance checks) and return a short summary + paths.
   Exit code 3 = codex missing: say so, the coordinator will fall back.

--- Instructions for Codex ---
"""


def load_profiles(root: Path = HOUSE_ROOT) -> dict[str, tuple[dict, str]]:
    out = {}
    for f in sorted((root / "core/agents").glob("*.md")):
        meta, body = parse_frontmatter(f.read_text())
        out[meta["id"]] = (meta, body)
    return out


def resolve_tier(profile: tuple[dict, str], tools: dict, profiles: dict) -> str | None:
    meta = profile[0]
    tier = meta["model_tier"]
    available = {"codex": tools.get("codex"), "local": bool(tools.get("ollama", {}).get("models"))}
    if tier not in available or available[tier]:
        return tier
    fb = meta.get("fallback")
    if fb is None:
        return None
    return resolve_tier(profiles[fb], tools, profiles) if fb in profiles else fb


def _read_tools(contest: Path) -> dict:
    return json.loads((contest / ".house/tools.json").read_text())


def claude_code(contest: Path, root: Path = HOUSE_ROOT) -> list[Path]:
    tools, profiles = _read_tools(contest), load_profiles(root)
    agents_dir = contest / ".claude/agents"
    if agents_dir.exists():
        shutil.rmtree(agents_dir)
    agents_dir.mkdir(parents=True)
    written = []
    for pid, (meta, body) in profiles.items():
        if pid == "coordinator":
            continue  # the main session is the coordinator (AGENTS.md)
        tier = resolve_tier((meta, body), tools, profiles)
        if tier is None or (meta["model_tier"] == "codex" and meta["fallback"] in profiles and tier != "codex"):
            continue  # e.g. coder-codex without codex: plain `coder` already covers it
        if tier == "codex":
            body = CODEX_DRIVER.format(id=pid, house_root=root.resolve()) + body
        desc = str(meta["description"]).replace("\n", " ")
        path = agents_dir / f"{pid}.md"
        path.write_text(f"---\nname: {pid}\ndescription: {desc}\nmodel: {CLAUDE_MODELS[tier]}\n---\n{body}")
        written.append(path)
    skills_dst = contest / ".claude/skills"
    for skill in sorted((root / "adapters/claude-code/skills").iterdir()):
        shutil.copytree(skill, skills_dst / skill.name, dirs_exist_ok=True)
        written.append(skills_dst / skill.name / "SKILL.md")
    claude_md = contest / "CLAUDE.md"
    claude_md.write_text(
        "@AGENTS.md\n\nIn Claude Code, use superpowers skills where the playbooks call for them: "
        "using-git-worktrees for task branches, dispatching-parallel-agents for parallel tasks, "
        "test-driven-development for coders, verification-before-completion before claiming done.\n"
    )
    written.append(claude_md)
    return written


def codex(contest: Path, root: Path = HOUSE_ROOT) -> Path:
    tools, profiles = _read_tools(contest), load_profiles(root)
    rows = ["| Agent | Model on this machine | Use for |", "|---|---|---|"]
    for pid, (meta, body) in profiles.items():
        tier = resolve_tier((meta, body), tools, profiles)
        rows.append(f"| `{pid}` | {tier or 'unavailable'} | {meta['description']} |")
    text = (root / "adapters/codex/AGENTS.md.tmpl").read_text()
    for key, value in {
        "{contest_name}": contest.name,
        "{house_dir}": str((contest / ".house").resolve()),
        "{house_root}": str(root.resolve()),
        "{roster}": "\n".join(rows),
        "{coordinator}": profiles["coordinator"][1].strip(),
    }.items():
        text = text.replace(key, value)
    path = contest / "AGENTS.md"
    path.write_text(text)
    return path


def adapt(contest: Path, root: Path = HOUSE_ROOT, detect=doctor.detect) -> list[Path]:
    install_core(contest, root)  # refresh playbooks/protocols from core; never touches existing .house files
    doctor.write(contest, detect())
    return [codex(contest, root), *claude_code(contest, root)]
