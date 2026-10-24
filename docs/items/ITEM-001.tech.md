# ITEM-001 — Development Sandbox: piano tecnico

Nessun file di produzione (`server.py`, `auth.py`, ...) viene modificato.

## Architettura
- **Immagine** (`sandbox/Dockerfile`): Python 3.14 slim + `requirements.txt` in `/opt/venv`, utente non root. **Non contiene il codice del progetto**: nell'immagine entra solo `requirements.txt`, quindi nessun segreto può finirci.
- **Colima dedicato**: profilo `dashboard-sandbox` (contesto docker `colima-dashboard-sandbox`), avviato con `--mount "$SANDBOX_DIR:w"`. La VM vede solo la cartella del sandbox, non la home (di default Colima monta la home).
- **Workspace** (`$SANDBOX_DIR`, default `~/dashboard-sandbox`): contiene `repo/`, un `git clone --local` del repository reale. Un clone ha solo i file tracciati, quindi `app.db`, `.env`, `dati.json`, `ai-config.json`, `esempi-estratti/` non ci sono. `origin` è rimosso: nessun push possibile. È montato in `/work` (lettura/scrittura). I worktree di `AGENTS.md` §6 si creano in `/work` accanto a `repo/`.
- **Container** (`run`): `--network none` (loopback incluso, quindi il server di prova funziona), `--read-only` con `--tmpfs /tmp`, `--cap-drop ALL`, `no-new-privileges`, limiti di memoria e processi, utente = uid/gid dell'host. `/work/repo/.venv` è un link a `/opt/venv`, escluso da git con `.git/info/exclude`, così i comandi di `AGENTS.md` (`.venv/bin/python -m unittest ...`) valgono invariati.
- **Uscita del lavoro**: `sandbox.sh pull BRANCH` esegue sull'host `git fetch` dal clone del sandbox. Il push resta manuale.

## T1 — Immagine (`sandbox/Dockerfile`)
Test: `docker build` riesce; `python -c "import cryptography, xlrd, openpyxl"` nel container.

## T2 — Script (`sandbox/sandbox.sh`)
Sottocomandi: `setup`, `run [--net] [-- CMD...]`, `test`, `pull BRANCH`, `verify [--hash-app-db]`, `stop`, `help`. Variabili: `SANDBOX_DIR`, `SANDBOX_PROFILE`.
Contratto: nessun sottocomando legge `app.db`, `.env`, `dati.json`, `ai-config.json`. Solo `verify --hash-app-db`, lanciato dal proprietario, calcola lo SHA-256 di `app.db` prima e dopo.
Test: `verify` controlla AT-2.1, AT-2.2, AT-3.2, AT-3.3 e poi esegue i test (AT-1.2).

## T3 — Controlli statici (`tests/test_sandbox.py`)
Senza Docker: `bash -n` sullo script, il Dockerfile copia solo `requirements.txt`, lo script usa `--network none` come predefinito e non monta mai il percorso del repository reale. Il resto si verifica con `sandbox.sh verify` (richiede Colima).

## T4 — Documentazione
`README.md` (sezione «Sandbox di sviluppo») e `CLAUDE.md` (una riga).

## Rischi noti
- I permessi dei file sul volume dipendono dal tipo di mount di Colima: `verify` controlla che si possa scrivere.
- Gli agenti che chiamano API esterne hanno bisogno di `--net`: è una scelta esplicita per sessione.
