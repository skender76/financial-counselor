

## ITEM-004 — Sandbox-only enforcement

- **Status:** DONE
- **Why:** nothing in `AGENTS.md` says development must happen in the sandbox, and nothing stops an agent from running on the host.
- **Goal:** the development workflow requires the sandbox, in writing and technically.
- **Scope (to be refined by the Functional and Technical Lead agents):**
  - `AGENTS.md` states that all commands, worktrees and commits happen in the sandbox and that branches reach the real repository only through `sandbox.sh pull` after human review.
  - A start script (or a hook) that refuses to start an agent session outside the sandbox, with a clear error.
  - The Orchestrator checks the sandbox is up (`verify` passes) before handing work to any agent.
- **Open questions:** how is "inside the sandbox" detected reliably (a marker file in the image, an environment variable set by `sandbox.sh`)? What is the owner's emergency override, and how is its use recorded?
- **Done when:** starting an agent session outside the sandbox fails with an explanatory message; starting it through the documented command works; `AGENTS.md` describes the rule.
- **Depends on:** ITEM-003.
