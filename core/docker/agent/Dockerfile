# Image for Signal jobs (S7.1) and their allowlist proxy: Claude Code (pinned), python3, git. Built by
# `house signal agent image`; nothing secret is baked in (the token comes per job through --env-file).
FROM node:22-bookworm-slim
ARG CLAUDE_VERSION
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pip python3-venv git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_VERSION}" && npm cache clean --force
ENV HOME=/home/agent DISABLE_AUTOUPDATER=1 PIP_NO_CACHE_DIR=1
WORKDIR /work
