# ROADMAP

Features ordered by priority. The workflow and the meaning of each status are defined in `AGENTS.md`.

Statuses: `OPEN` | `READY FOR DEV` | `IN PROGRESS` | `BLOCKED` | `NEEDS HUMAN` | `DONE`

Only items that are not `DONE` keep their full text here. A `DONE` item is reduced to its title and status, and its full text lives in `ROADMAP-DONE.md` (do not read that file unless you need the history).

## ITEM-000 — Test harness

- **Status:** DONE — full text in `ROADMAP-DONE.md`.

## ITEM-001 — Development Sandbox

- **Status:** DONE — full text in `ROADMAP-DONE.md`.

## ITEM-003 — Agent inside the sandbox

- **Status:** OPEN
- **Why:** ITEM-001 isolates the commands (tests, server, git) but the agent process itself, with its file tools, still runs on the owner's machine with the owner's permissions. Staying inside the clean clone is a convention, not something enforced. One agent session already ran `shasum` on `app.db` against the rules in `AGENTS.md`.
- **Goal:** the agent process runs inside the sandbox, so everything it does (reading, writing, running commands, committing) happens in the container and cannot reach the owner's real data and secrets.
- **Scope (to be refined by the Functional and Technical Lead agents):**
  - A documented command that starts an agent session inside the sandbox container, with `/work/repo` as its working directory.
  - The agent's own tools (file read/write/edit, shell) act only on files visible in the container.
  - Works with the git worktrees of `AGENTS.md` (section 6) and with `sandbox.sh pull`.
  - Network access for the agent and the handling of its credentials are separate items (ITEM-005, ITEM-006); until they exist this item may use `run --net`, documented as a known gap.
- **Open questions (the Technical Lead Agent must settle them with the owner):**
  - Which agent runtimes must be supported (Claude Code only, or a generic way to run any agent CLI)? The owner wants to limit lock-in.
  - Does the agent keep its session history and settings between runs, and where are they stored?
  - How does the owner watch and interrupt an agent running in the container (attached terminal, logs)?
- **Out of scope:** protecting the production server; browser/UI testing; making the model provider itself trusted; egress allowlist (ITEM-005); credentials (ITEM-006).
- **Done when:** from a session started by the documented command, reading `app.db`, `.env`, `dati.json`, `ai-config.json`, the real repository path and `~/.ssh` fails when attempted through the agent's own file tools as well as its shell; a task (for example adding a test) is completed and committed inside the sandbox and brought out with `sandbox.sh pull`; and the owner's `app.db` is byte-identical before and after.
- **Depends on:** ITEM-001.

## ITEM-004 — Sandbox-only enforcement

- **Status:** OPEN
- **Why:** nothing in `AGENTS.md` says development must happen in the sandbox, and nothing stops an agent from running on the host.
- **Goal:** the development workflow requires the sandbox, in writing and technically.
- **Scope (to be refined by the Functional and Technical Lead agents):**
  - `AGENTS.md` states that all commands, worktrees and commits happen in the sandbox and that branches reach the real repository only through `sandbox.sh pull` after human review.
  - A start script (or a hook) that refuses to start an agent session outside the sandbox, with a clear error.
  - The Orchestrator checks the sandbox is up (`verify` passes) before handing work to any agent.
- **Open questions:** how is "inside the sandbox" detected reliably (a marker file in the image, an environment variable set by `sandbox.sh`)? What is the owner's emergency override, and how is its use recorded?
- **Done when:** starting an agent session outside the sandbox fails with an explanatory message; starting it through the documented command works; `AGENTS.md` describes the rule.
- **Depends on:** ITEM-003.

## ITEM-005 — Egress allowlist

- **Status:** OPEN
- **Why:** with open network an agent (or code it runs) can send data anywhere, and `--network none` cannot be used once the agent runs inside the container and must reach its model provider.
- **Goal:** outbound connections from the sandbox are limited to a short allowlist, enforced outside the agent's control.
- **Scope (to be refined by the Functional and Technical Lead agents):**
  - Only the model provider's hosts (and, when asked, the package index during setup) are reachable; everything else fails.
  - Tests and the local server stay offline as in ITEM-001.
  - The allowlist is a visible file in the repository, changes to it need human approval.
- **Open questions:** mechanism with Colima and docker (proxy container on an internal network, firewall rules in the VM, DNS filtering)? How are other tools in the container (git, pip) handled?
- **Done when:** from inside the sandbox a connection to an allowlisted host works and a connection to any other host fails, also when the agent tries to bypass the proxy settings.
- **Depends on:** ITEM-003.

## ITEM-006 — Agent credentials

- **Status:** OPEN
- **Why:** the agent needs an API key to work. If it is stored in the workspace, in git, or in a mounted home directory, the sandbox leaks it.
- **Goal:** the agent receives a dedicated, limited credential in a controlled way and it never ends up in the workspace, in git, in logs or in the image.
- **Scope (to be refined by the Functional and Technical Lead agents):**
  - A dedicated key for the agent, never one of the keys stored in `app.db`, with a spending cap set at the provider.
  - A documented way to pass it to the container (environment variable at start, docker secret, or a file removed after start) and to rotate or revoke it.
  - A check that fails the commit when the key appears in the diff (see ITEM-008).
- **Open questions:** which passing method fits Colima and docker best? Who creates and rotates the key, and how often?
- **Done when:** a session started with the documented command can use the model; the key is not found in the workspace, in `git log -p`, in the image or in the logs.
- **Depends on:** ITEM-003.

## ITEM-007 — Safe pull gate

- **Status:** OPEN
- **Why:** work coming out of the sandbox is untrusted. Changes to files that define the isolation (`sandbox/`, hooks, scripts, `.gitignore`, `AGENTS.md`) could weaken it silently.
- **Goal:** `sandbox.sh pull` shows what is coming in and requires explicit approval for risky files before they reach the real repository.
- **Scope (to be refined by the Functional and Technical Lead agents):**
  - A diff report: files changed, additions, deletions, and the list of protected paths touched (`sandbox/`, `AGENTS.md`, `.gitignore`, git hooks, `*.sh`, `auth.py`, `server.py` security parts, `mailer.py`).
  - Fetch never runs hooks or scripts from the sandbox clone.
  - The pull stops until the owner confirms when a protected path is touched.
- **Open questions:** where is the list of protected paths kept, and who may change it?
- **Done when:** a test branch touching `sandbox/sandbox.sh` is flagged and not merged without confirmation; a branch touching only `tests/` passes with a plain report.
- **Depends on:** ITEM-001.

## ITEM-008 — Secret and personal-data scan

- **Status:** OPEN
- **Why:** real keys, IBANs, names or statement rows could slip into a commit, a fixture or a doc.
- **Goal:** commits are checked automatically for secrets and personal data, in the sandbox and again before a merge.
- **Scope (to be refined by the Functional and Technical Lead agents):**
  - A scan for API keys, the master key format, IBANs, email addresses outside `example.test`, and files named like protected ones.
  - Runs as a test (ITEM-000) and as part of `pull`.
  - No external service: a free, local tool or a small script.
- **Open questions:** a ready-made open source scanner or a project script? How are false positives (test fixtures) allowed?
- **Done when:** a commit with a fake key or IBAN fails the scan; the current repository passes.
- **Depends on:** ITEM-000.

## ITEM-009 — Disposable workspaces

- **Status:** OPEN
- **Why:** a long-lived workspace accumulates state, and a compromised session would stay compromised.
- **Goal:** a fresh, clean workspace per item or worktree, with limits on time and disk.
- **Scope (to be refined by the Functional and Technical Lead agents):**
  - `sandbox.sh reset` recreates the clean clone from the real repository, after checking nothing unpulled would be lost.
  - One workspace per item (and per task worktree), removed when the item ends.
  - Time and disk limits per session.
- **Open questions:** is the workspace kept after `DONE` for a while, and how are unpulled commits protected from a reset?
- **Done when:** `reset` produces a clean clone without losing unpulled commits unless confirmed; sessions stop at the limits.
- **Depends on:** ITEM-001.

## ITEM-011 — Isolation regression check

- **Status:** OPEN
- **Why:** upgrades to Colima, docker or the sandbox files can break the isolation without anyone noticing.
- **Goal:** `sandbox.sh verify` runs automatically before work starts and after each change that could affect isolation.
- **Scope (to be refined by the Functional and Technical Lead agents):**
  - `verify` runs at the start of every item and before each merge to `main`.
  - It also covers the new protections as they appear (agent file tools, egress allowlist, credentials).
  - A failure sets the item to `NEEDS HUMAN`.
- **Open questions:** who runs it when no CI exists (the Orchestrator, a git hook on the owner's machine)?
- **Done when:** a deliberately weakened sandbox (for example a mounted home directory) makes `verify` fail and blocks the item.
- **Depends on:** ITEM-001.

## ITEM-010 — Audit log

- **Status:** OPEN
- **Why:** after a session the owner cannot see what the agent ran or tried to reach.
- **Goal:** a log of commands run and network connections attempted, readable after the session.
- **Scope (to be refined by the Functional and Technical Lead agents):**
  - Commands, file operations outside the workspace that were denied, and blocked connections are recorded with timestamps.
  - The log lives outside the agent's reach and contains no secrets.
  - A short summary command for the owner.
- **Open questions:** how much detail is useful without producing noise? Where is the log stored and for how long?
- **Done when:** after a test session, the log lists the commands run and a blocked connection attempt, and the agent cannot edit it.
- **Depends on:** ITEM-003, ITEM-005.

## ITEM-012 — Browser/UI testing in the sandbox

- **Status:** OPEN
- **Why:** `financial-dashboard.html` has no automated UI tests (out of scope of ITEM-000), and a browser needs data to show something meaningful.
- **Goal:** UI checks run in the sandbox with a headless browser that reaches only the loopback server and uses throwaway or demo data.
- **Scope (to be refined by the Functional and Technical Lead agents):**
  - A headless browser in the image, reaching only `localhost`.
  - A few smoke tests: login, loading the dashboard, adding a movement, no console errors.
  - Uses data from a test server (ITEM-000) or Demo Mode (ITEM-013).
- **Open questions:** which free browser automation tool (Playwright, Selenium) fits the image size and the lock-in concern?
- **Done when:** the UI smoke tests pass inside the sandbox and fail when the page is deliberately broken.
- **Depends on:** ITEM-000, ITEM-001.

## ITEM-013 — Demo Mode

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
- **Depends on:** ITEM-000. Also useful for ITEM-012 (UI tests without real data).
