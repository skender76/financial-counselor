# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Personal finance dashboard ("Dashboard Finanziaria") for a single user. UI text, code comments and log messages are all in **Italian**; keep new ones in Italian too. There is no build step, no package manager and no test suite. Git-tracked; `dati.json` (real financial data) and `ai-config.json` (API keys) are gitignored and must never be committed.

## Files

- `financial-dashboard.html` — the whole dashboard (HTML + CSS + JS in one file), served by `server.py`. The AI chat goes through `askAdvisor()`, which POSTs to `server.py`'s `/api/ask`; `loadAdvisorInfo()` reads `/api/ai-info` at startup to show the active vendor/model under the chat; opening the HTML file directly (not via `localhost:8765`) leaves the chat and file sync without a backend. The `#fileSyncStatus` header indicator shows whether writes are reaching `dati.json`.
- `server.py` — Python 3 server on `localhost:8765`: serves the dashboard at `/` and exposes `GET/POST /api/data` backed by `dati.json` (atomic write via `.tmp` + `os.replace`), and delegates the AI endpoints (`POST /api/ask` `{prompt}` → `{text}`, `GET /api/ai-info`) to `ai_providers.py`. Otherwise stdlib only. Everything is sent with `Cache-Control: no-store` on purpose (stale caching caused problems before). Request hardening (`_reject_foreign_request`): every request must carry `Host: localhost:8765` or `127.0.0.1:8765` (blocks DNS rebinding), and every POST must be `Content-Type: application/json` (a cross-site page can't send that without a CORS preflight, which the server doesn't answer) — keep both when adding endpoints, and send JSON from the page. Graceful stop: `POST /api/shutdown` (used by the header's "⏻ Chiudi dashboard" button, which first awaits a final `syncToServerFile()`), Ctrl+C, SIGTERM or SIGHUP (Terminal window closed) all end `serve_forever()`, then wait on `DATA_LOCK` so an in-progress `dati.json` write finishes. `server.shutdown()` must run off the serving thread (`request_shutdown()`). If port 8765 is already taken, `main()` opens the running instance instead of crashing.
- `ai_providers.py` — vendor-neutral AI layer. `ask(prompt)` reads `ai-config.json` on every call (so switching vendor/key needs no restart), picks the `Provider` subclass named by `"provider"` (`anthropic`, `openai`, `gemini`), and falls back to the vendor's env var (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`) when `api_key` is empty. Each provider lazy-imports its official SDK, so only the chosen vendor's SDK needs installing. Adapters must translate every vendor error into `AdvisorError(message_in_italian, http_status)` — the page shows that message in the chat. To add a vendor: subclass `Provider`, implement `import_sdk`/`make_client`/`ask`, register it in `PROVIDERS`. `ai-config.example.json` is the committed template.
- `avvia-dashboard.command` — double-clickable macOS launcher; runs `server.py` with `.venv/bin/python` when `.venv` exists (it doesn't inherit shell env vars, so keys belong in `ai-config.json`).

## Running

```bash
python3 -m venv .venv && .venv/bin/python -m pip install anthropic   # once; for the AI chat install the chosen vendor's SDK (anthropic | openai | google-genai) — Homebrew Python blocks global pip
cp ai-config.example.json ai-config.json   # then fill in provider, api_key, model
.venv/bin/python server.py   # serves the dashboard, opens browser, writes ./dati.json (avvia-dashboard.command does this)
```

## Architecture (single-file vanilla JS + Chart.js from CDN)

- **State lives in `localStorage` under keys prefixed `skender_fin`** (e.g. `skender_fin_v5` main data, `_investments_v1`, `_properties_v1`, `_expenses_v1`, `_transfers_v1`, `_flows_v1`, `_chat_v1`, `_memory_v1`, `_theme`, `_last_modified_v1`). Each domain has `load*/save*` helpers and a `migrateLegacy*` function that lifts older fields out of the main `skender_fin_v5` object. Bump/migrate rather than silently changing the shape of stored data.
- **File sync**: `hydrateFromServerFileSync()` does a *synchronous* XHR to `/api/data` and copies every `skender_fin*` key into `localStorage` before anything else reads it — it must stay the first thing the script runs. Writes go back via `syncToServerFile()` (POSTs all `skender_fin*` keys), which is invoked from `touchLastModified()`. So **every data-mutating operation must call `touchLastModified()`**; otherwise the change is not persisted to `dati.json` and the backup-age check is wrong.
- `touchLastModified()` timestamp is distinct from `d.lastUpdated` (only set by the "Aggiorna dati" modal). CSV backup import compares against it: a backup not newer than current data can still be imported, but only after two explicit `confirm()` dialogs (summary with warning + definitive confirmation). Import writes via `save*()` helpers that don't sync, so the import calls `syncToServerFile()` explicitly at the end.
- **Startup automation** (bottom of the script): `migratePendingCcDueMonths()`, `applyMaturedCreditCardCharges()` (deferred credit-card charges hit the account balance on the due date) and `applyRecurringMortgage()` run on every load, then all `render*()` functions.
- **Net worth math** is in `render(d)`: liquid net worth deliberately excludes pension/TFR (Fondo Cometa) and properties.
- **Export/import**: CSV report and a versioned CSV backup (`BACKUP_FORMAT_MARKER`/`BACKUP_FORMAT_VERSION`), plus a recovery path for movements (`processRecoveredMovements`).
- **AI advisor chat**: two-pass flow in `sendChat()` — first prompt asks the model for a JSON calculation plan (`buildCalcPlanPrompt` → `parseCalcPlan`), expressions are evaluated locally by a small recursive-descent parser (`safeEval`, no `eval`), then a final prompt (`buildFinalPrompt`) includes the verified results plus `formatFinancialSnapshot(d)`. This exists to avoid LLM arithmetic errors — don't let the model compute numbers directly. A user-editable "memory" block is also fed into prompts. Market/tax context is a hardcoded snapshot, not a live feed.
- Theming uses CSS variables on `:root` with a `[data-theme="dark"]` override; charts are updated via `updateChartDefaultsForTheme()`.
