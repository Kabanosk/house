"""Small git helpers. Identity is always set per repo, never globally.

Used by: scaffold, registry, adapters and export when they create or clone repos. Rule: never change the global
git config; contest repos get the identity of the house repo (`identity`/`set_identity`).
"""
import subprocess
from pathlib import Path


def git(cwd: Path, *args: str) -> str:
    """Run `git <args>` and return its output.

    Args:
        cwd: Directory to run in.
        *args: git arguments, e.g. `"config", "user.email"`.

    Returns:
        Standard output.

    Raises:
        RuntimeError: git exited non-zero; the message holds the command and stderr.
    """
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {cwd}: {r.stderr.strip()}")
    return r.stdout


def identity(root: Path) -> tuple[str, str]:
    """Read the local git identity of a repo.

    Args:
        root: Repo to read from (falls back to whatever git resolves there).

    Returns:
        `(user.name, user.email)`.

    Raises:
        RuntimeError: No name or email is configured.
    """
    return git(root, "config", "user.name").strip(), git(root, "config", "user.email").strip()


def set_identity(repo: Path, name: str, email: str) -> None:
    """Set the repo-local git identity.

    Args:
        repo: Repo to configure (never the global config).
        name: `user.name`.
        email: `user.email`.
    """
    git(repo, "config", "user.name", name)
    git(repo, "config", "user.email", email)
