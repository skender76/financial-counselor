# HOWTORUN — come avviare una sessione di sviluppo (dopo ITEM-004)

Il sandbox è obbligatorio (`AGENTS.md`, sezione 8): una sessione di Claude Code avviata
direttamente sul Mac viene bloccata dall'hook `.claude/settings.json`. Si lavora così.
Tutti i comandi si lanciano dalla cartella del progetto, in un terminale normale
(l'agente richiede un terminale vero: non usare `!` dentro Claude Code).

## A. Una volta sola (o quando cambiano `sandbox/` o il Dockerfile)

```bash
brew install colima docker

# Costruisce la VM, l'immagine (con la CLI di Claude Code) e il clone pulito.
SANDBOX_AGENT_INSTALL='apt-get update && apt-get install -y --no-install-recommends nodejs npm && npm install -g @anthropic-ai/claude-code && rm -rf /var/lib/apt/lists/*' \
  sandbox/sandbox.sh setup

sandbox/sandbox.sh verify      # isolamento + test: tutto deve essere "ok"
```

Rilancia `setup` con lo stesso comando dopo ogni modifica a `sandbox/Dockerfile`
(senza `SANDBOX_AGENT_INSTALL` l'immagine perde Claude Code).

## B. Ogni sessione

### 1. Controllo rapido
```bash
sandbox/sandbox.sh status      # VM, immagine, clone, segno nell'immagine: tutto "ok"
```
Se fallisce, rilancia il comando di `setup` della sezione A.

### 2. Porta nel clone il codice aggiornato
```bash
git -C ~/dashboard-sandbox/repo fetch "$PWD" main
git -C ~/dashboard-sandbox/repo checkout -B main FETCH_HEAD
git -C ~/dashboard-sandbox/repo checkout -b feature/ITEM-XXX      # il branch dell'item su cui lavori
```
(Se il `checkout` si rifiuta per file modificati, non forzare: guarda prima cosa c'è nel clone
con `sandbox/sandbox.sh run -- git status`.)

### 3. Segna lo stato di `app.db` (controllo opzionale ma consigliato)
```bash
shasum -a 256 app.db > ~/app.db.sha
```

### 4. Avvia l'agente nel sandbox
```bash
sandbox/sandbox.sh agent --net -- claude
```
Al primo avvio fai il login con il link che compare. Il login resta in
`~/dashboard-sandbox/agent-home`. Poi dai il compito, per esempio:
> Lavora su ITEM-XXX seguendo AGENTS.md e ROADMAP.md.

Si ferma con `/exit` (o Ctrl+C). Se resta bloccato, da un altro terminale:
```bash
sandbox/sandbox.sh agent-stop
```

### 5. Prima di uscire, fai controllare che i commit ci siano
Chiedi all'agente: «Mostrami l'output di `git branch` e di `git log --oneline -3`».

### 6. Porta fuori il lavoro e rivedilo
```bash
sandbox/sandbox.sh pull feature/ITEM-XXX
git diff main..feature/ITEM-XXX --stat
shasum -a 256 -c ~/app.db.sha          # deve rispondere "app.db: OK"
```
Leggi il diff con attenzione, soprattutto se tocca `sandbox/`, `.claude/`, `AGENTS.md`,
`.gitignore`, `auth.py`, `server.py`, `mailer.py`: sono i file che definiscono
l'isolamento o la sicurezza. Il codice che arriva dal sandbox non va eseguito sul Mac
prima della revisione: per provarlo usa `sandbox/sandbox.sh test`.

### 7. Merge e push (a mano tua)
Il merge su `main` richiede la tua approvazione (`AGENTS.md`, sezione 7). Il push non è
possibile dal sandbox e resta a mano tua, dal repository reale.

## C. Emergenze e manutenzione

| Cosa | Comando |
| --- | --- |
| Shell nel sandbox (senza rete) | `sandbox/sandbox.sh run` |
| Esegui i test nel sandbox | `sandbox/sandbox.sh test` |
| Ferma gli agenti rimasti accesi | `sandbox/sandbox.sh agent-stop` |
| Ferma la VM del sandbox | `sandbox/sandbox.sh stop` |
| Controllo completo dell'isolamento | `sandbox/sandbox.sh verify` |
| Controllo completo + confronto di `app.db` | `sandbox/sandbox.sh verify --hash-app-db` |
| Lavorare sul Mac **solo se non hai alternative** | `DASHBOARD_ALLOW_HOST=1 claude` |

L'ultima riga è l'uscita di emergenza, solo tua: ogni uso viene scritto in
`~/dashboard-sandbox/override.log`. Un agente non deve mai impostarla da solo.

## Limiti noti

- Con `--net` la rete del sandbox è aperta, finché non c'è l'elenco degli host consentiti (ITEM-005).
- Il login dell'account resta come file in `~/dashboard-sandbox/agent-home` (ITEM-006). Per
  revocarlo cancella quella cartella o fai il logout dal tuo account Claude.
- La guardia evita errori e dimenticanze; non ferma un agente ostile: per quello c'è
  l'isolamento del container.
