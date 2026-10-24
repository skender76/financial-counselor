# ITEM-003 — Agent inside the sandbox: technical plan

No production file (`server.py`, `auth.py`, ...) is modified.

## Design
- **One place starts containers.** `container_exec NET CMD...` in `sandbox/sandbox.sh` is the only `docker run`; commands and the agent share every restriction (read-only root, `--cap-drop ALL`, `no-new-privileges`, limits, host uid/gid, a single volume `$SANDBOX_DIR:/work`). `DOCKER_OPTS` carries the extras (label, `-e NAME`), `SANDBOX_HOME` sets HOME.
- **Agent mode** (`agent` subcommand): HOME is `/work/agent-home` (a sibling of `repo/`, never in git), label `dashboard-sandbox=agent`, `--init` so signals reach the agent, an interactive terminal is required. `--net` is opt-in and prints a warning (ITEM-005 will replace it with an allowlist). `--env NAME` only accepts a name, so secrets never appear on a command line.
- **Generic runtime:** the image has `ARG AGENT_INSTALL` (a shell command run at build time), fed by `SANDBOX_AGENT_INSTALL` in `setup`. Nothing provider-specific in the Dockerfile.
- **Control:** attached terminal + Ctrl+C; `agent-stop` kills containers by label.
- **verify:** the isolation checks run twice, in command mode and in agent mode (HOME differs, container is the same). New checks: single host mount, worktree + commit in a throwaway worktree (this also replaces the previous `reset --hard`, which could discard uncommitted work in the workspace).

## T1 — Dockerfile and launcher (`sandbox/Dockerfile`, `sandbox/sandbox.sh`)
Contract: `agent`, `agent-stop`, `AGENT_INSTALL` build argument, shared `container_exec`.

## T2 — Tests (`tests/test_sandbox.py`)
Static checks without Docker: a single `dk run`, the agent mount/HOME/label, no provider names in the Dockerfile. Behaviour checks that fail before docker is needed: `--env NAME=value` refused without echoing the value, missing command, missing terminal.

## T3 — Documentation (`README.md`, `CLAUDE.md`)

## Manual verification (owner)
`sandbox/sandbox.sh setup` (optionally with `SANDBOX_AGENT_INSTALL`), `sandbox/sandbox.sh verify --hash-app-db`, then one real agent task as described in the functional document.

## Known gaps
Network open with `--net` (ITEM-005); the agent's login or key lives in `agent-home` or in a forwarded variable (ITEM-006); nothing yet makes the sandbox mandatory (ITEM-004).
