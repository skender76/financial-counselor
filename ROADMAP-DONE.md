

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

## ITEM-005 — Egress allowlist

- **Status:** DONE
- **Why:** with open network an agent (or code it runs) can send data anywhere, and `--network none` cannot be used once the agent runs inside the container and must reach its model provider.
- **Goal:** outbound connections from the sandbox are limited to a short allowlist, enforced outside the agent's control.
- **Scope (to be refined by the Functional and Technical Lead agents):**
  - Only the model provider's hosts (and, when asked, the package index during setup) are reachable; everything else fails.
  - Tests and the local server stay offline as in ITEM-001.
  - The allowlist is a visible file in the repository, changes to it need human approval.
- **Open questions:** mechanism with Colima and docker (proxy container on an internal network, firewall rules in the VM, DNS filtering)? How are other tools in the container (git, pip) handled?
- **Owner decisions:** a dedicated proxy container plus an internal docker network created and destroyed per `--net` session (not VM firewall rules, not DNS filtering alone); initial allowlist host `api.anthropic.com`; the package index (PyPI) is reachable only with the separate, explicit `--allow-pip` flag.
- **Done when:** from inside the sandbox a connection to an allowlisted host works and a connection to any other host fails, also when the agent tries to bypass the proxy settings.
- **Depends on:** ITEM-003.
