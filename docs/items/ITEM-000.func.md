# ITEM-000 — Test harness: requisiti funzionali

Obiettivo: poter eseguire test automatici ripetibili senza toccare dati reali.

## US-1 — Eseguire i test con un solo comando
Come sviluppatore voglio lanciare `.venv/bin/python -m unittest discover tests` e avere un esito chiaro.

- **AT-1.1** Dato un checkout pulito con `.venv` installato, quando lancio il comando, allora tutti i test passano.
- **AT-1.2** Dato un endpoint volutamente rotto (es. `/api/data` che non controlla `If-Match`), quando lancio il comando, allora almeno un test fallisce.

## US-2 — Non toccare mai dati reali
- **AT-2.1** Quando i test girano, `app.db`, `dati.json` e `ai-config.json` non vengono letti né scritti (il server di test usa `DASHBOARD_DB` su un file temporaneo e una chiave maestra di test).
- **AT-2.2** Il server di test non apre il browser, non invia email (SMTP disattivato) e ascolta solo su 127.0.0.1 su una porta libera.

## US-3 — Smoke test del comportamento esistente
- **AT-3.1** Registrazione (modo `open`), accesso e uscita funzionano; dopo l'uscita la sessione non vale più.
- **AT-3.2** Un accesso con password errata dà 401.
- **AT-3.3** `GET /api/data` senza sessione dà 401.
- **AT-3.4** `GET /api/data` restituisce un `ETag`; `POST /api/data` con l'`If-Match` giusto dà 200 e un nuovo `ETag`; con un `If-Match` vecchio dà 409; senza `If-Match` dà 428.
- **AT-3.5** Un POST con sessione ma senza `X-CSRF-Token` (o con uno sbagliato) dà 403.
- **AT-3.6** Un POST con `Content-Type` diverso da JSON dà 415; un `Host` non consentito dà 403.
- **AT-3.7** I dati di un utente non sono visibili a un altro utente.

## US-4 — Test di `statement_import.py`
- **AT-4.1** Revolut CSV, BPER conto `.xls`, BPER carta `.xls` e Fineco `.xlsx` (fixture sintetiche) producono i movimenti attesi (data, direzione, importo, tag).
- **AT-4.2** Cambi valuta Revolut e regolamenti carta BPER finiscono in `ignored`, non in `movements`; i movimenti prima del 2026-01-01 sono scartati.
- **AT-4.3** I controlli incrociati BPER (numero movimenti, totali) risultano `ok`, e un file con totali alterati dà `ok: false`.
- **AT-4.4** Conto, tipo, estensione o file non validi sollevano `ImportError_`.

## US-5 — Documentazione
- **AT-5.1** `README.md` e `CLAUDE.md` riportano il comando dei test.
