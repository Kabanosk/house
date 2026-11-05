# Protocol: public export

Contest repos are always private. Anything public (hackathon submission repo, shared code) is produced only
with `house export <contest> <dest>`:

- exports tracked files only (`git ls-files`),
- excludes `.house/`, `.claude/`, `.cursor/`, `.codex/`, `AGENTS.md`, `CLAUDE.md`, `.env*` (keeps `.env.example`),
- scans for secrets (API keys, private keys) and refuses on hits unless `--force`,
- creates a fresh git repo with a single commit.

Before export: README for the public (language `spec.languages.public`) must exist in the contest repo.
Never push the contest repo itself to a public remote.
