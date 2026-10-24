# ITEM-005 — Egress allowlist: functional requirements

Goal: dentro il sandbox (`sandbox/sandbox.sh agent --net ...`) le uscite di rete sono limitate a un elenco visibile di host; il resto fallisce, anche se l'agente tenta di aggirare le impostazioni del proxy.

## Owner decisions
- **Meccanismo:** un container-proxy dedicato è l'unico ponte verso Internet; il container dell'agente (e quello lanciato con `run --net`) sta su una rete docker *interna* (nessuna rotta verso l'esterno) e raggiunge solo il proxy. L'allowlist è la lista di domini del proxy. Niente regole nella VM Colima, niente filtro DNS da solo (un agente che si connette per IP noto lo aggirerebbe).
- **Host iniziale:** `api.anthropic.com` (usato da questa stessa sessione, Claude Code).
- **Indice pacchetti (PyPI):** non è incluso automaticamente con `--net`. Un'opzione separata ed esplicita lo aggiunge solo per quella sessione.
- **Fuori scope di questa decisione:** la fase di `sandbox.sh setup` (build dell'immagine) continua a usare la rete host direttamente, come oggi; non passa dal proxy.

## US-1 — Allowlist visibile e protetta
- **AT-1.1** Un file tracciato in `sandbox/` elenca gli host consentiti, uno per riga, con commenti; contiene inizialmente solo `api.anthropic.com`.
- **AT-1.2** Il file è un normale file del repository: le modifiche passano dalla stessa approvazione umana richiesta per ogni merge su `main` (sezione 7 di `AGENTS.md`); non c'è un meccanismo per cambiarlo da dentro una sessione nel sandbox.

## US-2 — Connessioni consentite funzionano
- **AT-2.1** Dentro una sessione avviata con `sandbox/sandbox.sh agent --net -- <comando>` (o `run --net`), una richiesta HTTPS verso un host in allowlist (es. `api.anthropic.com`) va a buon fine.

## US-3 — Connessioni non consentite falliscono
- **AT-3.1** Dentro la stessa sessione, una richiesta verso un host non in allowlist fallisce (nessuna risposta, non un blocco "soft" che un client potrebbe ignorare).
- **AT-3.2** Il fallimento vale anche se l'agente ignora le variabili `HTTP_PROXY`/`HTTPS_PROXY` e tenta una connessione diretta (per nome o per IP): il container non ha nessuna rotta di rete verso l'esterno all'infuori del proxy, quindi non c'è nulla da aggirare lato agente.

## US-4 — Nessuna regressione sull'isolamento esistente
- **AT-4.1** `sandbox/sandbox.sh test` e `sandbox/sandbox.sh run` (senza `--net`) restano completamente offline come in ITEM-001: nessuna rete è collegata, non solo filtrata.
- **AT-4.2** Il comando usato finora, `sandbox/sandbox.sh agent --net -- <comando>`, non richiede nuova sintassi per avere l'allowlist: `--net` la applica sempre.

## US-5 — Indice pacchetti su richiesta
- **AT-5.1** Un'opzione esplicita (`--allow-pip`), usabile solo insieme a `--net`, aggiunge `pypi.org` e `files.pythonhosted.org` all'allowlist effettiva solo per quella sessione.
- **AT-5.2** Senza `--allow-pip`, `pip install` verso l'indice pubblico fallisce anche con `--net`.

## US-6 — Visibilità sullo stato del proxy
- **AT-6.1** `sandbox/sandbox.sh status` segnala se l'immagine del proxy e le reti docker necessarie esistono, con lo stesso formato ok/FAIL degli altri controlli.
- **AT-6.2** `sandbox/sandbox.sh verify` controlla, con una vera richiesta, che un host in allowlist funzioni e uno fuori lista fallisca.

## Known limits (dichiarati, non da risolvere in questo item)
- Il proxy filtra per nome di dominio nella richiesta CONNECT (TLS), non decifra il traffico: un host consentito che fa da relay verso un altro host non in lista non è coperto. Non è lo scenario di minaccia di questo item.
