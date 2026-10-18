#!/usr/bin/env bash
# Avvia la Dashboard Finanziaria (macOS e Linux).
#
#   ./run.sh                         avvia il server
#   ./run.sh invite                  genera un codice d'invito monouso
#   ./run.sh import-legacy EMAIL     importa dati.json e ai-config.json nell'account EMAIL
#   ./run.sh reset-password EMAIL    imposta una nuova password per EMAIL
#
# Alla prima esecuzione crea .venv, installa requirements.txt e crea .env
# (permessi 600, ignorato da git) con una DASHBOARD_MASTER_KEY nuova. In .env si
# mettono anche le altre impostazioni, per esempio online:
#   DASHBOARD_PUBLIC_URL=https://dash.example.com
#   DASHBOARD_TRUST_PROXY=1
#   DASHBOARD_SMTP_HOST=...   (vedi README.md)
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "ERRORE: non trovo $PYTHON. Installa Python 3 (https://www.python.org/downloads/)." >&2
  exit 1
fi

# Ambiente virtuale e dipendenze (si reinstalla solo se requirements.txt cambia).
if [ ! -x .venv/bin/python ]; then
  echo "Creo l'ambiente virtuale .venv..."
  "$PYTHON" -m venv .venv
fi
stamp=".venv/.requirements.sha"
current="$(shasum requirements.txt | cut -d' ' -f1)"
if [ ! -f "$stamp" ] || [ "$(cat "$stamp")" != "$current" ]; then
  echo "Installo le dipendenze..."
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -r requirements.txt
  echo "$current" > "$stamp"
fi

# Impostazioni: .env, con una chiave maestra generata la prima volta.
if [ ! -f .env ]; then
  (umask 077; printf 'DASHBOARD_MASTER_KEY=%s\n' "$(.venv/bin/python auth.py gen-master-key)" > .env)
  echo "Creato .env con una nuova DASHBOARD_MASTER_KEY: conservane una copia fuori dal progetto"
  echo "(senza, chiavi API e segreti 2FA già salvati non si possono decifrare)."
fi
chmod 600 .env
set -a
# shellcheck disable=SC1091
. ./.env
set +a

case "${1:-start}" in
  start)          exec .venv/bin/python server.py ;;
  invite)         exec .venv/bin/python auth.py create-invite ;;
  import-legacy)  exec .venv/bin/python auth.py import-legacy "${2:?uso: ./run.sh import-legacy EMAIL}" ;;
  reset-password) exec .venv/bin/python auth.py reset-password "${2:?uso: ./run.sh reset-password EMAIL}" ;;
  *) echo "Uso: ./run.sh [start|invite|import-legacy EMAIL|reset-password EMAIL]" >&2; exit 2 ;;
esac
