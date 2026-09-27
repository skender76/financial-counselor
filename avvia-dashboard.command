#!/bin/bash
# Doppio click su questo file per avviare la Dashboard Finanziaria.
# Apre automaticamente il browser su http://localhost:8765/

# Si posiziona nella cartella dove si trova questo file, qualunque sia
# la cartella da cui viene lanciato (doppio click, Finder, ecc.).
cd "$(dirname "$0")"

echo "Avvio la Dashboard Finanziaria..."
echo "(questa finestra deve restare aperta finché usi la dashboard)"
echo ""

# Usa prima l'ambiente virtuale del progetto (.venv, contiene l'SDK del
# fornitore AI scelto in ai-config.json), poi "python3" (il nome standard su macOS), poi
# "python" come ripiego. Senza .venv la dashboard funziona, ma non la chat AI.
if [ -x ".venv/bin/python" ]; then
  .venv/bin/python server.py
elif command -v python3 >/dev/null 2>&1; then
  python3 server.py
elif command -v python >/dev/null 2>&1; then
  python server.py
else
  echo "ERRORE: non trovo Python installato su questo Mac."
  echo "Installa Python 3 da https://www.python.org/downloads/ e riprova."
  read -p "Premi Invio per chiudere..."
  exit 1
fi

# Chiusura normale (pulsante "Chiudi dashboard" o Ctrl+C): esce subito.
# In caso di errore tiene aperta la finestra, così si vede il messaggio
# invece che sparire subito.
status=$?
if [ $status -ne 0 ]; then
  read -p "Premi Invio per chiudere questa finestra..."
fi
exit $status
