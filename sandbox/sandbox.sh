#!/usr/bin/env bash
# Sandbox di sviluppo: container OCI su Colima + CLI docker (vedi README.md).
#
#   sandbox/sandbox.sh setup                 prepara tutto (una volta)
#   sandbox/sandbox.sh run [--net] [-- CMD]  esegue CMD nel sandbox (default: bash)
#   sandbox/sandbox.sh test                  esegue i test nel sandbox
#   sandbox/sandbox.sh pull BRANCH           porta un branch del sandbox nel repository reale
#   sandbox/sandbox.sh verify [--hash-app-db]  controlla l'isolamento ed esegue i test
#   sandbox/sandbox.sh stop                  ferma la VM del sandbox
#
# Variabili: SANDBOX_DIR (default ~/dashboard-sandbox), SANDBOX_PROFILE (default dashboard-sandbox).
# Il push non è possibile dal sandbox: si fa a mano dal repository reale.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SANDBOX_DIR="${SANDBOX_DIR:-$HOME/dashboard-sandbox}"
PROFILE="${SANDBOX_PROFILE:-dashboard-sandbox}"
IMAGE="dashboard-sandbox:latest"
CONTEXT="colima-$PROFILE"
PROTECTED=(app.db .env dati.json ai-config.json esempi-estratti)

die() { echo "ERRORE: $*" >&2; exit 1; }
dk() { docker --context "$CONTEXT" "$@"; }

need_tools() {
  command -v colima >/dev/null || die "manca colima (brew install colima docker)."
  command -v docker >/dev/null || die "manca la CLI docker (brew install colima docker)."
}

cmd_setup() {
  need_tools
  mkdir -p "$SANDBOX_DIR"
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
  dk build -t "$IMAGE" "$ctx"
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

# docker run con tutte le restrizioni; i parametri sono quelli di `docker run`
# dopo le opzioni di sicurezza.
sandbox_run() {
  local net=none tty=()
  if [ "${1:-}" = "--net" ]; then net=bridge; shift; fi
  [ "${1:-}" = "--" ] && shift
  [ -t 0 ] && tty=(-it)
  [ -d "$SANDBOX_DIR/repo/.git" ] || die "sandbox non preparato: lancia prima 'sandbox/sandbox.sh setup'."
  dk run --rm ${tty[@]+"${tty[@]}"} \
    --network "$net" \
    --read-only --tmpfs /tmp:exec,mode=1777 \
    --cap-drop ALL --security-opt no-new-privileges \
    --memory 2g --pids-limit 512 \
    --user "$(id -u):$(id -g)" \
    -v "$SANDBOX_DIR:/work" \
    "$IMAGE" "$@"
}

cmd_run() {
  if [ $# -eq 0 ] || { [ "${1:-}" = "--net" ] && [ $# -eq 1 ]; }; then
    sandbox_run "$@" bash
  else
    sandbox_run "$@"
  fi
}

cmd_test() { sandbox_run -- .venv/bin/python -m unittest discover tests; }

cmd_pull() {
  local branch="${1:-}"
  [ -n "$branch" ] || die "uso: sandbox/sandbox.sh pull BRANCH"
  git -C "$REPO_ROOT" fetch "$SANDBOX_DIR/repo" "$branch:$branch"
  echo "Branch '$branch' portato nel repository. Controlla il diff, poi il push è a mano tua."
}

cmd_verify() {
  local hash_db=0 before="" after="" fail=0
  [ "${1:-}" = "--hash-app-db" ] && hash_db=1
  need_tools
  if [ "$hash_db" = 1 ]; then
    [ -f "$REPO_ROOT/app.db" ] && before="$(shasum -a 256 "$REPO_ROOT/app.db" | cut -d' ' -f1)"
  fi

  check() { # descrizione, comando che DEVE riuscire dentro il sandbox
    if sandbox_run -- sh -c "$2" >/dev/null 2>&1; then echo "  ok   $1"; else echo "  FAIL $1"; fail=1; fi
  }
  echo "Isolamento:"
  for f in "${PROTECTED[@]}"; do
    check "dentro non esiste $f" "[ ! -e /work/repo/$f ] && ! cat /work/repo/$f"
  done
  check "la home dell'host non esiste" "[ ! -e '$HOME/.ssh' ] && [ ! -e '$REPO_ROOT' ]"
  check "git push fallisce" "! git push"
  check "nessuna rete" "! python -c 'import socket; socket.create_connection((\"1.1.1.1\", 53), 2)'"
  check "si scrive in /work" "touch /work/.verifica && rm /work/.verifica"
  check "si crea un commit" "cd /work/repo && git commit --allow-empty -q -m verifica && git reset -q --hard HEAD~1"

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
  setup)  shift; cmd_setup "$@" ;;
  run)    shift; cmd_run "$@" ;;
  test)   shift; cmd_test "$@" ;;
  pull)   shift; cmd_pull "$@" ;;
  verify) shift; cmd_verify "$@" ;;
  stop)   shift; cmd_stop "$@" ;;
  *)      sed -n '2,12p' "$0" ;;
esac
