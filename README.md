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

## Avvio rapido

```bash
./run.sh                       # crea .venv, installa le dipendenze, crea .env e avvia il server
./run.sh invite                # codice d'invito per registrare un utente
./run.sh import-legacy EMAIL   # porta dati.json e ai-config.json in un account
./run.sh reset-password EMAIL
```

Le impostazioni (chiave maestra, SMTP, URL pubblico…) stanno in `.env`, creato alla prima esecuzione e ignorato da git. Quello che segue è la procedura manuale equivalente.

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
| `sandbox/` | Sandbox di sviluppo (container su Colima, vedi «Sandbox di sviluppo») |
| `tests/` | Test automatici (vedi «Test») |
| `app.db` | Account, dati finanziari e chiavi API di tutti gli utenti (ignorato da git) |

## Test

```bash
.venv/bin/python -m unittest discover tests
```

I test usano solo la libreria standard. Avviano `server.py` su una porta libera con un database temporaneo e una chiave maestra di prova (registrazione aperta, email disattivate): non leggono né scrivono `app.db`, `dati.json`, `ai-config.json` o `.env`. Le fixture degli estratti conto in `tests/fixtures/` sono sintetiche (nessun dato reale) e si rigenerano con `tests/make_fixtures.py`.

## Sandbox di sviluppo

Il ciclo di sviluppo (agenti, test, server di prova) può girare in un container che non vede `app.db`, `.env`, `dati.json`, `ai-config.json`, `esempi-estratti/` né la tua home. Usa strumenti standard e gratuiti: Colima e la CLI docker.

```bash
brew install colima docker        # una volta
sandbox/sandbox.sh setup          # VM dedicata, immagine, checkout pulito in ~/dashboard-sandbox/repo
sandbox/sandbox.sh test           # i test nel sandbox
sandbox/sandbox.sh run            # shell nel sandbox (aggiungi --net per abilitare la rete)
sandbox/sandbox.sh verify         # controlla l'isolamento ed esegue i test
sandbox/sandbox.sh pull BRANCH    # porta un branch del sandbox nel repository reale
```

Come funziona: l'immagine contiene solo Python e le dipendenze; il codice arriva da un `git clone` locale (quindi solo file tracciati) montato in `/work`. La VM Colima ha un profilo dedicato che monta solo `~/dashboard-sandbox`, non la home. Il container non ha rete (`--network none`), ha il filesystem in sola lettura, nessuna capability e nessun remote git: gli agenti possono committare, ma il push si fa a mano dal repository reale, dopo `pull` e la revisione del diff. I worktree di `AGENTS.md` si creano in `/work` accanto a `repo/`.

Per far girare anche l'agente nel sandbox (così i suoi strumenti di file e la shell vedono solo `/work`):

```bash
# una volta: installa nell'immagine la CLI dell'agente che usi (comando di shell a tua scelta)
SANDBOX_AGENT_INSTALL='...' sandbox/sandbox.sh setup
# avvio, da un terminale; Ctrl+C lo ferma
sandbox/sandbox.sh agent --net --env NOME_VARIABILE -- <comando-dell-agente>
sandbox/sandbox.sh agent-stop     # ferma gli agenti rimasti in esecuzione
```

Storia e impostazioni dell'agente stanno in `~/dashboard-sandbox/agent-home` (fuori dal clone git, mai nei commit). Limiti noti: con `--net` la rete è aperta finché non esiste l'elenco degli host consentiti (ITEM-005); `--env NOME` inoltra una variabile dal tuo ambiente (mai il valore sulla riga di comando) e la gestione delle credenziali è ITEM-006. `verify --hash-app-db` confronta anche lo SHA-256 del tuo `app.db` prima e dopo: lancialo solo tu.

## Dati e sicurezza

- I dati di ogni utente stanno solo nel database (tabella `user_data`): la pagina non usa `localStorage`. Ogni modifica viene inviata al server, con controllo di concorrenza (`ETag` / `If-Match`) per evitare sovrascritture da più schede. Ogni query è limitata all'utente della sessione.
- Password con scrypt (sale per utente), minimo 12 caratteri; sessioni lato server (nel database c'è solo l'hash del token), cookie `HttpOnly` + `SameSite=Strict`, token CSRF su ogni POST, verifica dell'email alla registrazione, password dimenticata con link monouso (1 ora), verifica in due passaggi TOTP con codici di recupero (dalla pagina, "🔐 Sicurezza"), chiavi API e segreti 2FA cifrati con AES-256-GCM, CSP con nonce per gli `<script>`, blocco temporaneo dopo troppi tentativi falliti, limiti per utente su chat AI e importazioni.
- Il server accetta solo l'`Host` configurato e POST `application/json` con `Origin` coerente, per bloccare DNS rebinding e richieste da altri siti.
- `app.db` (e i vecchi `dati.json` / `ai-config.json`) sono in `.gitignore`: non vanno mai committati.
- Dopo aver modificato il codice Python, riavvia il server (la pagina HTML viene invece riletta a ogni richiesta).

## Aggiungere un fornitore AI

Crea una sottoclasse di `Provider` in `ai_providers.py` implementando `import_sdk`, `make_client` e `ask`, registrala in `PROVIDERS` e aggiungi il prefisso della chiave in `detectApiKeyProvider()` nella pagina. Per i fornitori compatibili con OpenAI basta una sottoclasse di `OpenAICompatibleProvider` con il suo `base_url`.

Per maggiori dettagli sull'architettura vedi `CLAUDE.md`.
