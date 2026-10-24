# ITEM-004 — Sandbox-only enforcement: technical plan

No production file (`server.py`, `auth.py`, ...) is modified.

## Design
- **Marker:** the Dockerfile writes `dashboard-sandbox-v1` to `/etc/dashboard-sandbox`. The guard passes only if that file has that text. A file is used instead of an environment variable because an agent started on the Mac could export a variable, while creating a file in `/etc` needs root. The path is a constant in the script (no environment override), so tests use a copy of the script with another path.
- **Guard** (`sandbox/require-sandbox.sh`): exit 0 inside; otherwise 1 (2 with `--hook`, the blocking code of Claude Code hooks) and a message on stderr. Override via `DASHBOARD_ALLOW_HOST=1`, logged to `$SANDBOX_DIR/override.log` (`ts override ppid=<pid> cwd=<dir> user=<user>`), one line per `PPID`, refused when the log cannot be written.
- **Hook** (`.claude/settings.json`): `PreToolUse`, matcher `*`, command `"$CLAUDE_PROJECT_DIR"/sandbox/require-sandbox.sh --hook`. It is tracked, so the clone used in the sandbox has it too and passes there.
- **Preflight:** `sandbox.sh status` (VM, image, clone, marker) and the new guard checks in `verify`.

## T1 — Guard, marker, status, verify (`sandbox/require-sandbox.sh`, `sandbox/Dockerfile`, `sandbox/sandbox.sh`)
## T2 — Tests (`tests/test_sandbox.py`, `tests/test_claude_hook.py`)
Guard behaviour on the Mac (blocks, `--hook` code 2, override logged once per session, refuses without a writable log, other values refused, environment variable alone is not enough, a copy with a test marker passes); static checks on the Dockerfile marker and `status`; the hook file is valid JSON and calls the guard with `--hook`.
## T3 — Rule (`AGENTS.md` section 8, Orchestrator row, tests and worktree lines) and docs (`README.md`, `CLAUDE.md`)

## Order of delivery
The hook file is the last change: from the moment `.claude/settings.json` exists in the working tree, a Claude Code session on the Mac is blocked. It is added in the same step as the merge, after the owner's approval.

## Manual verification (owner)
`sandbox/sandbox.sh setup` (rebuilds the image with the marker), `sandbox/sandbox.sh status`, `sandbox/sandbox.sh verify`; then a new Claude Code session started in the project folder on the Mac must be blocked, and one started with `sandbox/sandbox.sh agent --net -- claude` must work.

## Known limits
The guard stops mistakes, not a hostile agent: someone with access to the Mac can edit it or the hook (changes to `sandbox/` and `.claude/` are reviewed at `pull`, ITEM-007).
