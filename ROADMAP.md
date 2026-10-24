# ROADMAP

Features ordered by priority. The workflow and the meaning of each status are defined in `AGENTS.md`.

Statuses: `OPEN` | `READY FOR DEV` | `IN PROGRESS` | `BLOCKED` | `NEEDS HUMAN` | `DONE`

## ITEM-000 — Test harness

- **Status:** OPEN
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
