# ROADMAP

Features ordered by priority. The workflow and the meaning of each status are defined in `AGENTS.md`.

Statuses: `OPEN` | `READY FOR DEV` | `IN PROGRESS` | `BLOCKED` | `NEEDS HUMAN` | `DONE`

Only items that are not `DONE` keep their full text here. A `DONE` item is reduced to its title and status, and its full text lives in `ROADMAP-DONE.md` (do not read that file unless you need the history).

## ITEM-000 — Test harness

- **Status:** DONE — full text in `ROADMAP-DONE.md`.

## ITEM-001 — Development Sandbox

- **Status:** DONE — full text in `ROADMAP-DONE.md`.

## ITEM-002 — Demo Mode

- **Status:** OPEN
- **Why:** as the project owner I want to show the different features of this tool using mock data, without exposing my real data.
- **Goal:** a setup to demo the product without using my actual data.
- **Scope (to be refined by the Functional and Technical Lead agents):**
  - A Demo user is configured, with a demo password.
  - The fake data of the Demo user is rich enough to show the capabilities of the tool: accounts, movements, transfers, investments, properties, flows and charts.
  - The demo data is separate from every real user's data and is never mixed with it.
- **Open questions (the Functional Agent must settle them with the owner):**
  - How is the Demo user created and reset to its initial data (CLI command, on startup, on every login)?
  - Is the demo password public? Does demo mode have to work on the hosted online instance, or only locally?
  - How does the AI chat behave in demo mode, since it needs a real API key (disabled, canned answers, or a key supplied by the owner)?
  - Can demo visitors change the demo data? If so, how is it protected from abuse and restored afterwards?
  - Must 2FA, password change and API-key management be disabled for the Demo user?
- **Out of scope:** N/A.
- **Done when:** logging in as the Demo user shows a fully populated dashboard; the demo data never touches or mixes with a real user's data; the Demo user's initial state can be restored; and the item's tests (see ITEM-000) pass.
- **Depends on:** ITEM-000.

## ITEM-003 — Agent inside the sandbox

- **Status:** OPEN
- **Why:** ITEM-001 isolates the commands (tests, server, git) but the agent process itself, with its file tools, still runs on the owner's machine with the owner's permissions. Staying inside the clean clone is a convention, not something enforced. One agent session already ran `shasum` on `app.db` against the rules in `AGENTS.md`.
- **Goal:** the agent process runs inside the sandbox, so everything it does (reading, writing, running commands, committing) happens in the container and cannot reach the owner's real data and secrets.
- **Scope (to be refined by the Functional and Technical Lead agents):**
  - A documented command that starts an agent session inside the sandbox container, with `/work/repo` as its working directory.
  - The agent's own tools (file read/write/edit, shell) act only on files visible in the container.
  - The agent's credentials (for example its API key) reach the container in a controlled way, never from a mounted home directory, and are not written to the workspace or to git.
  - Network access is limited to what the agent needs to talk to its model provider (an allowlist, not open internet); tests and the local server stay offline as in ITEM-001.
  - Works with the git worktrees of `AGENTS.md` (section 6) and with `sandbox.sh pull`.
- **Open questions (the Technical Lead Agent must settle them with the owner):**
  - Which agent runtimes must be supported (Claude Code only, or a generic way to run any agent CLI)? The owner wants to limit lock-in.
  - How is the network allowlist enforced with Colima and docker (a proxy container, firewall rules in the VM, an internal docker network)?
  - How are the agent's credentials passed (environment variable at start, docker secret, a file removed after start) and how are they kept out of logs and commits?
  - Does the agent keep its session history and settings between runs, and where are they stored?
  - How does the owner watch and interrupt an agent running in the container (attached terminal, logs)?
- **Out of scope:** protecting the production server; browser/UI testing; making the model provider itself trusted.
- **Done when:** from a session started by the documented command, reading `app.db`, `.env`, `dati.json`, `ai-config.json`, the real repository path and `~/.ssh` fails when attempted through the agent's own file tools as well as its shell; a task (for example adding a test) is completed and committed inside the sandbox and brought out with `sandbox.sh pull`; outbound connections to hosts outside the allowlist fail; and the owner's `app.db` is byte-identical before and after.
- **Depends on:** ITEM-001.
