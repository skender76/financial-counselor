# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Personal finance dashboard ("Dashboard Finanziaria") for a single user. UI text, code comments and log messages are all in **Italian**; keep new ones in Italian too. There is no build step, no package manager and no test suite. Git-tracked; `dati.json` (real financial data) and `anthropic-api-key.txt` are gitignored and must never be committed.

## Files

- `financial-dashboard.html` — the whole dashboard (HTML + CSS + JS in one file), served by `server.py`. The AI chat goes through `askAdvisor()`, which POSTs to `server.py`'s `/api/ask`; opening the HTML file directly (not via `localhost:8765`) leaves the chat and file sync without a backend. The `#fileSyncStatus` header indicator shows whether writes are reaching `dati.json`.
- `server.py` — Python 3 server on `localhost:8765`: serves the dashboard at `/` and exposes `GET/POST /api/data` backed by `dati.json` (atomic write via `.tmp` + `os.replace`), and `POST /api/ask` (`{prompt}` → `{text}`) which calls Claude via the `anthropic` SDK. Everything else is stdlib; the SDK is imported lazily so the server still runs without it. API key from `ANTHROPIC_API_KEY` or `anthropic-api-key.txt` next to the script (the double-click launcher doesn't inherit shell env vars). Everything is sent with `Cache-Control: no-store` on purpose (stale caching caused problems before).
- `avvia-dashboard.command` — double-clickable macOS launcher that runs `server.py`.

## Running

```bash
python3 server.py          # serves the dashboard, opens browser, writes ./dati.json
pip3 install anthropic     # only needed for the AI advisor chat
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
