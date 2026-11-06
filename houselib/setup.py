"""`house setup`: make a fresh machine ready (user-level skills + hints for missing tools)."""
import filecmp
import shutil
import time
from pathlib import Path

from . import HOUSE_ROOT


def _same_tree(a: Path, b: Path) -> bool:
    cmp = filecmp.dircmp(a, b)
    if cmp.left_only or cmp.right_only or cmp.diff_files or cmp.funny_files:
        return False
    return all(_same_tree(a / d, b / d) for d in cmp.common_dirs)


def install_user_skills(root: Path = HOUSE_ROOT, home: Path = Path.home()) -> list[str]:
    """Copy user-skills/* to ~/.claude/skills; a differing local copy is kept as <name>.bak-<timestamp>."""
    dst_root = home / ".claude/skills"
    dst_root.mkdir(parents=True, exist_ok=True)
    installed = []
    for src in sorted((root / "user-skills").iterdir()):
        if not src.is_dir():
            continue
        dst = dst_root / src.name
        if dst.exists():
            if _same_tree(src, dst):
                installed.append(src.name)
                continue
            dst.rename(dst_root / f"{src.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copytree(src, dst)
        installed.append(src.name)
    return installed


def hints(tools: dict) -> list[str]:
    """Exact next steps for whatever is missing on this machine."""
    out = []
    if not tools.get("claude"):
        out.append("Claude Code: install from https://claude.com/claude-code, then run `claude` and log in")
    out.append("superpowers plugin (if not installed yet): in Claude Code run "
               "`/plugin install superpowers@claude-plugins-official`, then restart")
    if not tools.get("uv"):
        out.append("uv: `curl -LsSf https://astral.sh/uv/install.sh | sh` (Python tooling: uv run / uvx)")
    if not tools.get("ruff"):
        out.append("ruff: `uv tool install ruff`")
    if not tools.get("codex"):
        out.append("Codex CLI: `npm i -g @openai/codex && codex login` (second usage pool)")
    ollama, gpu = tools.get("ollama") or {}, tools.get("gpu")
    if not ollama.get("installed"):
        out.append("ollama (optional, local summaries): https://ollama.com/download")
    elif not ollama.get("models") and gpu and gpu.get("vram_mib", 0) >= 10000:
        out.append("ollama model: `ollama pull qwen3:8b` (enough VRAM on this GPU)")
    return out
