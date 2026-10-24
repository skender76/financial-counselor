# ITEM-005 — Egress allowlist: technical plan

Nessun file di produzione (`server.py`, `auth.py`, ...) è modificato: solo `sandbox/` e `tests/`.

## Design
- **Un proxy effimero per sessione**, non un proxy condiviso a lungo termine: ogni chiamata a `container_exec` con rete filtrata crea una rete docker interna dedicata e un container-proxy dedicato, entrambi rimossi alla fine. Due sessioni `--net` in parallelo non condividono stato e non si influenzano.
- **Rete interna** (`dk network create --internal <nome univoco>`): nessuna rotta verso l'esterno. Il container dell'agente/comando si collega solo a questa rete, quindi non ha interfaccia di rete alternativa da usare per aggirare il proxy (non è una policy che l'agente potrebbe ignorare, è l'assenza fisica di una rotta).
- **Container-proxy** (`dashboard-sandbox-proxy:latest`, Alpine + tinyproxy, build separata in `sandbox/proxy/Dockerfile`): parte su `--network bridge` (rete Internet reale, la stessa NAT che oggi dà accesso con `--net`), poi viene collegato anche alla rete interna della sessione (`dk network connect`). Stesse restrizioni del container principale: `--read-only`, `--cap-drop ALL`, `--security-opt no-new-privileges`, nessun volume scrivibile dell'host tranne il file di filtro (montato `:ro`).
- **Allowlist** (`sandbox/allowlist.txt`, tracciato in git): un host per riga, `#` per i commenti. Letta da `sandbox.sh` **sull'host**, mai dentro il container, e trasformata in un file di filtro per tinyproxy (`FilterDefaultDeny Yes`, un pattern regex per riga, ancorato ed escape dei punti: `^host\.consentito$`). Con `--allow-pip` si aggiungono `pypi\.org` e `files\.pythonhosted\.org` solo al filtro di quella sessione; il file `sandbox/allowlist.txt` non cambia.
- **Variabili nel container filtrato:** `HTTP_PROXY`/`HTTPS_PROXY`/`http_proxy`/`https_proxy=http://<nome-proxy-sessione>:8888`, risolto via il DNS interno di docker sulla rete dedicata. Se l'agente le ignora e tenta una connessione diretta (per nome o per IP), il kernel del container restituisce "rete irraggiungibile": non c'è un salto successivo da provare.
- **`run`/`test` senza `--net`** restano `--network none`: nessuna rete collegata, comportamento di ITEM-001 invariato.
- **Setup del proxy, non del filtro:** l'immagine del proxy si costruisce in `cmd_setup` (serve la rete, come per l'immagine principale); il filtro invece si genera a ogni invocazione con `--net`, perché dipende da `--allow-pip`.

## T1 — Immagine e filtro del proxy (`sandbox/proxy/Dockerfile`, `sandbox/proxy/tinyproxy.conf`, `sandbox/allowlist.txt`)
- `Dockerfile`: Alpine + `tinyproxy`, nessun codice del progetto copiato (stesso principio del Dockerfile principale), porta 8888, legge `/etc/tinyproxy/filter` (montato a runtime, non nell'immagine).
- `tinyproxy.conf`: `FilterDefaultDeny Yes`, `Filter /etc/tinyproxy/filter`, `FilterType fqdn` (l'host della richiesta/CONNECT, non l'URL completo), logging su stdout.
- `sandbox/allowlist.txt`: solo `api.anthropic.com` inizialmente, con un commento che spiega il formato e che le modifiche passano dall'approvazione umana al merge.

## T2 — `sandbox.sh`: rete filtrata, build del proxy, generazione del filtro, `--allow-pip`
- `cmd_setup`: aggiunge la build di `dashboard-sandbox-proxy:latest` (stesso pattern di contesto temporaneo del build principale).
- Nuove funzioni: `allowlist_filter_file(allow_pip)` (scrive il file di filtro in `$SANDBOX_DIR`, ritorna il percorso), `start_proxy(net_name)` (crea la rete, avvia il proxy, lo collega, ritorna il nome del container), `stop_proxy(net_name, proxy_name)` (ferma il proxy, rimuove la rete; chiamata anche in un `trap` per non lasciare reti o container orfani se il comando fallisce).
- `container_exec`: il parametro di rete diventa `none` oppure `egress` (non più `bridge` diretto); con `egress` fa partire proxy + rete dedicati, imposta le variabili `*_PROXY` sul container principale, poi pulisce.
- `sandbox_run`/`cmd_run`/`cmd_agent`: `--net` seleziona `egress` invece di `bridge`. `cmd_agent` e `cmd_run` accettano anche `--allow-pip` (solo insieme a `--net`; errore chiaro altrimenti), passato a `allowlist_filter_file`.
- `cmd_status`: aggiunge il controllo "immagine del proxy presente".
- `cmd_verify`: aggiunge, nella modalità `--net` di un comando di prova, una richiesta che deve riuscire verso l'host in allowlist e una che deve fallire verso un host fuori lista (vedi T3 per i dettagli).

## T3 — Test (`tests/test_sandbox.py`)
Tutti statici (niente Docker/Colima nella CI, come per gli altri test del sandbox):
- sintassi di `sandbox.sh` ancora valida (`bash -n`);
- `sandbox/proxy/Dockerfile` non copia codice del progetto;
- `tinyproxy.conf` contiene `FilterDefaultDeny Yes` e il percorso del filtro;
- `sandbox/allowlist.txt` esiste, contiene `api.anthropic.com` e nessuna riga non commentata vuota o malformata;
- la funzione di generazione del filtro produce pattern ancorati ed escape dei punti per ogni host dell'allowlist, e aggiunge `pypi.org`/`files.pythonhosted.org` solo quando richiesto;
- `--net` non usa più `--network bridge` direttamente nel container principale (deve usare la rete dedicata filtrata);
- `--allow-pip` senza `--net` è un errore con un messaggio chiaro, prima di toccare docker;
- `run`/`test` senza `--net` restano `--network none` (nessuna regressione su ITEM-001);
- il codice che avvia/ferma il proxy compare in un solo punto (stesso principio già testato per `dk run`), per evitare che un percorso bypassi le restrizioni.

## Manual verification (owner, su Colima reale)
1. `sandbox/sandbox.sh setup` (ricostruisce anche l'immagine del proxy).
2. `sandbox/sandbox.sh run --net -- curl -sS -o /dev/null -w '%{http_code}\n' https://api.anthropic.com` → riesce.
3. `sandbox/sandbox.sh run --net -- curl -sS -o /dev/null -w '%{http_code}\n' https://example.com` → fallisce (nessuna risposta, non solo un codice di errore HTTP).
4. `sandbox/sandbox.sh run --net -- python3 -c "import socket; socket.create_connection(('1.1.1.1', 443), 3)"` (bypass diretto, ignorando le variabili proxy) → fallisce con rete irraggiungibile.
5. `sandbox/sandbox.sh run --net --allow-pip -- python3 -m pip index versions requests` → riesce; senza `--allow-pip` lo stesso comando fallisce.
6. `sandbox/sandbox.sh run -- curl https://api.anthropic.com` (senza `--net`) → fallisce come oggi (nessuna rete).
7. `sandbox/sandbox.sh verify` end-to-end.

## Known limits
Il filtro guarda il nome host della richiesta (CONNECT per HTTPS), non il contenuto cifrato: un host in allowlist che facesse da relay verso altri host non è bloccato da questo meccanismo. Non è lo scenario d'uso previsto (il model provider non è un relay generico) e non è nello scope di questo item.
