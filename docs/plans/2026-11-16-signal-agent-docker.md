# S7.1: Signal jobs in a Docker container — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Run every `@claude` job in its own Docker container instead of Claude Code's bubblewrap sandbox (which
cannot start on this Ubuntu because AppArmor forbids nested user namespaces), so jobs can run Bash (tests,
experiments) while still seeing nothing but their worktree and reaching only an allowlist of hosts.

**Decisions (Wojtek, Signal 2026-11-16):** Q4 „Dockerowy kontener”; auth: a subscription token from
`claude setup-token` (option A), stored by house, revocable, never the owner's main login.

**Architecture:** image `house-agent:<claude version>` (node 22 slim + python3 + git + pinned
`@anthropic-ai/claude-code`), built by `house signal agent image`. Two Docker networks: `house-agent-int`
(`--internal`, no route out) and the default bridge. A long-running container `house-agent-proxy` (same image,
stdlib Python CONNECT proxy from `houselib/agentproxy.py`, allowlist) sits on both. Job containers sit only on
`house-agent-int` with `HTTPS_PROXY=http://house-agent-proxy:3128`. The systemd unit per job stays (2 h limit,
`/stop`), it now runs `docker run --rm --name house-agent-<id> …` and `ExecStopPost=docker rm -f house-agent-<id>`.

**Spec:** `docs/specs/2026-11-16-signal-groups-design.md` §15; research note §2; plan
`docs/plans/2026-11-16-signal-agent.md` (S7, all of its rules stay).

## Global Constraints

- Container: `--user 1001:1001` (the owner's uid/gid, so files in the worktree stay theirs), `--cap-drop ALL`,
  `--security-opt no-new-privileges`, `--read-only` root, `--tmpfs /tmp` and `--tmpfs /home/agent`,
  `--memory 8g --pids-limit 512`, `--network house-agent-int`, `--init`.
- Mounts: worktree `rw` at its host path (paths in messages stay valid); the repo's `.git` `ro` at its host path
  (agent can read history, cannot write it); the house code `ro` at `/house` (hook); job prompt and settings `ro`.
  Nothing else from the host — no `$HOME`, no `~/.claude`, no docker socket.
- Token: `~/.config/house/agent-token` (0600, written by `house signal agent token`, TTY + not inside Claude
  Code); passed with `--env-file` from a 0600 file in the house job dir (never in argv, never in the worktree).
- Proxy allowlist: `api.anthropic.com`, `statsig.anthropic.com` (telemetry endpoints of other vendors are not allowed), plus
  `pypi.org`, `files.pythonhosted.org`, `download.pytorch.org`. CONNECT only (TLS passthrough), port 443 only;
  everything else 403. The proxy logs host names only.
- Claude in the container: `--permission-mode dontAsk`, scoped rules for the worktree **plus `Bash`** (the
  container is the boundary), built-in sandbox off, hook on (job mode, fail closed), no web/MCP tools.
- Runtime choice per job: `docker` when Docker works, the image exists and the token is set; otherwise the S7
  local runtime (no Bash without nested userns). `house doctor` and `agent status` show which.

## Tasks

### Task 1: allowlist proxy (`houselib/agentproxy.py`)
- `allowed(host, port) -> bool`; `serve(port, allow)` — threading HTTP server handling `CONNECT host:443` only,
  tunnelling bytes both ways; 403 otherwise; host names logged to stderr.
- [ ] Tests: allowlist matching (exact host, no suffix tricks like `api.anthropic.com.evil.io`, port 443 only);
  a real local CONNECT to a refused host returns 403; GET (non-CONNECT) returns 405.

### Task 2: image and networks
- `core/docker/agent/Dockerfile`; `signalagent.image_name()`, `build_image(run)`, `ensure_networks(run)`,
  `ensure_proxy(run)` (start `house-agent-proxy` if not running, `--restart unless-stopped`, on both networks,
  `--read-only`, cap-drop, runs `python3 -m houselib.agentproxy`).
- [ ] Tests with a fake runner: exact docker argv; idempotency (exists → no create).

### Task 3: token
- `house signal agent token` (TTY, not CLAUDECODE): reads the token with `getpass`, writes the file 0600;
  `token_present()`; guard refuses `agent token` to agents and refuses reads of `~/.config/house` already.
- [ ] Tests: refused without TTY / inside Claude Code; file mode 0600; never echoed.

### Task 4: docker job command
- `runtime(run) -> "docker" | "local"`; `docker_command(job, wt, ...)` per Global Constraints; settings for the
  docker runtime (Bash allowed, sandbox off, hook command `/house/bin/house-hook pre-tool`); env file with the
  token + job vars; `launch` picks the runtime and records it on the job; `/stop` and timeout remove the container
  (`ExecStopPost`).
- [ ] Tests: argv contains every isolation flag and mount, no `$HOME`/`~/.claude`/docker.sock mount, token not in
  argv, env file 0600; local fallback when the image or token is missing.

### Task 5: doctor, CLI, docs
- `house signal agent image` (build + networks + proxy), `agent status` shows runtime; doctor lines for Docker,
  image, proxy, token; README.
- [ ] Tests for the lines; commit.

### Task 6: live check
- [ ] Build the image, start the proxy, run one job with a test token-less dry run (`claude --version` in the
  container), then — after Wojtek has set the token — a real job: writes in the worktree, `pip download` works
  via the proxy, `curl https://example.com` is refused, `cat ~/.ssh/…` finds nothing.
