"""Small git helpers. Identity is always set per repo, never globally."""
import subprocess
from pathlib import Path


def git(cwd: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {cwd}: {r.stderr.strip()}")
    return r.stdout


def identity(root: Path) -> tuple[str, str]:
    """Local identity of the system repo (falls back to whatever git resolves)."""
    return git(root, "config", "user.name").strip(), git(root, "config", "user.email").strip()


def set_identity(repo: Path, name: str, email: str) -> None:
    git(repo, "config", "user.name", name)
    git(repo, "config", "user.email", email)
