#!/usr/bin/env python3
"""
Server locale per la Dashboard Finanziaria.

Cosa fa:
- Serve il file financial-dashboard.html su http://localhost:8765/
- Espone due endpoint per la persistenza vera su disco:
    GET  /api/data  -> restituisce il contenuto di dati.json (o {} se non esiste ancora)
    POST /api/data  -> sovrascrive dati.json con il corpo JSON ricevuto
- Espone POST /api/ask per il consulente AI: inoltra il prompt a Claude tramite
  l'SDK ufficiale Anthropic.
- Per conti/movimenti/salvataggio basta la libreria standard di Python 3.
  Solo il consulente AI richiede in più:
    pip3 install anthropic
  e una chiave API, in uno di questi modi:
    - variabile d'ambiente ANTHROPIC_API_KEY
    - oppure un file "anthropic-api-key.txt" (solo la chiave) nella stessa
      cartella di questo script — comodo con il doppio click su
      avvia-dashboard.command, che non eredita le variabili della shell.

Come si usa:
    python3 server.py
(oppure fai doppio click su "avvia-dashboard.command", che lancia questo
script e apre automaticamente il browser)

I dati vengono scritti in dati.json, nella STESSA cartella di questo script:
è un file di testo semplice che puoi aprire, leggere, copiare altrove per
backup (Dropbox, iCloud, chiavetta USB, ecc.) in qualunque momento.
"""

import json
import os
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8765
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_FILE = os.path.join(BASE_DIR, 'financial-dashboard.html')
DATA_FILE = os.path.join(BASE_DIR, 'dati.json')
API_KEY_FILE = os.path.join(BASE_DIR, 'anthropic-api-key.txt')

ADVISOR_MODEL = 'claude-opus-5'
ADVISOR_MAX_TOKENS = 16000


def read_data_file():
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if not content:
                return {}
            return json.loads(content)
    except (json.JSONDecodeError, OSError) as e:
        # Non solleviamo un errore che blocchi il server: meglio partire da
        # uno stato vuoto e segnalarlo chiaramente in console, piuttosto che
        # impedire del tutto l'avvio per un file corrotto.
        print('ATTENZIONE: dati.json non è leggibile (%s). Riparti da vuoto; '
              'il file corrotto NON viene sovrascritto finché non salvi di '
              'nuovo dalla dashboard.' % e, file=sys.stderr)
        return {}


def write_data_file(data):
    # Scrittura "atomica": prima su un file temporaneo, poi rinominato sopra
    # il file finale. Se il processo viene interrotto a metà scrittura (es.
    # il Mac va in sospensione, il terminale viene chiuso di colpo), dati.json
    # resta comunque nel suo stato precedente valido, invece di restare
    # a metà scritto e corrotto.
    tmp_path = DATA_FILE + '.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, DATA_FILE)


_anthropic_client = None


def get_anthropic_client():
    # Import "pigro": se l'SDK non è installato, il resto della dashboard
    # (conti, movimenti, salvataggio su file) deve funzionare lo stesso.
    global _anthropic_client
    if _anthropic_client is not None:
        return _anthropic_client
    import anthropic
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key and os.path.exists(API_KEY_FILE):
        with open(API_KEY_FILE, 'r', encoding='utf-8') as f:
            api_key = f.read().strip() or None
    # Senza chiave esplicita, l'SDK prova le altre credenziali disponibili
    # (ANTHROPIC_AUTH_TOKEN, profilo "ant auth login").
    _anthropic_client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    return _anthropic_client


def ask_claude(prompt):
    import anthropic
    client = get_anthropic_client()
    try:
        response = client.beta.messages.create(
            model=ADVISOR_MODEL,
            max_tokens=ADVISOR_MAX_TOKENS,
            thinking={'type': 'adaptive'},
            output_config={'effort': 'medium'},
            messages=[{'role': 'user', 'content': prompt}],
            # Se i filtri di sicurezza rifiutano la richiesta, l'API la
            # ripete lato server su un modello alternativo scelto da Anthropic.
            betas=['server-side-fallback-2026-07-01'],
            extra_body={'fallbacks': 'default'},
        )
    except anthropic.AuthenticationError:
        raise AdvisorError('chiave API Anthropic non valida o mancante (vedi istruzioni in server.py).', 401)
    except anthropic.BadRequestError as e:
        raise AdvisorError('richiesta rifiutata dal modello (prompt troppo lungo?): ' + e.message, 400)
    except anthropic.RateLimitError:
        raise AdvisorError('troppe richieste in poco tempo, riprova tra un minuto.', 429)
    except anthropic.APIStatusError as e:
        raise AdvisorError('errore API Anthropic (%d): %s' % (e.status_code, e.message), 502)
    except anthropic.APIConnectionError:
        raise AdvisorError('impossibile raggiungere l\'API Anthropic: controlla la connessione internet.', 502)

    if response.stop_reason == 'refusal':
        raise AdvisorError('il modello ha rifiutato di rispondere a questa domanda.', 422)
    text = '\n'.join(b.text for b in response.content if b.type == 'text').strip()
    if response.stop_reason == 'max_tokens':
        text += '\n\n[risposta troncata: limite di lunghezza raggiunto]'
    return text


class AdvisorError(Exception):
    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Log minimale invece del formato di default (più leggibile in un
        # terminale lasciato aperto durante l'uso normale).
        sys.stderr.write('%s - %s\n' % (self.address_string(), fmt % args))

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
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

    def do_GET(self):
        if self.path == '/api/data':
            self._send_json(read_data_file())
            return
        if self.path in ('/', '/index.html'):
            self._send_file(DASHBOARD_FILE, 'text/html; charset=utf-8')
            return
        self.send_error(404, 'Percorso non gestito: ' + self.path)

    def do_POST(self):
        if self.path == '/api/ask':
            self._handle_ask()
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
            write_data_file(data)
            self._send_json({'status': 'ok'})
        except (ValueError, json.JSONDecodeError) as e:
            self._send_json({'status': 'error', 'message': str(e)}, status=400)
        except OSError as e:
            self._send_json({'status': 'error', 'message': 'Errore scrivendo dati.json: ' + str(e)}, status=500)

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
            text = ask_claude(prompt)
        except ImportError:
            self._send_json({'status': 'error', 'message': 'SDK Anthropic non installato: esegui "pip3 install anthropic" e riavvia server.py.'}, status=500)
            return
        except AdvisorError as e:
            self._send_json({'status': 'error', 'message': str(e)}, status=e.status)
            return
        self._send_json({'status': 'ok', 'text': text})


def main():
    if not os.path.exists(DASHBOARD_FILE):
        print('ERRORE: non trovo "financial-dashboard.html" nella '
              'stessa cartella di server.py (%s). Assicurati che i due file '
              'siano insieme.' % BASE_DIR, file=sys.stderr)
        sys.exit(1)

    server = ThreadingHTTPServer(('localhost', PORT), Handler)
    url = 'http://localhost:%d/' % PORT
    print('Dashboard Finanziaria — server locale avviato.')
    print('Apri (o si aprirà da solo tra un attimo): ' + url)
    print('I dati vengono salvati in: ' + DATA_FILE)
    try:
        import anthropic  # noqa: F401
        has_key = bool(os.environ.get('ANTHROPIC_API_KEY')) or os.path.exists(API_KEY_FILE)
        print('Consulente AI: attivo (modello %s)%s' % (
            ADVISOR_MODEL, '' if has_key else ' — ATTENZIONE: nessuna chiave in '
            'ANTHROPIC_API_KEY né in anthropic-api-key.txt'))
    except ImportError:
        print('Consulente AI: NON attivo — esegui "pip3 install anthropic" e riavvia.')
    print('Per fermare il server: chiudi questa finestra, oppure Ctrl+C.')
    try:
        webbrowser.open(url)
    except Exception:
        pass  # se non riesce ad aprire il browser da solo, l'utente lo apre a mano

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nServer fermato.')


if __name__ == '__main__':
    main()
