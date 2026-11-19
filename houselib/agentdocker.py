"""Docker runtime for Signal jobs (S7.1): every `@claude` job in its own locked-down container.

The container sees only the job's worktree (read-write), the repo's `.git` (read-only), the house code (read-only,
for the job hook) and the job's prompt and settings. It runs as the owner's uid without capabilities, with a
read-only root, and its only network is `house-agent-int` (internal: no route out); the proxy container
`house-agent-proxy` (`houselib.agentproxy`) is the single way out, to an allowlist of hosts. Claude authenticates
with a subscription token from `claude setup-token`, stored in `~/.config/house/agent-token` (0600) and passed per
job through an `--env-file` in the house-owned job dir — never in argv, never in the worktree.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

CLAUDE_VERSION = "2.1.325"
IMAGE = "house-agent"
INTERNAL_NET = "house-agent-int"
PROXY = "house-agent-proxy"
PROXY_PORT = 3128
CODE_ROOT = Path(__file__).resolve().parents[1]
MEMORY = "8g"
PIDS = 512
# Environment every Claude container gets next to the token and proxy (HOME is the tmpfs, the rest keeps Claude quiet).
BASE_ENV = {"HOME": "/home/agent", "LANG": "C.UTF-8",
            "CLAUDE_CODE_TMPDIR": "/home/agent",  # owned by the job user; /tmp/claude-<uid> may be a root mount path
            "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
            "DISABLE_AUTOUPDATER": "1", "DISABLE_TELEMETRY": "1"}
# The image's own PATH (node:22-bookworm-slim), for runs that put their own directory in front.
IMAGE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def image_name() -> str:
    """`house-agent:<pinned Claude Code version>`."""
    return f"{IMAGE}:{CLAUDE_VERSION}"


def _ok(run, argv: list[str]) -> bool:
    try:
        return run(argv, capture_output=True, text=True, check=False).returncode == 0
    except OSError:
        return False


def docker_usable(run=subprocess.run) -> bool:
    return _ok(run, ["docker", "info", "--format", "{{.ServerVersion}}"])


def image_present(run=subprocess.run) -> bool:
    return _ok(run, ["docker", "image", "inspect", image_name()])


def build_image(run=subprocess.run) -> subprocess.CompletedProcess:
    """Build the job image from `core/docker/agent/Dockerfile` (pinned Claude Code)."""
    folder = CODE_ROOT / "core/docker/agent"
    return run(["docker", "build", "-t", image_name(), "--build-arg", f"CLAUDE_VERSION={CLAUDE_VERSION}",
                "-f", str(folder / "Dockerfile"), str(folder)], capture_output=True, text=True, check=False)


def ensure_networks(run=subprocess.run) -> None:
    """Create the internal network (no route out) once."""
    if not _ok(run, ["docker", "network", "inspect", INTERNAL_NET]):
        run(["docker", "network", "create", "--internal", INTERNAL_NET], capture_output=True, text=True,
            check=False)


def proxy_running(run=subprocess.run) -> bool:
    try:
        r = run(["docker", "inspect", "-f", "{{.State.Status}}", PROXY], capture_output=True, text=True,
                check=False)
    except OSError:
        return False
    return r.returncode == 0 and r.stdout.strip() == "running"  # `.State.Running` is true in a restart loop too


def proxy_status(run=subprocess.run) -> str:
    """`docker inspect` status of the proxy ("running", "restarting", ...), "" when it does not exist."""
    try:
        r = run(["docker", "inspect", "-f", "{{.State.Status}}", PROXY], capture_output=True, text=True,
                check=False)
    except OSError:
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def ensure_proxy(run=subprocess.run) -> None:
    """Start `house-agent-proxy` (locked down like the jobs) on the bridge and the internal network if needed."""
    if proxy_running(run):
        return
    run(["docker", "rm", "-f", PROXY], capture_output=True, text=True, check=False)
    run(["docker", "run", "-d", "--name", PROXY, "--restart", "unless-stopped", "--read-only", "--cap-drop", "ALL",
         "--security-opt", "no-new-privileges", "--user", f"{os.getuid()}:{os.getgid()}", "--memory", "256m",
         "-v", f"{CODE_ROOT}:/house:ro", "-w", "/house", image_name(),
         "python3", "-m", "houselib.agentproxy", str(PROXY_PORT)], capture_output=True, text=True, check=False)
    run(["docker", "network", "connect", INTERNAL_NET, PROXY], capture_output=True, text=True, check=False)


def token_path() -> Path:
    from . import signalchats
    return signalchats.config_path().parent / "agent-token"


def token_present() -> bool:
    return token_path().is_file() and bool(read_token())


def read_token() -> str:
    try:
        return token_path().read_text().strip()
    except OSError:
        return ""


def save_token(token: str) -> None:
    """Store the subscription token for job containers (0600; the directory is created 0700)."""
    path = token_path()
    if not path.parent.exists():
        path.parent.mkdir(parents=True)
        path.parent.chmod(0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (token.strip() + "\n").encode())
    finally:
        os.close(fd)
    path.chmod(0o600)


def snap_docker(which=None) -> bool:
    """Docker from snap: its dockerd AppArmor profile refuses every exec under `no-new-privileges`."""
    path = (which or shutil.which)("docker")
    return bool(path) and path.startswith("/snap/")


def runtime(run=subprocess.run, which=None) -> str:
    """"docker" when Docker works, the image is built and the token is set; else "local" (S7 fallback).

    Snap Docker stays "local": no container with `no-new-privileges` can start there, so every job would fail.
    """
    if snap_docker(which):
        return "local"
    return "docker" if token_present() and docker_usable(run) and image_present(run) else "local"


def proxy_env() -> dict[str, str]:
    """The proxy variables of a container: HTTP(S) through `house-agent-proxy`, nothing else direct."""
    proxy = f"http://{PROXY}:{PROXY_PORT}"
    return {"HTTPS_PROXY": proxy, "HTTP_PROXY": proxy, "https_proxy": proxy, "http_proxy": proxy,
            "NO_PROXY": "localhost,127.0.0.1"}


def write_env_lines(path: Path, lines: dict[str, str]) -> Path:
    """Write `KEY=value` lines for `docker run --env-file`, created 0600 (it holds the token)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, "".join(f"{k}={v}\n" for k, v in lines.items()).encode())
    finally:
        os.close(fd)
    path.chmod(0o600)
    return path


def write_env_file(job_dir: Path, job_id: str, wt: Path) -> Path:
    """The job's environment for `docker run --env-file` (token, proxy, job vars), 0600 in the job dir."""
    lines = {"CLAUDE_CODE_OAUTH_TOKEN": read_token(), **proxy_env(),
             "HOUSE_AGENT_JOB": job_id, "HOUSE_AGENT_WORKTREE": str(wt), **BASE_ENV}
    return write_env_lines(job_dir / "container.env", lines)


def hardening_flags(name: str, env_file: Path | None, network: str = INTERNAL_NET) -> list[str]:
    """`docker run` flags shared by every container that runs Claude (or code an agent wrote): name, user, no
    capabilities, read-only root, tmpfs home, limits, the network and the env-file (the caller adds mounts, workdir,
    image, command).

    Args:
        name: Container name.
        env_file: `--env-file` path; None adds no env-file (a container that must hold no secret).
        network: Docker network; the internal one behind the proxy by default, `none` for no network at all.
    """
    flags = ["--rm", "-i", "--init", "--name", name,
             "--user", f"{os.getuid()}:{os.getgid()}", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
             "--read-only", "--tmpfs", "/tmp:exec",
             "--tmpfs", f"/home/agent:exec,uid={os.getuid()},gid={os.getgid()},mode=0700", "--memory", MEMORY,
             "--pids-limit", str(PIDS), "--network", network]
    return flags + ["--env-file", str(env_file)] if env_file is not None else flags


def container_command(job_id: str, wt: Path, repo: Path, prompt: Path, settings: Path, env_file: Path,
                      claude_argv: list[str]) -> list[str]:
    """`docker run` for one job: only the listed mounts, no capabilities, internal network, resource limits."""
    return ["docker", "run", *hardening_flags(f"house-agent-{job_id}", env_file),
            "-v", f"{wt}:{wt}", "-v", f"{repo / '.git'}:{repo / '.git'}:ro", "-v", f"{CODE_ROOT}:/house:ro",
            "-v", f"{prompt}:/job/prompt.md:ro", "-v", f"{settings}:/job/settings.json:ro",
            "-w", str(wt), image_name(), *claude_argv]


def doctor_lines(run=subprocess.run, which=None, unit: Path | None = None) -> list[str]:
    """`house doctor` lines about the Docker runtime of Signal jobs.

    Args:
        run: Runner for `docker` (signature of `subprocess.run`).
        which: `shutil.which`-like lookup (default `shutil.which`), also called with `path=` for the timer's PATH.
        unit: The S7 timer's service unit. Defaults to `~/.config/systemd/user/house-signal-pull.service`.
    """
    which = which or shutil.which
    if not docker_usable(run):
        return ["✗ S7 docker: Docker niedostępny (zlecenia idą lokalnie, bez Basha)"]
    lines = ["✓ S7 docker: Docker działa"]
    if snap_docker(which):
        lines.append(f"✗ S7 docker: Docker ze snapa ({which('docker')}) nie uruchamia kontenerów z "
                     "no-new-privileges (AppArmor), zlecenia idą lokalnie; zainstaluj docker-ce z apt "
                     "(https://docs.docker.com/engine/install/ubuntu/)")
    path = _unit_env_path(unit or Path.home() / ".config/systemd/user/house-signal-pull.service")
    if path is not None and not which("docker", path=path):
        lines.append("✗ S7 docker: timer nie widzi `docker` w swoim PATH, zlecenia idą lokalnie "
                     "(`house signal agent on` przeinstaluje timer)")
    lines.append("✓ S7 docker: obraz " + image_name() if image_present(run)
                 else f"✗ S7 docker: brak obrazu {image_name()} (`house signal agent image`)")
    lines.append("✓ S7 docker: token ustawiony" if token_present()
                 else "✗ S7 docker: brak tokenu (`claude setup-token`, potem `house signal agent token`)")
    if image_present(run):
        status = proxy_status(run)
        if status == "running":
            lines.append("✓ S7 docker: proxy działa")
        elif status == "restarting":
            lines.append(f"✗ S7 docker: proxy w pętli restartów (`docker logs {PROXY}`)")
        else:
            lines.append("✗ S7 docker: proxy nie działa (`house signal agent image` je uruchomi)")
    return lines


def _unit_env_path(unit: Path) -> str | None:
    """`Environment=PATH=` of a systemd unit, None when the unit is missing or sets no PATH."""
    try:
        text = unit.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"^Environment=PATH=(.*)$", text, re.MULTILINE)
    return m.group(1).strip() if m else None
