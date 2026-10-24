# AGENTS

Development workflow for the Consulente Finanziario ("Dashboard Finanziaria") project: which agents exist, what each one produces, and how a `ROADMAP.md` item goes from idea to `main`.

This file is written in English; the product itself (UI text, code comments, log messages) stays in **Italian**.

## 1. Prerequisites (read before doing anything)

- **`CLAUDE.md` is mandatory reading for every agent.** It describes the architecture and the hard rules of the project. This file never overrides it. Before editing a file, also read its notes in `docs/notes/` (listed in the Files section of `CLAUDE.md`).
- Rules that most often get broken, repeated here on purpose:
  - Never commit `app.db`, `dati.json` or `ai-config.json`; never read or print their contents.
  - Every data function takes `user_id` from the session, never from request input.
  - Never return an API key's `value` to the page.
  - Any new `<script>` must be a literal `<script` in `financial-dashboard.html` (it receives the CSP nonce).
  - Never use `localStorage`/`sessionStorage`/IndexedDB for data; use `store`.
  - Changes to the shape of stored data need a migration (`migrateLegacy*` / version bump).
  - New UI text, comments and log messages are in Italian.
- **Tests:** the test suite lives in `tests/` and runs with `.venv/bin/python -m unittest discover tests` (inside the sandbox, or from outside with `sandbox/sandbox.sh test`). It starts `server.py` on a throwaway DB; see `CLAUDE.md`. Each acceptance test of an item must be covered by an automated test or, when it cannot be automated, by a check written in the item's `.tech.md`.
- Python modules are loaded at startup: restart `server.py` after changing Python code before testing.

## 2. ROADMAP.md

`ROADMAP.md` lists features with an ID (`ITEM-XXX`), ordered by priority.

| Status | Meaning | Set by |
|---|---|---|
| `OPEN` | Not analyzed yet | human / initial |
| `READY FOR DEV` | `ITEM-XXX.func.md` and `ITEM-XXX.tech.md` exist and the Architect approved them | Architect Agent |
| `IN PROGRESS` | Development or integration running on `feature/ITEM-XXX` | Orchestrator |
| `BLOCKED` | Waiting for a dependency or an external decision | Orchestrator |
| `NEEDS HUMAN` | Loop limit reached or a human gate is pending | Orchestrator |
| `DONE` | Merged on `main`, tests green, documented | Documentation Agent, after the merge |

- When an item becomes `DONE`, its full text moves to `ROADMAP-DONE.md` and `ROADMAP.md` keeps only its title and status, with a pointer to that file. Agents read `ROADMAP.md` by default and open `ROADMAP-DONE.md` only when they need the history of a finished item.
- `ITEM-XXX.func.md`: user stories; each one has a list of acceptance tests.
- `ITEM-XXX.tech.md`: technical tasks; each one has a target, the files/sections it may touch, its tests, and the contracts/interfaces shared with the other tasks.
- Both files live in `docs/items/` unless the repository already uses another folder.

## 3. Flow

```
Human picks item (via Orchestrator)
  → 1. Functional Agent        → ITEM-XXX.func.md
  → 2. Technical Lead Agent    → ITEM-XXX.tech.md
  → 3. Architect Agent         → approves (READY FOR DEV) or sends feedback back to step 2   [max 3 rounds]
  → 4. Development Agents      → code + tests on worktrees of feature/ITEM-XXX
  → 5. Integrator & Tester     → merges tasks on feature/ITEM-XXX, runs all tests
        failure → routed by type (section 5)                                                [max 3 fix rounds]
  → 6. Documentation Agent     → docs updated
  → 7. Human approval          → required before merging
  → 8. Final Merger Agent      → merge on main, rerun tests, set DONE
```

## 4. Agents

| Agent | Input | Output | Done when |
|---|---|---|---|
| **Orchestrator** | `ROADMAP.md`, outputs of all agents | Next item proposal for the human, status updates, handoffs | Every handoff has the input the next agent needs; it never writes feature code. Before each handoff it runs `sandbox/sandbox.sh status`, and `sandbox/sandbox.sh verify` once at the start of every item (section 8). A failure stops the handoff and sets `NEEDS HUMAN`. |
| **Functional Agent** | The selected item | `ITEM-XXX.func.md` | Every user story has at least one acceptance test, in checkable form (given/when/then). |
| **Technical Lead Agent** | `ITEM-XXX.func.md`, `CLAUDE.md`, existing code | `ITEM-XXX.tech.md` | Tasks have a clear target and tests; contracts/interfaces between tasks are defined; each task lists the file regions it may edit (see section 6); every acceptance test is covered by a task. |
| **Architect Agent** | `ITEM-XXX.tech.md` (and `.func.md` for context) | Approval, or written feedback | Checks consistency with `CLAUDE.md` and the broader design. On feedback the **Technical Lead rewrites `ITEM-XXX.tech.md`**; the `.func.md` changes only if a requirement is actually wrong. With no feedback the item becomes READY FOR DEV. |
| **Development Agents** | One task of `ITEM-XXX.tech.md` | Code and tests for that task, committed on the task's worktree branch | Task tests pass. One agent per task; a task that depends on another waits for it. |
| **Integrator & Tester Agent** | All finished tasks | Integrated `feature/ITEM-XXX`, test report | Acceptance and unit tests all pass. Otherwise it classifies each failure (section 5). |
| **Documentation Agent** | Passing feature branch | Updated `README`/`CLAUDE.md` when architecture, endpoints, env vars or stored data change; item notes | Docs match the code. It does **not** set DONE yet. |
| **Final Merger Agent** | Human-approved feature branch | Merge on `main`, test run on `main`, `ROADMAP.md` → DONE (full text moved to `ROADMAP-DONE.md`, a stub left behind) | Tests green on `main`. If they fail after the merge: revert the merge and set `NEEDS HUMAN`. |

## 5. Failures and loop limits

Each loop has its own counter; when a limit is hit the Orchestrator writes a report (what was tried, what fails, logs) in `docs/items/ITEM-XXX.report.md`, sets `NEEDS HUMAN` and stops.

| Loop | Limit |
|---|---|
| Architect ↔ Technical Lead | 3 rounds |
| Integrator failures routed back | 3 rounds in total |

Route each failing test by cause, not always back to the Technical Lead:

- **Code bug** (the design is right) → the relevant Development Agent.
- **Design or contract flaw** (tasks do not fit together, wrong interface) → Technical Lead, then Architect again.
- **Wrong or ambiguous requirement** → Functional Agent, then the whole chain from step 2.
- **Test or environment problem** → fix the test/setup, not the feature.

## 6. Branches and parallel work

- Feature branch: `feature/ITEM-XXX`. Task worktrees: `/work/wt-ITEM-XXX-T<n>` (inside the sandbox, next to `repo/`) on branch `feature/ITEM-XXX-T<n>`, created with `git worktree add`.
- Almost all UI code lives in `financial-dashboard.html`, so parallel tasks conflict easily. The Technical Lead must assign non-overlapping regions (named `// =====` sections or separate functions) to each task, and tasks that must edit the same region are made sequential, not parallel.
- The Integrator merges task branches into `feature/ITEM-XXX` one at a time, resolving conflicts before running tests.
- Commits are in Italian, one logical change each, with the attribution line required by the session.

## 7. Human gates and forbidden actions

A human must approve:

- the item picked from the roadmap;
- the merge on `main` (step 7), with the diff and the test report in front of them;
- any change that touches `auth.py`, the security parts of `server.py` (sessions, CSRF, CSP, Host/Origin checks, encryption), `mailer.py`, or the shape of stored data.

Agents must never: push, deploy or restart the VPS; touch or read `app.db`, `dati.json`, `ai-config.json` or any real API key; rewrite git history on shared branches; skip or weaken a failing test to make it pass.

## 8. The sandbox is mandatory

All development happens in the sandbox (`sandbox/`, see `README.md` and `CLAUDE.md`):

- Agents run **inside** it: `sandbox/sandbox.sh agent --net -- <agent command>` from a terminal. Tests, the local server, worktrees (`/work/wt-...`) and commits all happen there, on the clean clone `~/dashboard-sandbox/repo`.
- Work reaches the real repository only with `sandbox/sandbox.sh pull BRANCH`, followed by the human review of section 7. `git push` is not possible from the sandbox and stays manual.
- `sandbox/require-sandbox.sh` is the guard: it exits with an error when it is not run inside the sandbox. The project hook in `.claude/settings.json` calls it before every tool call, so Claude Code refuses to work outside the sandbox. Other agents or wrappers must call it before doing anything.
- The only way around it is the owner's emergency override, `DASHBOARD_ALLOW_HOST=1`, and every use is recorded in `~/dashboard-sandbox/override.log`. An agent must never set it by itself.
- The guard prevents mistakes and omissions. It is not a barrier against a hostile agent: that is the job of the container isolation (ITEM-003) and, later, the network allowlist (ITEM-005).
