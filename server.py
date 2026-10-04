#!/usr/bin/env python3
"""
Server locale per la Dashboard Finanziaria.

Cosa fa:
- Serve il file financial-dashboard.html su http://localhost:8765/
- Espone due endpoint per la persistenza vera su disco:
    GET  /api/data  -> restituisce il contenuto di dati.json (o {} se non esiste
                       ancora) e la sua versione nell'header ETag
    POST /api/data  -> sovrascrive dati.json con il corpo JSON ricevuto, solo se
                       l'header If-Match indica la versione attuale del file
                       (altrimenti 409: dati cambiati da un'altra scheda)
- Espone il consulente AI:
    POST /api/ask     -> inoltra il prompt con la chiave API in uso
    GET  /api/ai-info -> fornitore/modello/chiave in uso e cosa eventualmente manca
    GET  /api/ai-keys -> elenco delle chiavi API (mai il valore, solo le
                         ultime 4 cifre)
    POST /api/ai-keys -> {"action": "add" | "update" | "delete" | "activate", ...}
  Le chiavi (Anthropic, OpenAI, Gemini) si gestiscono dalla dashboard e sono
  salvate in ai-config.json: vedi ai_providers.py.
- Importazione estratti conto (Revolut, BPER, Fineco):
    POST /api/import-statement -> {account, kind, filename, content_b64, since?}: legge
                       il file (xlrd/openpyxl per gli Excel) e restituisce
                       {movements, ignored, summary} come anteprima; NON scrive nulla.
                       Vedi statement_import.py.
- Chiusura ordinata (attende la fine di un eventuale salvataggio di dati.json):
    POST /api/shutdown (pulsante "Chiudi dashboard"), Ctrl+C, oppure la
    chiusura della finestra del Terminale.
- Per conti/movimenti/salvataggio basta la libreria standard di Python 3.
  Solo il consulente AI richiede in più l'SDK del fornitore scelto, installato
  nell'ambiente virtuale del progetto (Python di Homebrew non permette pip
  install globale):
    python3 -m venv .venv && .venv/bin/python -m pip install anthropic
  avvia-dashboard.command usa .venv automaticamente se esiste.

Come si usa:
    python3 server.py
(oppure fai doppio click su "avvia-dashboard.command", che lancia questo
script e apre automaticamente il browser)

I dati vengono scritti in dati.json, nella STESSA cartella di questo script:
è un file di testo semplice che puoi aprire, leggere, copiare altrove per
backup (Dropbox, iCloud, chiavetta USB, ecc.) in qualunque momento.
"""

import base64
import binascii
import errno
import hashlib
import json
import os
import signal
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import ai_providers
import statement_import

PORT = 8765
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_FILE = os.path.join(BASE_DIR, 'financial-dashboard.html')
DATA_FILE = os.path.join(BASE_DIR, 'dati.json')

# Serializza le scritture di dati.json e permette alla chiusura di aspettare
# che l'ultima sia terminata.
DATA_LOCK = threading.Lock()

# Il server risponde solo a richieste indirizzate a sé stesso: blocca il "DNS
# rebinding", con cui un sito esterno potrebbe farsi passare per localhost e
# leggere o sovrascrivere dati.json.
ALLOWED_HOSTS = {'localhost:%d' % PORT, '127.0.0.1:%d' % PORT}


def data_version(raw):
    """Versione di dati.json (ETag): hash del contenuto esatto del file."""
    return '"%s"' % hashlib.sha256(raw).hexdigest()[:20]


def read_data_file():
    """Restituisce (dati, versione) di dati.json."""
    try:
        with open(DATA_FILE, 'rb') as f:
            raw = f.read()
    except FileNotFoundError:
        return {}, data_version(b'')
    version = data_version(raw)
    content = raw.decode('utf-8', errors='replace').strip()
    if not content:
        return {}, version
    try:
        return json.loads(content), version
    except json.JSONDecodeError as e:
        # Non solleviamo un errore che blocchi il server: meglio partire da
        # uno stato vuoto e segnalarlo chiaramente in console, piuttosto che
        # impedire del tutto l'avvio per un file corrotto.
        print('ATTENZIONE: dati.json non è leggibile (%s). Riparti da vuoto; '
              'il file corrotto NON viene sovrascritto finché non salvi di '
              'nuovo dalla dashboard.' % e, file=sys.stderr)
        return {}, version


class VersionConflict(Exception):
    pass


def write_data_file(data, expected_version):
    """Scrive dati.json solo se è ancora alla versione da cui è partita la
    pagina (expected_version); restituisce la nuova versione.

    Così una scheda rimasta aperta con dati vecchi (o una versione precedente
    della dashboard) non può sovrascrivere modifiche fatte altrove.
    """
    content = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
    # Scrittura "atomica": prima su un file temporaneo, poi rinominato sopra
    # il file finale. Se il processo viene interrotto a metà scrittura (es.
    # il Mac va in sospensione, il terminale viene chiuso di colpo), dati.json
    # resta comunque nel suo stato precedente valido, invece di restare
    # a metà scritto e corrotto.
    tmp_path = DATA_FILE + '.tmp'
    with DATA_LOCK:
        _, current_version = read_data_file()
        if expected_version != current_version:
            raise VersionConflict()
        with open(tmp_path, 'wb') as f:
            f.write(content)
        os.replace(tmp_path, DATA_FILE)
    return data_version(content)


def request_shutdown(server):
    # server.shutdown() blocca finché serve_forever() non termina, quindi va
    # chiamato da un thread diverso da quello che esegue serve_forever().
    threading.Thread(target=server.shutdown, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Log minimale invece del formato di default (più leggibile in un
        # terminale lasciato aperto durante l'uso normale).
        sys.stderr.write('%s - %s\n' % (self.address_string(), fmt % args))

    def _send_json(self, obj, status=200, etag=None):
        body = json.dumps(obj).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        if etag:
            self.send_header('ETag', etag)
        self.send_header('Content-Length', str(len(body)))
        # Mai cache per le risposte dinamiche: contenuto vecchio servito da
        # cache ha già causato confusione in passato (dati che sembravano
        # aggiornati e non lo erano).
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, content_type):
        try:
            with open(path, 'rb') as f:
                body = f.read()
        except OSError:
            self.send_error(404, 'File non trovato: ' + os.path.basename(path))
            return
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _reject_foreign_request(self, require_json):
        """Invia l'errore e restituisce True se la richiesta va rifiutata."""
        if self.headers.get('Host') not in ALLOWED_HOSTS:
            self._send_json({'status': 'error', 'message': 'Host non consentito.'}, status=403)
            return True
        # Solo richieste JSON per le operazioni che scrivono o agiscono: un
        # altro sito aperto nel browser può inviare a localhost un POST
        # "semplice" (es. un form text/plain che contiene JSON valido), ma non
        # con Content-Type application/json senza un preflight CORS, che questo
        # server non accetta. Così non può sovrascrivere dati.json, usare il
        # credito API del consulente AI o spegnere la dashboard.
        if require_json and not (self.headers.get('Content-Type') or '').startswith('application/json'):
            self._send_json({'status': 'error', 'message': 'Content-Type non valido: è richiesto application/json.'}, status=415)
            return True
        return False

    def do_GET(self):
        if self._reject_foreign_request(require_json=False):
            return
        if self.path == '/api/data':
            data, version = read_data_file()
            self._send_json(data, etag=version)
            return
        if self.path == '/api/ai-info':
            self._send_json(ai_providers.info())
            return
        if self.path == '/api/ai-keys':
            try:
                self._send_json(ai_providers.list_keys())
            except ai_providers.AdvisorError as e:
                self._send_json({'status': 'error', 'message': str(e)}, status=e.status)
            return
        if self.path in ('/', '/index.html'):
            self._send_file(DASHBOARD_FILE, 'text/html; charset=utf-8')
            return
        self.send_error(404, 'Percorso non gestito: ' + self.path)

    def do_POST(self):
        if self._reject_foreign_request(require_json=True):
            return
        if self.path == '/api/shutdown':
            self._handle_shutdown()
            return
        if self.path == '/api/ask':
            self._handle_ask()
            return
        if self.path == '/api/ai-keys':
            self._handle_ai_keys()
            return
        if self.path == '/api/import-statement':
            self._handle_import_statement()
            return
        if self.path != '/api/data':
            self.send_error(404, 'Percorso non gestito: ' + self.path)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            raw = self.rfile.read(length) if length > 0 else b'{}'
            data = json.loads(raw.decode('utf-8'))
            if not isinstance(data, dict):
                raise ValueError('Il corpo della richiesta deve essere un oggetto JSON.')
            expected = self.headers.get('If-Match')
            if not expected:
                # Le versioni precedenti della dashboard non inviano If-Match:
                # una loro scheda rimasta aperta non deve poter scrivere.
                self._send_json({'status': 'error', 'message': 'Pagina di una versione precedente della dashboard: ricaricala.'}, status=428)
                return
            new_version = write_data_file(data, expected)
            self._send_json({'status': 'ok'}, etag=new_version)
        except VersionConflict:
            self._send_json({'status': 'error', 'message': 'dati.json è stato modificato da un\'altra scheda o finestra: ricarica la pagina.'}, status=409)
        except (ValueError, json.JSONDecodeError) as e:
            self._send_json({'status': 'error', 'message': str(e)}, status=400)
        except OSError as e:
            self._send_json({'status': 'error', 'message': 'Errore scrivendo dati.json: ' + str(e)}, status=500)

    def _handle_shutdown(self):
        self._send_json({'status': 'ok'})
        print('Richiesta di chiusura dalla dashboard.')
        request_shutdown(self.server)

    def _handle_ai_keys(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
            body = json.loads(self.rfile.read(length).decode('utf-8')) if length > 0 else {}
            if not isinstance(body, dict):
                raise ValueError('Il corpo della richiesta deve essere un oggetto JSON.')
            result = ai_providers.manage_keys(body)
        except (ValueError, json.JSONDecodeError) as e:
            self._send_json({'status': 'error', 'message': str(e)}, status=400)
            return
        except ai_providers.AdvisorError as e:
            self._send_json({'status': 'error', 'message': str(e)}, status=e.status)
            return
        except OSError as e:
            self._send_json({'status': 'error', 'message': 'Errore scrivendo ai-config.json: %s' % e}, status=500)
            return
        result['status'] = 'ok'
        self._send_json(result)

    def _handle_import_statement(self):
        # Il file arriva in base64 dentro un JSON (come tutte le POST, che devono
        # essere application/json). Il limite del corpo tiene conto del +33% del base64.
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length > statement_import.MAX_FILE_BYTES * 4 // 3 + 4096:
                self._send_json({'status': 'error', 'message': 'File troppo grande.'}, status=413)
                return
            body = json.loads(self.rfile.read(length).decode('utf-8')) if length > 0 else {}
            if not isinstance(body, dict):
                raise ValueError('Il corpo della richiesta deve essere un oggetto JSON.')
            try:
                content = base64.b64decode(body.get('content_b64') or '', validate=True)
            except (binascii.Error, ValueError):
                raise ValueError('Contenuto del file non valido.')
            result = statement_import.parse_statement(
                body.get('account'), body.get('kind'), body.get('filename'), content,
                since=body.get('since') or statement_import.IMPORT_START_DATE)
        except (ValueError, json.JSONDecodeError) as e:
            self._send_json({'status': 'error', 'message': str(e)}, status=400)
            return
        except statement_import.ImportError_ as e:
            self._send_json({'status': 'error', 'message': str(e)}, status=e.status)
            return
        except Exception as e:
            print('Errore imprevisto in /api/import-statement: %r' % e, file=sys.stderr)
            self._send_json({'status': 'error', 'message': 'errore imprevisto leggendo il file: %s' % e}, status=500)
            return
        result['status'] = 'ok'
        self._send_json(result)

    def _handle_ask(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
            body = json.loads(self.rfile.read(length).decode('utf-8')) if length > 0 else {}
            prompt = body.get('prompt') if isinstance(body, dict) else None
            if not isinstance(prompt, str) or not prompt.strip():
                raise ValueError('Campo "prompt" mancante o vuoto.')
        except (ValueError, json.JSONDecodeError) as e:
            self._send_json({'status': 'error', 'message': str(e)}, status=400)
            return
        try:
            text = ai_providers.ask(prompt)
        except ai_providers.AdvisorError as e:
            self._send_json({'status': 'error', 'message': str(e)}, status=e.status)
            return
        except Exception as e:
            # Qualunque altro errore imprevisto torna comunque in chat come
            # messaggio, invece di chiudere la connessione (che nella pagina
            # sembrerebbe "server.py non raggiungibile").
            print('Errore imprevisto in /api/ask: %r' % e, file=sys.stderr)
            self._send_json({'status': 'error', 'message': 'errore imprevisto nel server: %s' % e}, status=500)
            return
        self._send_json({'status': 'ok', 'text': text})


def main():
    if not os.path.exists(DASHBOARD_FILE):
        print('ERRORE: non trovo "financial-dashboard.html" nella '
              'stessa cartella di server.py (%s). Assicurati che i due file '
              'siano insieme.' % BASE_DIR, file=sys.stderr)
        sys.exit(1)

    url = 'http://localhost:%d/' % PORT
    try:
        server = ThreadingHTTPServer(('localhost', PORT), Handler)
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        # Di solito significa che la dashboard è già in esecuzione (un'altra
        # finestra del terminale ancora aperta): invece di un errore
        # incomprensibile, si apre semplicemente quella già attiva.
        print('La porta %d è già in uso: probabilmente la dashboard è già '
              'avviata in un\'altra finestra del Terminale.' % PORT)
        print('Apro ' + url + ' — se non si carica, chiudi le altre finestre '
              'del Terminale che eseguono server.py e riprova.')
        try:
            webbrowser.open(url)
        except Exception:
            pass
        return
    print('Dashboard Finanziaria — server locale avviato.')
    print('Apri (o si aprirà da solo tra un attimo): ' + url)
    print('I dati vengono salvati in: ' + DATA_FILE)
    ai = ai_providers.info()
    if ai['ready']:
        print('Consulente AI: attivo (%s, modello %s)' % (ai['label'], ai['model']))
    else:
        print('Consulente AI: NON attivo — ' + ai['problem'])
    print('Per fermare il server: chiudi questa finestra, oppure Ctrl+C.')
    try:
        webbrowser.open(url)
    except Exception:
        pass  # se non riesce ad aprire il browser da solo, l'utente lo apre a mano

    # Chiusura della finestra del Terminale (SIGHUP) o "kill" (SIGTERM):
    # stessa chiusura ordinata del pulsante nella dashboard.
    def on_signal(signum, frame):
        request_shutdown(server)
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGHUP, on_signal)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    # Attende che un eventuale salvataggio di dati.json in corso sia finito.
    with DATA_LOCK:
        server.server_close()
    print('\nServer fermato. I dati sono in: ' + DATA_FILE)


if __name__ == '__main__':
    main()
