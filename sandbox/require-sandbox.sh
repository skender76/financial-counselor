#!/usr/bin/env bash
# Guardia: si rifiuta di proseguire se non si è dentro il sandbox.
#
#   sandbox/require-sandbox.sh          esce con 1 e un messaggio fuori dal sandbox
#   sandbox/require-sandbox.sh --hook   come sopra ma esce con 2 (blocca la chiamata
#                                       dello strumento negli hook di Claude Code)
#
# "Dentro il sandbox" = esiste /etc/dashboard-sandbox con il testo atteso: il file lo
# scrive il Dockerfile dell'immagine, su un Mac non c'è e senza root non si crea.
# Non è una barriera contro un agente ostile (per quello c'è l'isolamento vero del
# sandbox): evita errori e dimenticanze.
#
# Uscita di emergenza: DASHBOARD_ALLOW_HOST=1 la lascia passare, ma ogni uso viene
# scritto in $SANDBOX_DIR/override.log (default ~/dashboard-sandbox/override.log);
# se il registro non è scrivibile la guardia rifiuta.
set -u

MARKER=/etc/dashboard-sandbox
EXPECTED=dashboard-sandbox-v1
SANDBOX_DIR="${SANDBOX_DIR:-$HOME/dashboard-sandbox}"
FAIL=1
[ "${1:-}" = "--hook" ] && FAIL=2

[ "$(cat "$MARKER" 2>/dev/null)" = "$EXPECTED" ] && exit 0

if [ "${DASHBOARD_ALLOW_HOST:-}" = 1 ]; then
  log="$SANDBOX_DIR/override.log"
  # Una riga per sessione dell'agente (PPID), non una per ogni chiamata.
  if ! tail -n 1 "$log" 2>/dev/null | grep -q " ppid=$PPID "; then
    if ! { mkdir -p "$SANDBOX_DIR" \
           && printf '%s override ppid=%s cwd=%s user=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$PPID" "$PWD" "$(id -un)" >> "$log"; } 2>/dev/null; then
      echo "ERRORE: l'uscita di emergenza va registrata ma non riesco a scrivere $log." >&2
      exit "$FAIL"
    fi
  fi
  exit 0
fi

cat >&2 <<'MSG'
Bloccato: questo agente non gira nel sandbox.
Avvialo così:  sandbox/sandbox.sh agent --net -- <comando-dell-agente>
(prima, una volta:  sandbox/sandbox.sh setup)
Uscita di emergenza, registrata in override.log:  DASHBOARD_ALLOW_HOST=1
MSG
exit "$FAIL"
