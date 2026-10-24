#!/usr/bin/env bash
# Sandbox di sviluppo: container OCI su Colima + CLI docker (vedi README.md).
#
#   sandbox/sandbox.sh setup                 prepara tutto (una volta)
#   sandbox/sandbox.sh run [--net] [-- CMD]  esegue CMD nel sandbox (default: bash)
#   sandbox/sandbox.sh agent [--net] [--env NOME]... -- CMD
#                                            avvia un agente (CMD) DENTRO il sandbox
#   sandbox/sandbox.sh agent-stop            ferma gli agenti rimasti in esecuzione
#   sandbox/sandbox.sh test                  esegue i test nel sandbox
#   sandbox/sandbox.sh pull BRANCH           porta un branch del sandbox nel repository reale
#   sandbox/sandbox.sh status                controllo rapido (VM, immagine, clone, segno nell'immagine)
#   sandbox/sandbox.sh verify [--hash-app-db]  controlla l'isolamento ed esegue i test
#   sandbox/sandbox.sh stop                  ferma la VM del sandbox
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
CONTEXT="colima-$PROFILE"
AGENT_LABEL="dashboard-sandbox=agent"
PROTECTED=(app.db .env dati.json ai-config.json esempi-estratti)

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

# Unico punto in cui parte un container: tutte le restrizioni stanno qui, quindi
# vale lo stesso isolamento per i comandi e per l'agente.
# Nota: sul mount di Colima le cartelle create dal Mac risultano di root nel
# container e git le rifiuta ("dubious ownership"): safe.directory='*' vale
# solo qui dentro, dove tutto è il sandbox.
# Uso: container_exec RETE CMD...   (opzioni extra di docker in DOCKER_OPTS)
DOCKER_OPTS=()
container_exec() {
  local net="$1" tty=(); shift
  [ -t 0 ] && tty=(-it)
  [ -d "$SANDBOX_DIR/repo/.git" ] || die "sandbox non preparato: lancia prima 'sandbox/sandbox.sh setup'."
  dk run --rm --init ${tty[@]+"${tty[@]}"} ${DOCKER_OPTS[@]+"${DOCKER_OPTS[@]}"} \
    --network "$net" \
    --read-only --tmpfs /tmp:exec,mode=1777 \
    --cap-drop ALL --security-opt no-new-privileges \
    --memory 2g --pids-limit 512 \
    --user "$(id -u):$(id -g)" \
    -e "HOME=${SANDBOX_HOME:-/tmp/home}" \
    -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0='*' \
    -v "$SANDBOX_DIR:/work" \
    "$IMAGE" "$@"
}

sandbox_run() {
  local net=none
  if [ "${1:-}" = "--net" ]; then net=bridge; shift; fi
  [ "${1:-}" = "--" ] && shift
  container_exec "$net" "$@"
}

cmd_run() {
  if [ $# -eq 0 ] || { [ "${1:-}" = "--net" ] && [ $# -eq 1 ]; }; then
    sandbox_run "$@" bash
  else
    sandbox_run "$@"
  fi
}

# L'agente gira nel container: i suoi strumenti (shell, lettura e scrittura di
# file) vedono solo /work. La sua HOME (storia, impostazioni) è
# $SANDBOX_DIR/agent-home, fuori dal clone git, quindi non finisce nei commit.
cmd_agent() {
  local net=none
  DOCKER_OPTS=(--label "$AGENT_LABEL")
  while [ $# -gt 0 ]; do
    case "$1" in
      --net) net=bridge; shift ;;
      --env)
        [ -n "${2:-}" ] || die "--env vuole il NOME di una variabile."
        case "$2" in *=*) die "--env accetta solo il nome: il valore si legge dall'ambiente, non dalla riga di comando." ;; esac
        DOCKER_OPTS+=(-e "$2"); shift 2 ;;
      --) shift; break ;;
      *) break ;;
    esac
  done
  [ $# -gt 0 ] || die "uso: sandbox/sandbox.sh agent [--net] [--env NOME]... -- COMANDO"
  [ -t 0 ] && [ -t 1 ] || die "l'agente va avviato da un terminale: serve per vederlo e fermarlo con Ctrl+C."
  mkdir -p "$SANDBOX_DIR/agent-home"
  if [ "$net" = bridge ]; then
    echo "ATTENZIONE: rete aperta. Finché non c'è l'elenco degli host consentiti (ITEM-005) il sandbox non limita le connessioni." >&2
  fi
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
  *)          sed -n '2,21p' "$0" ;;
esac
