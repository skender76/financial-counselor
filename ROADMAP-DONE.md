# ROADMAP-DONE

Full text of the `ROADMAP.md` items that reached `DONE`, moved here by the Final Merger Agent. Agents do **not** read this file by default: open it only when the history of a finished item is needed (the requirements and tasks of each item are also in `docs/items/ITEM-XXX.func.md` and `.tech.md`).

## ITEM-000 — Test harness

- **Status:** DONE
- **Why:** the project has no test suite, but the development workflow requires unit and acceptance tests at every stage. No other item can reach `READY FOR DEV` until this one is `DONE`.
- **Goal:** a repeatable way to run automated tests without touching real data.
- **Scope (to be refined by the Functional and Technical Lead agents):**
  - `tests/` folder with Python `unittest` tests, run with `.venv/bin/python -m unittest discover tests`.
  - Tests use a throwaway SQLite DB (`DASHBOARD_DB` pointing to a temporary file) and never read or write `app.db`, `dati.json` or `ai-config.json`.
  - A helper that starts `server.py` on a free port with a test configuration (registration open, SMTP disabled, test master key) and stops it afterwards.
  - First smoke tests covering the existing behavior: register/login/logout, `GET`/`POST /api/data` with `ETag`/`If-Match` (200, 409, 428), CSRF rejection, and an endpoint that requires a session returning 401 without one.
  - Unit tests for `statement_import.py` using the files in `esempi-estratti/` (check that none contains real personal data first; otherwise use anonymized fixtures).
  - Document the test command in `README.md` and `CLAUDE.md`.
- **Out of scope:** browser/UI tests of `financial-dashboard.html` (to be evaluated in a later item).
- **Done when:** the test command runs green on a clean checkout, and a deliberately broken endpoint makes it fail.

## ITEM-001 — Development Sandbox

- **Status:** DONE
- **Why:** the project handles real financial data and secrets, and the development agents run commands on the owner's machine. Today nothing technically stops an agent from reading or changing them; `AGENTS.md` only forbids it in writing.
- **Goal:** the whole development loop (agents, tests, local server) runs in a sandbox that cannot reach the owner's real data and secrets.
- **Scope (to be refined by the Functional and Technical Lead agents):**
  - Protected from the sandbox: `app.db`, `.env` (it holds `DASHBOARD_MASTER_KEY`), `dati.json`, `ai-config.json`, and credentials outside the project (SSH keys, tokens, other folders in the home directory).
  - Inside the sandbox the agents can read and write the project files, run `.venv/bin/python`, run the tests (ITEM-000) and start `server.py` on a throwaway DB with a test master key.
  - Network access is limited to what the work needs (for example package installation); `git push` and the production VPS are not reachable from inside it.
  - One documented command or procedure to start the development loop in the sandbox.
- **Open questions (the Technical Lead Agent must settle them with the owner):**
  - Which mechanism: Claude Code sandbox settings, a container, a separate OS user, or a VM?
  - Does the sandbox work with git worktrees (`AGENTS.md`, section 6), and how do commits get out of it?
  - Is a copy of the project with anonymized data enough, or must the sandbox use a clean checkout?
- **Out of scope:** protecting the production server; browser/UI testing.
- **Done when:** from inside the sandbox, reading `app.db`, `.env`, `dati.json`, `ai-config.json` and `~/.ssh` fails; the test command passes; and the owner's own `app.db` is byte-identical before and after a full run of the loop.
- **Depends on:** ITEM-000 (to prove the tests pass inside the sandbox).
