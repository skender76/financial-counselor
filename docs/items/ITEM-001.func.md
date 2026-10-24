# ITEM-001 — Development Sandbox: requisiti funzionali

Obiettivo: il ciclo di sviluppo (agenti, test, server locale) gira in un ambiente isolato che non raggiunge i dati e i segreti reali del proprietario.

## Decisioni del proprietario
- Meccanismo: **container OCI con Colima + CLI `docker`** (gratuito, standard, senza lock-in verso uno strumento di agenti).
- Dati: il sandbox lavora su un **checkout pulito** del repository (solo file tracciati da git).
- Commit: gli agenti committano dentro il sandbox; **solo il push resta manuale**, fuori dal sandbox.

## Protetto dal sandbox
`app.db`, `.env`, `dati.json`, `ai-config.json`, `esempi-estratti/` e le credenziali fuori dal progetto (`~/.ssh`, token, altre cartelle della home).

## US-1 — Avviare il ciclo di sviluppo con un solo comando
Come proprietario voglio una procedura documentata per preparare e avviare il sandbox.
- **AT-1.1** Dato un Mac con Colima e la CLI docker installati, quando lancio `sandbox/sandbox.sh setup`, allora il sandbox è pronto (profilo Colima dedicato, immagine costruita, checkout pulito creato).
- **AT-1.2** Quando lancio `sandbox/sandbox.sh test`, allora i test di ITEM-000 passano dentro il sandbox.

## US-2 — Dati e segreti non raggiungibili
- **AT-2.1** Dentro il sandbox non esistono `app.db`, `.env`, `dati.json`, `ai-config.json`, `esempi-estratti/`.
- **AT-2.2** Dentro il sandbox i percorsi della home del proprietario (`~/.ssh`, la cartella del progetto reale) non esistono e non si possono leggere.
- **AT-2.3** Prima e dopo un ciclo completo il `app.db` del proprietario è identico byte per byte (verifica opzionale con SHA-256, avviata solo dal proprietario).

## US-3 — Lavorare come gli agenti
- **AT-3.1** Dentro il sandbox gli agenti leggono e scrivono i file del progetto, eseguono `.venv/bin/python`, avviano `server.py` su un DB temporaneo, creano worktree e committano.
- **AT-3.2** `git push` dal sandbox fallisce (nessun remote, nessuna credenziale).
- **AT-3.3** Per impostazione predefinita il sandbox non ha rete; solo `--net` la abilita, in modo esplicito.

## US-4 — Recuperare il lavoro
- **AT-4.1** Il proprietario porta un branch del sandbox nel repository reale con `sandbox/sandbox.sh pull BRANCH`, senza che il sandbox veda il repository reale.

## US-5 — Documentazione
- **AT-5.1** `README.md` e `CLAUDE.md` descrivono prerequisiti, comandi e limiti del sandbox.
