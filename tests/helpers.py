import shutil
import subprocess
from pathlib import Path

from houselib import HOUSE_ROOT

NAME = "Test Owner"
EMAIL = "owner@example.com"

FAKE_TOOLS = {"claude": True, "codex": False, "cursor": False, "grok_bot": False,
              "ollama": {"installed": False, "models": []}, "gpu": None}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def make_root(tmp: Path) -> Path:
    """A throwaway system repo: copy of core/ + adapters/, git-initialised with a local identity."""
    root = tmp / "house"
    root.mkdir()
    for d in ("core", "adapters", "bin", "vendor"):
        if (HOUSE_ROOT / d).exists():
            shutil.copytree(HOUSE_ROOT / d, root / d)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", NAME)
    git(root, "config", "user.email", EMAIL)
    return root
