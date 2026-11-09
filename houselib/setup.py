"""`house setup`: make a fresh machine ready (user-level skills + hints for missing tools)."""
import filecmp
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from . import HOUSE_ROOT


def _same_tree(a: Path, b: Path) -> bool:
    cmp = filecmp.dircmp(a, b)
    if cmp.left_only or cmp.right_only or cmp.diff_files or cmp.funny_files:
        return False
    return all(_same_tree(a / d, b / d) for d in cmp.common_dirs)


def _install_skill(src: Path, dst_root: Path) -> None:
    """Copy one skill folder into dst_root; a differing local copy is kept as <name>.bak-<timestamp>."""
    dst = dst_root / src.name
    if dst.exists():
        if _same_tree(src, dst):
            return
        dst.rename(dst_root / f"{src.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copytree(src, dst)


def install_user_skills(root: Path = HOUSE_ROOT, home: Path = Path.home()) -> list[str]:
    """Copy user-skills/* to ~/.claude/skills."""
    dst_root = home / ".claude/skills"
    dst_root.mkdir(parents=True, exist_ok=True)
    installed = []
    for src in sorted((root / "user-skills").iterdir()):
        if src.is_dir():
            _install_skill(src, dst_root)
            installed.append(src.name)
    return installed


REMOTION_REPO = "https://github.com/remotion-dev/skills"
REMOTION_SKILL = "remotion-best-practices"
REMOTION_LICENSE_NOTE = ("Remotion is free for individuals and companies of up to 3 employees; bigger companies "
                         "need a company licence (remotion.dev/license); fine for hackathons entered privately.")
GIT_TIMEOUT_S = 120


def _git_clone_or_pull(url: str, dest: Path) -> None:
    if (dest / ".git").is_dir():
        cmd = ["git", "-C", str(dest), "pull", "-q", "--ff-only"]
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["git", "clone", "-q", "--depth", "1", url, str(dest)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=GIT_TIMEOUT_S, check=False)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"{cmd[1]} failed")


def fetch_remotion_skill(home: Path | None = None,
                         clone: Callable[[str, Path], None] = _git_clone_or_pull) -> str | None:
    """Fetch remotion-best-practices into ~/.claude/skills (not vendored: its repo has no licence to redistribute).

    Clones into ~/.cache/house/remotion-skills; offline, falls back to that cache. None = nothing installed.
    """
    home = home or Path.home()
    cache = home / ".cache/house/remotion-skills"
    try:
        clone(REMOTION_REPO, cache)
    except (RuntimeError, OSError, subprocess.SubprocessError):
        pass  # offline: use the cached copy if there is one
    src = cache / "skills" / REMOTION_SKILL
    if not (src / "SKILL.md").is_file():
        return None
    dst_root = home / ".claude/skills"
    dst_root.mkdir(parents=True, exist_ok=True)
    _install_skill(src, dst_root)
    return REMOTION_SKILL


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
    out.append("before a video sprint: `npx remotion browser ensure` in the video project "
               "(downloads Remotion's Chrome Headless Shell once, so the first render doesn't)")
    return out
