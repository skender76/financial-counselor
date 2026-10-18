# Dashboard Finanziaria

Dashboard di finanza personale multi-utente, con consulente AI integrato. Ogni utente ha account, dati e chiavi API separati. Nessun build, nessun package manager, nessun servizio esterno obbligatorio.

## Funzionalità

- Patrimonio netto liquido (esclusi fondo pensione/TFR e immobili), investimenti, immobili, mutuo, spese, trasferimenti e flussi.
- Addebiti differiti della carta di credito applicati al saldo alla data di scadenza.
- Mutuo ricorrente applicato automaticamente.
- Esportazione CSV e backup CSV versionato, con importazione protetta da doppia conferma.
- Chat con consulente AI: il modello propone un piano di calcolo, le espressioni sono valutate localmente (niente aritmetica fatta dall'LLM) e poi viene generata la risposta finale.
- Gestione delle chiavi API dalla pagina ("🔑 Chiavi API"), con supporto a più fornitori: Anthropic, OpenAI, Gemini, Mistral, DeepSeek, xAI, OpenRouter.
- Tema chiaro/scuro.

## Requisiti

- Python 3
- Un browser moderno (Chart.js è caricato da CDN)
- L'SDK del fornitore AI scelto, solo se vuoi usare la chat: `anthropic`, `openai` o `google-genai`
- `cryptography`, per cifrare chiavi API e segreti 2FA (obbligatorio online)
- `xlrd` e `openpyxl`, solo se vuoi importare gli estratti conto BPER (.xls) e Fineco (.xlsx)

## Avvio

```bash
python3 -m venv .venv
.venv/bin/python -m pip install anthropic   # oppure openai / google-genai
.venv/bin/python -m pip install xlrd openpyxl   # estratti conto BPER / Fineco
.venv/bin/python server.py
```

Il server si avvia su `http://localhost:8765`, apre il browser e salva tutto in `./app.db` (SQLite, permessi 600).
Su macOS puoi anche fare doppio clic su `avvia-dashboard.command`.

Per fermare il server usa Ctrl+C (o SIGTERM). "⏻ Esci" nella pagina chiude solo la sessione dell'utente. Se la porta 8765 è già occupata, viene aperta l'istanza già in esecuzione.

> Aprendo direttamente `financial-dashboard.html` dal disco la dashboard non funziona: serve il server.

## Account

La registrazione è **su invito** (predefinito): genera un codice monouso sul server con

```bash
.venv/bin/python auth.py create-invite
```

Per portare nell'account i dati esistenti (`dati.json` e `ai-config.json`) dopo aver registrato l'account: `.venv/bin/python auth.py import-legacy tua@email.it`. Password dimenticata: `.venv/bin/python auth.py reset-password tua@email.it` (chiude anche tutte le sessioni).

## Messa online (VPS + Caddy)

Il server Python ascolta solo su `127.0.0.1`; HTTPS lo fornisce Caddy (certificato automatico).

`/etc/caddy/Caddyfile`:

```
dash.example.com {
    reverse_proxy 127.0.0.1:8765
}
```

Variabili d'ambiente del servizio (es. unit systemd con `User=` dedicato e `EnvironmentFile=`):

```
DASHBOARD_PUBLIC_URL=https://dash.example.com
DASHBOARD_TRUST_PROXY=1
DASHBOARD_REGISTRATION=invite    # invite | open | closed
```

Con `DASHBOARD_PUBLIC_URL` https il cookie di sessione è `__Host-sid; Secure; HttpOnly; SameSite=Strict`, il server accetta solo quell'`Host` e invia HSTS. Altre variabili: `DASHBOARD_HOST`, `DASHBOARD_PORT`, `DASHBOARD_DB`. Genera la chiave maestra con `.venv/bin/python auth.py gen-master-key` e mettila in `DASHBOARD_MASTER_KEY` (conservala a parte dai backup: senza non si decifrano chiavi API e segreti 2FA; online il server non parte senza). Per le email (verifica indirizzo, password dimenticata, avvisi di sicurezza) imposta `DASHBOARD_SMTP_HOST`, `DASHBOARD_SMTP_PORT`, `DASHBOARD_SMTP_USER`, `DASHBOARD_SMTP_PASSWORD`, `DASHBOARD_SMTP_FROM` (`DASHBOARD_SMTP_SECURITY`: `starttls` predefinito, `ssl`, oppure `none` solo per un relay locale). Senza SMTP non c'è verifica email né recupero password. Fai backup di `app.db` (usa `sqlite3 app.db ".backup copia.db"`, non copiare il file mentre è in uso) e cifrali.

## Configurazione dell'AI

1. Avvia la dashboard e apri la sezione "🔑 Chiavi API".
2. Aggiungi una chiave, scegli il modello e attivala.

Le chiavi sono salvate per utente in `app.db` (tabella `user_ai_config`), mai nei dati della dashboard, quindi non finiscono in esportazioni o backup. La pagina non riceve mai il valore completo di una chiave. Solo Anthropic ha un modello predefinito: per gli altri fornitori indica il modello a mano.

## Struttura del progetto

| File | Ruolo |
| --- | --- |
| `financial-dashboard.html` | Tutta la dashboard (HTML, CSS e JS in un unico file) |
| `server.py` | Server: serve la pagina, gestisce le sessioni ed espone `GET/POST /api/data` sui dati dell'utente |
| `auth.py` | Utenti, sessioni, 2FA, token email, cifratura, dati e chiavi per utente (SQLite) e comandi di amministrazione |
| `mailer.py` | Invio email via SMTP |
| `ai_providers.py` | Livello AI indipendente dal fornitore e gestione delle chiavi |
| `avvia-dashboard.command` | Launcher per macOS |
| `app.db` | Account, dati finanziari e chiavi API di tutti gli utenti (ignorato da git) |

## Dati e sicurezza

- I dati di ogni utente stanno solo nel database (tabella `user_data`): la pagina non usa `localStorage`. Ogni modifica viene inviata al server, con controllo di concorrenza (`ETag` / `If-Match`) per evitare sovrascritture da più schede. Ogni query è limitata all'utente della sessione.
- Password con scrypt (sale per utente), minimo 12 caratteri; sessioni lato server (nel database c'è solo l'hash del token), cookie `HttpOnly` + `SameSite=Strict`, token CSRF su ogni POST, verifica dell'email alla registrazione, password dimenticata con link monouso (1 ora), verifica in due passaggi TOTP con codici di recupero (dalla pagina, "🔐 Sicurezza"), chiavi API e segreti 2FA cifrati con AES-256-GCM, CSP con nonce per gli `<script>`, blocco temporaneo dopo troppi tentativi falliti, limiti per utente su chat AI e importazioni.
- Il server accetta solo l'`Host` configurato e POST `application/json` con `Origin` coerente, per bloccare DNS rebinding e richieste da altri siti.
- `app.db` (e i vecchi `dati.json` / `ai-config.json`) sono in `.gitignore`: non vanno mai committati.
- Dopo aver modificato il codice Python, riavvia il server (la pagina HTML viene invece riletta a ogni richiesta).

## Aggiungere un fornitore AI

Crea una sottoclasse di `Provider` in `ai_providers.py` implementando `import_sdk`, `make_client` e `ask`, registrala in `PROVIDERS` e aggiungi il prefisso della chiave in `detectApiKeyProvider()` nella pagina. Per i fornitori compatibili con OpenAI basta una sottoclasse di `OpenAICompatibleProvider` con il suo `base_url`.

Per maggiori dettagli sull'architettura vedi `CLAUDE.md`.
