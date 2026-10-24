# ITEM-000 — Test harness: piano tecnico

Nessun file di produzione (`server.py`, `auth.py`, ...) viene modificato.

## Decisioni
- Solo `unittest` della libreria standard, in `tests/`.
- **`esempi-estratti/` non si usa**: è in `.gitignore` perché contiene IBAN, nomi e movimenti reali. Le fixture sono file **sintetici** in `tests/fixtures/`, generati una volta da `tests/make_fixtures.py` (usa `xlwt`, dipendenza solo di sviluppo, non richiesta per eseguire i test) e committati.
- Il server di test è un sottoprocesso di `server.py` avviato con un launcher `-c` che sostituisce `webbrowser.open` (il server apre il browser in locale) e poi esegue `server.py` come `__main__`.

## T1 — Helper del server (`tests/helpers.py`)
Contratto: `TestServer` (avvia/ferma), `Client` (cookie jar, CSRF, `request(method, path, body, headers)` → `(status, headers, json)`).
- Env: `DASHBOARD_PORT` libera, `DASHBOARD_DB` in una cartella temporanea, `DASHBOARD_REGISTRATION=open`, `DASHBOARD_MASTER_KEY` di test, nessuna variabile `DASHBOARD_SMTP_*` / `DASHBOARD_PUBLIC_URL` / `DASHBOARD_TRUST_PROXY` ereditata. HOME e `.env` non vengono letti.
- Attende che la porta risponda; ferma con SIGTERM.
- Test: coperti da T2.

## T2 — Smoke test API (`tests/test_api.py`)
Copre AT-3.1 … AT-3.7. Un server per classe (`setUpClass`). Email uniche per test.

## T3 — Fixture e test import (`tests/make_fixtures.py`, `tests/fixtures/*`, `tests/test_statement_import.py`)
Copre AT-4.1 … AT-4.4. Nessun dato personale: nomi, IBAN e importi inventati.

## T4 — Documentazione
`README.md` e `CLAUDE.md`: sezione sui test (AT-5.1).

## Verifica AT-1.2
Si rompe temporaneamente il controllo di `If-Match` in una copia e si verifica che i test falliscano (senza committare la modifica).
