#!/usr/bin/env bash
# Sandbox di sviluppo: container OCI su Colima + CLI docker (vedi README.md).
#
#   sandbox/sandbox.sh setup                 prepara tutto (una volta)
#   sandbox/sandbox.sh run [--net] [--allow-pip] [-- CMD]
#                                            esegue CMD nel sandbox (default: bash)
#   sandbox/sandbox.sh agent [--net] [--allow-pip] [--env NOME]... -- CMD
#                                            avvia un agente (CMD) DENTRO il sandbox
#   sandbox/sandbox.sh agent-stop            ferma gli agenti rimasti in esecuzione
#   sandbox/sandbox.sh test                  esegue i test nel sandbox
#   sandbox/sandbox.sh pull BRANCH           porta un branch del sandbox nel repository reale
#   sandbox/sandbox.sh status                controllo rapido (VM, immagine, clone, segno nell'immagine)
#   sandbox/sandbox.sh verify [--hash-app-db]  controlla l'isolamento ed esegue i test
#   sandbox/sandbox.sh stop                  ferma la VM del sandbox
#
# --net collega una rete filtrata (ITEM-005): solo gli host elencati in
# sandbox/allowlist.txt sono raggiungibili, tramite un proxy dedicato alla
# sessione; il resto fallisce, anche se qualcosa dentro ignora le variabili
# HTTP_PROXY/HTTPS_PROXY e tenta una connessione diretta (il container non ha
# nessun'altra rotta d'uscita). --allow-pip (richiede --net) aggiunge
# pypi.org e files.pythonhosted.org solo per quella sessione.
#
# Variabili: SANDBOX_DIR (default ~/dashboard-sandbox), SANDBOX_PROFILE (default
# dashboard-sandbox), SANDBOX_AGENT_INSTALL (comando di shell eseguito nella
# costruzione dell'immagine per installare la CLI dell'agente; vuoto = nessuna).
# Il push non è possibile dal sandbox: si fa a mano dal repository reale.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SANDBOX_DIR="${SANDBOX_DIR:-$HOME/dashboard-sandbox}"
PROFILE="${SANDBOX_PROFILE:-dashboard-sandbox}"
AGENT_INSTALL="${SANDBOX_AGENT_INSTALL:-}"
IMAGE="dashboard-sandbox:latest"
PROXY_IMAGE="dashboard-sandbox-proxy:latest"
CONTEXT="colima-$PROFILE"
AGENT_LABEL="dashboard-sandbox=agent"
PROTECTED=(app.db .env dati.json ai-config.json esempi-estratti)
ALLOWLIST="$REPO_ROOT/sandbox/allowlist.txt"
PIP_HOSTS=(pypi.org files.pythonhosted.org)

die() { echo "ERRORE: $*" >&2; exit 1; }
dk() { docker --context "$CONTEXT" "$@"; }

need_tools() {
  command -v colima >/dev/null || die "manca colima (brew install colima docker)."
  command -v docker >/dev/null || die "manca la CLI docker (brew install colima docker)."
}

cmd_setup() {
  need_tools
  mkdir -p "$SANDBOX_DIR/agent-home"
  # La VM monta solo la cartella del sandbox, non la home.
  if ! colima status --profile "$PROFILE" >/dev/null 2>&1; then
    echo "Avvio la VM Colima (profilo $PROFILE)..."
    colima start --profile "$PROFILE" --cpu 2 --memory 4 --disk 20 \
      --mount "$SANDBOX_DIR:w" --mount-type virtiofs
  fi
  echo "Costruisco l'immagine (serve la rete, solo qui)..."
  # Contesto di build = una cartella temporanea con il solo requirements.txt.
  local ctx; ctx="$(mktemp -d)"
  cp "$REPO_ROOT/sandbox/Dockerfile" "$REPO_ROOT/requirements.txt" "$ctx/"
  dk build --build-arg "AGENT_INSTALL=$AGENT_INSTALL" -t "$IMAGE" "$ctx"
  rm -rf "$ctx"

  echo "Costruisco l'immagine del proxy d'uscita (ITEM-005, serve la rete, solo qui)..."
  local pctx; pctx="$(mktemp -d)"
  cp "$REPO_ROOT/sandbox/proxy/Dockerfile" "$REPO_ROOT/sandbox/proxy/tinyproxy.conf" "$pctx/"
  dk build -t "$PROXY_IMAGE" "$pctx"
  rm -rf "$pctx"

  if [ ! -d "$SANDBOX_DIR/repo/.git" ]; then
    echo "Creo il checkout pulito in $SANDBOX_DIR/repo..."
    git clone --quiet --local --no-hardlinks "$REPO_ROOT" "$SANDBOX_DIR/repo"
    git -C "$SANDBOX_DIR/repo" remote remove origin   # nessun push possibile
    git -C "$SANDBOX_DIR/repo" config user.name "$(git -C "$REPO_ROOT" config user.name || echo sandbox)"
    git -C "$SANDBOX_DIR/repo" config user.email "$(git -C "$REPO_ROOT" config user.email || echo sandbox@localhost)"
    echo ".venv" >> "$SANDBOX_DIR/repo/.git/info/exclude"
    ln -s /opt/venv "$SANDBOX_DIR/repo/.venv"
  fi
  echo "Sandbox pronto. Prova: sandbox/sandbox.sh test"
}

# Allowlist di rete in uscita (ITEM-005). sandbox.sh legge sandbox/allowlist.txt
# SULL'HOST, mai dentro il container, e scrive un file di filtro per il proxy:
# un pattern ancorato ed escape dei punti per riga ("api.anthropic.com" ->
# "^api\.anthropic\.com$"). --allow-pip aggiunge pypi.org/files.pythonhosted.org
# solo al filtro di quella sessione, senza toccare il file del repository.
write_filter_file() {
  local allow_pip="$1" out="$2" line host
  : > "$out"
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%%#*}"
    host="$(printf '%s' "$line" | tr -d '[:space:]')"
    [ -n "$host" ] || continue
    printf '^%s$\n' "$(printf '%s' "$host" | sed 's/\./\\./g')" >> "$out"
  done < "$ALLOWLIST"
  if [ "$allow_pip" = 1 ]; then
    for host in "${PIP_HOSTS[@]}"; do
      printf '^%s$\n' "$(printf '%s' "$host" | sed 's/\./\\./g')" >> "$out"
    done
  fi
}

# Rete filtrata e proxy dedicati a UNA sessione (ITEM-005): creati prima del
# container principale e distrutti subito dopo, così due sessioni --net in
# parallelo non condividono né si influenzano. La rete è "--internal": nessuna
# rotta verso l'esterno, quindi il container principale non ha un'altra via
# da provare se ignora le variabili *_PROXY. Ritorna "rete proxy filtro" su
# stdout.
start_egress() {
  local allow_pip="$1" suffix net_name proxy_name filter_file
  suffix="$$-$RANDOM"
  net_name="${PROFILE}-egress-$suffix"
  proxy_name="${PROFILE}-proxy-$suffix"
  filter_file="$SANDBOX_DIR/.proxy-filter-$suffix"
  write_filter_file "$allow_pip" "$filter_file"
  dk network create --internal "$net_name" >/dev/null \
    || { rm -f "$filter_file"; die "non riesco a creare la rete filtrata."; }
  dk run -d --name "$proxy_name" --network bridge \
      --read-only --tmpfs /tmp:mode=1777 \
      --cap-drop ALL --security-opt no-new-privileges \
      -v "$filter_file:/etc/tinyproxy/filter:ro" \
      "$PROXY_IMAGE" >/dev/null \
    || { dk network rm "$net_name" >/dev/null 2>&1; rm -f "$filter_file"; die "non riesco ad avviare il proxy d'uscita."; }
  dk network connect "$net_name" "$proxy_name" >/dev/null \
    || { dk rm -f "$proxy_name" >/dev/null 2>&1; dk network rm "$net_name" >/dev/null 2>&1; rm -f "$filter_file"; die "non riesco a collegare il proxy alla rete filtrata."; }
  echo "$net_name $proxy_name $filter_file"
}

stop_egress() {
  local net_name="$1" proxy_name="$2" filter_file="$3"
  dk rm -f "$proxy_name" >/dev/null 2>&1 || true
  dk network rm "$net_name" >/dev/null 2>&1 || true
  rm -f "$filter_file"
}

# Unico punto in cui parte il container principale: tutte le restrizioni
# stanno qui, quindi vale lo stesso isolamento per i comandi e per l'agente.
# Nota: sul mount di Colima le cartelle create dal Mac risultano di root nel
# container e git le rifiuta ("dubious ownership"): safe.directory='*' vale
# solo qui dentro, dove tutto è il sandbox.
# Uso: container_exec RETE CMD...   (RETE: none|egress; opzioni extra di
# docker in DOCKER_OPTS; con RETE=egress, ALLOW_PIP seleziona il filtro)
DOCKER_OPTS=()
ALLOW_PIP=0
container_exec() {
  local net="$1" tty=(); shift
  [ -t 0 ] && tty=(-it)
  [ -d "$SANDBOX_DIR/repo/.git" ] || die "sandbox non preparato: lancia prima 'sandbox/sandbox.sh setup'."
  local egress_env=() net_name="" proxy_name="" filter_file="" rc=0
  if [ "$net" = egress ]; then
    read -r net_name proxy_name filter_file < <(start_egress "$ALLOW_PIP")
    egress_env=(--network "$net_name"
      -e "HTTP_PROXY=http://$proxy_name:8888" -e "HTTPS_PROXY=http://$proxy_name:8888"
      -e "http_proxy=http://$proxy_name:8888" -e "https_proxy=http://$proxy_name:8888")
  else
    egress_env=(--network none)
  fi
  dk run --rm --init ${tty[@]+"${tty[@]}"} ${DOCKER_OPTS[@]+"${DOCKER_OPTS[@]}"} \
    "${egress_env[@]}" \
    --read-only --tmpfs /tmp:exec,mode=1777 \
    --cap-drop ALL --security-opt no-new-privileges \
    --memory 2g --pids-limit 512 \
    --user "$(id -u):$(id -g)" \
    -e "HOME=${SANDBOX_HOME:-/tmp/home}" \
    -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0='*' \
    -v "$SANDBOX_DIR:/work" \
    "$IMAGE" "$@" || rc=$?
  [ "$net" = egress ] && stop_egress "$net_name" "$proxy_name" "$filter_file"
  return "$rc"
}

sandbox_run() {
  local net=none allow_pip=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --net) net=egress; shift ;;
      --allow-pip) allow_pip=1; shift ;;
      --) shift; break ;;
      *) break ;;
    esac
  done
  [ "$allow_pip" = 1 ] && [ "$net" != egress ] && die "--allow-pip richiede --net."
  ALLOW_PIP="$allow_pip"
  if [ $# -eq 0 ]; then
    container_exec "$net" bash
  else
    container_exec "$net" "$@"
  fi
}

cmd_run() { sandbox_run "$@"; }

# L'agente gira nel container: i suoi strumenti (shell, lettura e scrittura di
# file) vedono solo /work. La sua HOME (storia, impostazioni) è
# $SANDBOX_DIR/agent-home, fuori dal clone git, quindi non finisce nei commit.
cmd_agent() {
  local net=none allow_pip=0
  DOCKER_OPTS=(--label "$AGENT_LABEL")
  while [ $# -gt 0 ]; do
    case "$1" in
      --net) net=egress; shift ;;
      --allow-pip) allow_pip=1; shift ;;
      --env)
        [ -n "${2:-}" ] || die "--env vuole il NOME di una variabile."
        case "$2" in *=*) die "--env accetta solo il nome: il valore si legge dall'ambiente, non dalla riga di comando." ;; esac
        DOCKER_OPTS+=(-e "$2"); shift 2 ;;
      --) shift; break ;;
      *) break ;;
    esac
  done
  [ "$allow_pip" = 1 ] && [ "$net" != egress ] && die "--allow-pip richiede --net."
  [ $# -gt 0 ] || die "uso: sandbox/sandbox.sh agent [--net] [--allow-pip] [--env NOME]... -- COMANDO"
  [ -t 0 ] && [ -t 1 ] || die "l'agente va avviato da un terminale: serve per vederlo e fermarlo con Ctrl+C."
  mkdir -p "$SANDBOX_DIR/agent-home"
  if [ "$net" = egress ]; then
    echo "Rete filtrata (ITEM-005): solo gli host in sandbox/allowlist.txt sono raggiungibili." >&2
  fi
  ALLOW_PIP="$allow_pip"
  SANDBOX_HOME=/work/agent-home container_exec "$net" "$@"
}

cmd_agent_stop() {
  need_tools
  local ids; ids="$(dk ps -q --filter "label=$AGENT_LABEL")"
  if [ -n "$ids" ]; then
    # shellcheck disable=SC2086
    dk kill $ids >/dev/null
    echo "Agenti fermati."
  else
    echo "Nessun agente in esecuzione."
  fi
}

cmd_test() { sandbox_run -- .venv/bin/python -m unittest discover tests; }

cmd_pull() {
  local branch="${1:-}"
  [ -n "$branch" ] || die "uso: sandbox/sandbox.sh pull BRANCH"
  git -C "$REPO_ROOT" fetch "$SANDBOX_DIR/repo" "$branch:$branch"
  echo "Branch '$branch' portato nel repository. Controlla il diff, poi il push è a mano tua."
}

# Controllo rapido (pochi secondi), per l'Orchestrator a ogni passaggio di consegne.
cmd_status() {
  need_tools
  local fail=0
  st() { if "${@:2}" >/dev/null 2>&1; then echo "  ok   $1"; else echo "  FAIL $1"; fail=1; fi; }
  echo "Stato del sandbox:"
  st "VM Colima in esecuzione" colima status --profile "$PROFILE"
  st "immagine presente" dk image inspect "$IMAGE"
  st "clone presente" test -d "$SANDBOX_DIR/repo/.git"
  st "segno del sandbox nell'immagine" sandbox_run -- sh -c '[ "$(cat /etc/dashboard-sandbox)" = dashboard-sandbox-v1 ]'
  st "immagine del proxy d'uscita presente" dk image inspect "$PROXY_IMAGE"
  [ "$fail" = 0 ] || { echo "Sandbox NON pronto: lancia 'sandbox/sandbox.sh setup'." >&2; exit 1; }
}

cmd_verify() {
  local hash_db=0 before="" after="" fail=0
  [ "${1:-}" = "--hash-app-db" ] && hash_db=1
  need_tools
  mkdir -p "$SANDBOX_DIR/agent-home"
  if [ "$hash_db" = 1 ]; then
    [ -f "$REPO_ROOT/app.db" ] && before="$(shasum -a 256 "$REPO_ROOT/app.db" | cut -d' ' -f1)"
  fi

  check() { # descrizione, comando che DEVE riuscire dentro il sandbox
    if sandbox_run -- sh -c "$2" >/dev/null 2>&1; then echo "  ok   $1"; else echo "  FAIL $1"; fail=1; fi
  }
  # Gli stessi controlli per la shell e per l'agente (che ha un'altra HOME): è
  # lo stesso container, quindi anche gli strumenti di file dell'agente
  # vedono solo questo.
  for mode in comandi agente; do
    if [ "$mode" = agente ]; then export SANDBOX_HOME=/work/agent-home; else unset SANDBOX_HOME; fi
    echo "Isolamento ($mode):"
    for f in "${PROTECTED[@]}"; do
      check "dentro non esiste $f" "[ ! -e /work/repo/$f ] && ! cat /work/repo/$f"
    done
    check "la home dell'host non esiste" "[ ! -e '$HOME/.ssh' ] && [ ! -e '$REPO_ROOT' ]"
    check "l'unico mount dell'host è /work" "[ \"\$(grep -cE ' (virtiofs|fuse[^ ]*|9p|sshfs) ' /proc/mounts)\" -le 1 ]"
    check "git push fallisce (nessun remote)" "git push 2>&1 | grep -q 'No configured push destination'"
    check "nessuna rete" "! python -c 'import socket; socket.create_connection((\"1.1.1.1\", 53), 2)'"
    check "si scrive in /work" "touch /work/.verifica && rm /work/.verifica"
    # Worktree usa e getta: prova anche il commit senza toccare il lavoro in corso.
    check "worktree e commit funzionano" "cd /work/repo && { git worktree remove --force /work/wt-verifica 2>/dev/null; git branch -q -D verifica-wt 2>/dev/null; true; } && git worktree add -q /work/wt-verifica -b verifica-wt HEAD && git -C /work/wt-verifica commit --allow-empty -q -m verifica && git worktree remove --force /work/wt-verifica && git branch -q -D verifica-wt"
  done
  unset SANDBOX_HOME

  echo "Allowlist di rete (ITEM-005):"
  # Sonda il tunnel CONNECT verso il proxy direttamente, invece di fare una
  # richiesta HTTP vera: così il risultato non dipende da cosa risponde
  # l'host dietro (200, 404, ...), solo dal fatto che il proxy l'abbia
  # lasciato passare o no.
  local connect_probe
  connect_probe='
import os, socket, sys
from urllib.parse import urlparse
host, want = sys.argv[1], sys.argv[2]
u = urlparse(os.environ.get("HTTPS_PROXY", ""))
s = socket.create_connection((u.hostname, u.port), timeout=10)
s.sendall(("CONNECT %s:443 HTTP/1.1\r\nHost: %s:443\r\n\r\n" % (host, host)).encode())
allowed = b" 200 " in s.recv(200)
sys.exit(0 if allowed == (want == "allow") else 1)
'
  if sandbox_run --net -- python3 -c "$connect_probe" api.anthropic.com allow >/dev/null 2>&1; then
    echo "  ok   host in allowlist raggiungibile tramite il proxy"
  else
    echo "  FAIL host in allowlist non raggiungibile"; fail=1
  fi
  if sandbox_run --net -- python3 -c "$connect_probe" example.com deny >/dev/null 2>&1; then
    echo "  ok   host fuori allowlist bloccato dal proxy"
  else
    echo "  FAIL host fuori allowlist non bloccato"; fail=1
  fi
  if sandbox_run --net -- python3 -c '
import socket, sys
try:
    socket.create_connection(("1.1.1.1", 443), 3)
except OSError:
    sys.exit(0)
sys.exit(1)
' >/dev/null 2>&1; then
    echo "  ok   bypass diretto del proxy bloccato (nessuna rotta d'uscita)"
  else
    echo "  FAIL bypass diretto del proxy riuscito"; fail=1
  fi

  echo "Guardia:"
  check "dentro il sandbox la guardia lascia passare" "/work/repo/sandbox/require-sandbox.sh || [ \"\$(cat /etc/dashboard-sandbox)\" = dashboard-sandbox-v1 ]"
  # Fuori dal sandbox (sul Mac) la guardia deve bloccare, senza uscita di emergenza.
  if env -u DASHBOARD_ALLOW_HOST "$REPO_ROOT/sandbox/require-sandbox.sh" >/dev/null 2>&1; then
    echo "  FAIL la guardia lascia passare anche fuori dal sandbox"; fail=1
  else
    echo "  ok   la guardia blocca fuori dal sandbox"
  fi

  echo "Test:"
  if cmd_test; then echo "  ok   i test passano nel sandbox"; else echo "  FAIL i test"; fail=1; fi

  if [ "$hash_db" = 1 ] && [ -n "$before" ]; then
    after="$(shasum -a 256 "$REPO_ROOT/app.db" | cut -d' ' -f1)"
    if [ "$before" = "$after" ]; then echo "  ok   app.db identico prima e dopo"; else echo "  FAIL app.db è cambiato"; fail=1; fi
  fi
  [ "$fail" = 0 ] && echo "Sandbox verificato." || { echo "Verifica NON riuscita." >&2; exit 1; }
}

cmd_stop() { need_tools; colima stop --profile "$PROFILE"; }

case "${1:-help}" in
  setup)      shift; cmd_setup "$@" ;;
  run)        shift; cmd_run "$@" ;;
  agent)      shift; cmd_agent "$@" ;;
  agent-stop) shift; cmd_agent_stop "$@" ;;
  test)       shift; cmd_test "$@" ;;
  pull)       shift; cmd_pull "$@" ;;
  status)     shift; cmd_status "$@" ;;
  verify)     shift; cmd_verify "$@" ;;
  stop)       shift; cmd_stop "$@" ;;
  *)          sed -n '2,26p' "$0" ;;
esac
