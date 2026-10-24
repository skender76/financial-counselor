# ITEM-003 — Agent inside the sandbox: functional requirements

Goal: the agent process runs inside the sandbox container, so its shell and its file tools can only see what the container sees.

## Owner decisions
- **Runtime:** generic launcher. `sandbox.sh agent -- <command>` runs any agent CLI; the CLI is installed in the image through a build argument. No agent or provider is hard-wired.
- **History and settings:** kept in `~/dashboard-sandbox/agent-home`, mounted as the agent's HOME, outside the git clone.
- **Control:** the agent runs attached to the owner's terminal; Ctrl+C stops it, and `sandbox.sh agent-stop` kills a stuck one.
- **Known gaps, handled by other items:** network allowlist (ITEM-005) and credentials (ITEM-006). Until then the agent needs `--net`, with a warning, and may receive credentials by variable name with `--env NAME`.

## US-1 — Start an agent in the sandbox
- **AT-1.1** Given a prepared sandbox, when I run `sandbox/sandbox.sh agent -- <command>` from a terminal, then `<command>` runs in the container with `/work/repo` as working directory and `HOME=/work/agent-home`.
- **AT-1.2** Given the command is run without a terminal, then it refuses to start with a clear message.
- **AT-1.3** Given no command is passed, then it prints the usage and exits with an error.
- **AT-1.4** Given the sandbox was set up with `SANDBOX_AGENT_INSTALL`, then that CLI is available in the image; with it empty, no agent is installed and nothing provider-specific is in the image.

## US-2 — The agent cannot reach real data
- **AT-2.1** From an agent session, `app.db`, `.env`, `dati.json`, `ai-config.json` and `esempi-estratti` do not exist under `/work/repo`.
- **AT-2.2** From an agent session, the owner's home (`~/.ssh`) and the real repository path do not exist.
- **AT-2.3** The only host mount is the sandbox folder `/work`.
- **AT-2.4** Since the agent's shell and file tools run in the same container, AT-2.1 to AT-2.3 hold for both (`sandbox.sh verify` runs the checks in command mode and in agent mode).

## US-3 — Work like the development flow
- **AT-3.1** In an agent session, git worktrees can be created under `/work` and commits can be made (`AGENTS.md`, section 6).
- **AT-3.2** `git push` fails from an agent session.
- **AT-3.3** Commits made by the agent reach the real repository only through `sandbox.sh pull BRANCH`.
- **AT-3.4** The tests pass inside the sandbox.

## US-4 — Control
- **AT-4.1** Ctrl+C in the owner's terminal stops the agent and removes the container.
- **AT-4.2** `sandbox.sh agent-stop` stops every running agent container and says so; with none running it says "Nessun agente in esecuzione.".

## US-5 — Credentials passed safely (interim)
- **AT-5.1** `--env NAME` forwards the variable named `NAME` from the host environment; `--env NAME=value` is refused and the value is never printed.

## US-6 — Documentation
- **AT-6.1** `README.md` and `CLAUDE.md` describe the command, the history folder and the known gaps.

## Manual acceptance (needs an agent CLI and its key)
A task (for example adding a test) is completed and committed inside the sandbox by a real agent and brought out with `sandbox.sh pull`; the owner's `app.db` is byte-identical before and after (`verify --hash-app-db`).
