#!/bin/bash
# Doppio click su questo file per avviare la Dashboard Finanziaria.
# Apre automaticamente il browser su http://localhost:8765/

# Si posiziona nella cartella dove si trova questo file, qualunque sia
# la cartella da cui viene lanciato (doppio click, Finder, ecc.).
cd "$(dirname "$0")"

echo "Avvio la Dashboard Finanziaria..."
echo "(questa finestra deve restare aperta finché usi la dashboard)"
echo ""

# Prova prima "python3" (il nome standard su macOS), poi "python" come ripiego.
if command -v python3 >/dev/null 2>&1; then
  python3 server.py
elif command -v python >/dev/null 2>&1; then
  python server.py
else
  echo "ERRORE: non trovo Python installato su questo Mac."
  echo "Installa Python 3 da https://www.python.org/downloads/ e riprova."
  read -p "Premi Invio per chiudere..."
  exit 1
fi

# Se il server si ferma o va in errore, tieni aperta la finestra così si
# vede il messaggio invece che sparire subito.
read -p "Premi Invio per chiudere questa finestra..."
