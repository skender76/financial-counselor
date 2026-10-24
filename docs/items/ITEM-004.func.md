# ITEM-004 — Sandbox-only enforcement: functional requirements

Goal: the development workflow requires the sandbox, in writing and technically.

## Owner decisions
- **Enforcement:** a generic guard script (any agent or wrapper can call it) plus a project hook in `.claude/settings.json` that makes Claude Code run the guard before every tool call.
- **Override:** the environment variable `DASHBOARD_ALLOW_HOST=1`; every use is logged with a timestamp in `~/dashboard-sandbox/override.log`.
- **Preflight:** a quick `sandbox.sh status` before each handoff, the full `sandbox.sh verify` once at the start of each item.
- **Consequence the owner accepted:** once merged, a Claude Code session started on the Mac is blocked until it is started in the sandbox or the override is used.

## US-1 — The rule is written down
- **AT-1.1** `AGENTS.md` has a section stating that agents run in the sandbox, work reaches the real repository only through `pull` and human review, and the override exists and is for the owner only.
- **AT-1.2** `AGENTS.md` no longer describes the old host workflow (tests on `/tmp/test.db`, worktrees in `../wt-...`).

## US-2 — The guard refuses outside the sandbox
- **AT-2.1** Outside the sandbox, `sandbox/require-sandbox.sh` exits with 1 and a message in Italian that says how to start the sandbox.
- **AT-2.2** With `--hook` it exits with 2, the code that blocks a tool call in Claude Code.
- **AT-2.3** Inside the sandbox it exits with 0.
- **AT-2.4** "Inside the sandbox" means the file `/etc/dashboard-sandbox` exists with the expected text; that file is written by the image. An environment variable alone is not enough.

## US-3 — The Claude Code hook
- **AT-3.1** `.claude/settings.json` registers a `PreToolUse` hook, for all tools, that runs the guard with `--hook`.
- **AT-3.2** A Claude Code session started in the sandbox is not blocked; one started on the Mac is blocked before its first tool call.

## US-4 — Emergency override
- **AT-4.1** With `DASHBOARD_ALLOW_HOST=1` the guard passes and writes a line with timestamp, agent session id, directory and user to `override.log`; one line per session, not one per tool call.
- **AT-4.2** If the log cannot be written, the guard refuses.
- **AT-4.3** Any other value of the variable (`0`, `true`, empty) is not an override.

## US-5 — Preflight
- **AT-5.1** `sandbox.sh status` checks in a few seconds: VM running, image present, clone present, marker in the image; it exits with an error and tells the owner to run `setup` when something is missing.
- **AT-5.2** `sandbox.sh verify` also checks that the guard passes inside the container and blocks on the Mac.
- **AT-5.3** `AGENTS.md` makes the Orchestrator run `status` at each handoff and `verify` at the start of each item, and stop with `NEEDS HUMAN` on failure.

## US-6 — Documentation
- **AT-6.1** `README.md` and `CLAUDE.md` describe the guard, the hook, the override and `status`.
