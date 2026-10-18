#!/usr/bin/env python3
"""
Server della Dashboard Finanziaria (multi-utente).

Cosa fa:
- Serve il file financial-dashboard.html su /
- Account (vedi auth.py): registrazione, accesso e uscita con sessioni lato server
    GET  /api/auth/me        -> {authenticated, email?, csrf?, registration}
    POST /api/auth/register  -> {email, password, invite?}
    POST /api/auth/login     -> {email, password}
    POST /api/auth/logout
    POST /api/auth/login-mfa -> secondo passo dell'accesso con 2FA: {mfa_token, code}
    POST /api/auth/{verify,resend,forgot,reset}  -> verifica email e password dimenticata
    POST /api/auth/change-password, /api/auth/mfa/{setup,enable,disable}  (con sessione)
  Il token di sessione sta in un cookie HttpOnly + SameSite=Strict; ogni POST
  autenticato deve inoltre inviare l'header X-CSRF-Token.
- Tutti gli altri endpoint richiedono una sessione e lavorano solo sui dati
  dell'utente della sessione:
    GET  /api/data  -> dati dell'utente e la loro versione nell'header ETag
    POST /api/data  -> sovrascrive i dati dell'utente, solo se If-Match indica la
                       versione attuale (altrimenti 409: cambiati da un'altra scheda)
    POST /api/ask, GET /api/ai-info, GET/POST /api/ai-keys  -> consulente AI
    POST /api/import-statement -> anteprima dell'importazione di un estratto conto
- Per tutto tranne il consulente AI basta la libreria standard di Python 3; il
  consulente richiede l'SDK del fornitore scelto (vedi CLAUDE.md).

Configurazione (variabili d'ambiente):
    DASHBOARD_HOST          indirizzo di ascolto (default 127.0.0.1)
    DASHBOARD_PORT          porta (default 8765)
    DASHBOARD_PUBLIC_URL    indirizzo pubblico, es. https://dash.example.com: abilita
                            il cookie Secure e accetta solo quell'Host. Senza, il
                            server risponde solo su localhost:PORT.
    DASHBOARD_TRUST_PROXY   1 = dietro un reverse proxy locale (Caddy/nginx): l'IP
                            del client viene letto da X-Forwarded-For
    DASHBOARD_REGISTRATION  invite (default) | open | closed
    DASHBOARD_DB            percorso di app.db (default: accanto a server.py)
    DASHBOARD_MASTER_KEY    chiave AES-256 (python3 auth.py gen-master-key) che cifra
                            chiavi API e segreti 2FA; obbligatoria con DASHBOARD_PUBLIC_URL
    DASHBOARD_SMTP_*        invio email: vedi mailer.py

Come si usa in locale:
    python3 server.py
In produzione: dietro un reverse proxy con HTTPS, vedi README.md.
"""

import base64
import binascii
import errno
import json
import os
import secrets
import signal
import sys
import threading
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import ai_providers
import auth
import mailer
import statement_import

HOST = os.environ.get('DASHBOARD_HOST', '127.0.0.1')
PORT = int(os.environ.get('DASHBOARD_PORT', '8765'))
PUBLIC_URL = os.environ.get('DASHBOARD_PUBLIC_URL', '').rstrip('/')
TRUST_PROXY = os.environ.get('DASHBOARD_TRUST_PROXY') == '1'
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_FILE = os.path.join(BASE_DIR, 'financial-dashboard.html')

SECURE_COOKIE = PUBLIC_URL.startswith('https://')
COOKIE_NAME = '__Host-sid' if SECURE_COOKIE else 'sid'

# Il server risponde solo a richieste indirizzate a sé stesso: blocca il "DNS
# rebinding", con cui un sito esterno potrebbe farsi passare per questo server.
if PUBLIC_URL:
    ALLOWED_HOSTS = {urlsplit(PUBLIC_URL).netloc}
else:
    ALLOWED_HOSTS = {'localhost:%d' % PORT, '127.0.0.1:%d' % PORT}

MAX_DATA_BYTES = 25 * 1024 * 1024
MAX_AUTH_BODY = 4 * 1024
MAX_SMALL_BODY = 64 * 1024
MAX_ASK_BODY = 1024 * 1024

SECURITY_HEADERS = [
    ('X-Content-Type-Options', 'nosniff'),
    ('Referrer-Policy', 'no-referrer'),
    ('Cross-Origin-Opener-Policy', 'same-origin'),
]
# Le risposte JSON non devono caricare nulla.
CSP_API = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
# Pagina: gli <script> girano solo con il nonce di questa risposta (o se sono il
# Chart.js con SRI da jsdelivr), quindi un <script> iniettato non parte. Restano
# consentiti gli attributi onclick="..." (script-src-attr) e gli stili inline,
# perché la pagina li usa ovunque: vedi CLAUDE.md.
def csp_page(nonce):
    return ("default-src 'self'; script-src 'nonce-%s' https://cdn.jsdelivr.net; "
            "script-src-attr 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; object-src 'none'; form-action 'self'" % nonce)
if SECURE_COOKIE:
    SECURITY_HEADERS.append(('Strict-Transport-Security', 'max-age=31536000; includeSubDomains'))

# Limiti per utente (richieste per finestra) sugli endpoint costosi.
USER_LIMITS = {
    'ask': (20, 60),
    'import': (10, 60),
    'ai-keys': (30, 60),
}


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def request_shutdown(server):
    # server.shutdown() blocca finché serve_forever() non termina, quindi va
    # chiamato da un thread diverso da quello che esegue serve_forever().
    threading.Thread(target=server.shutdown, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    # Una connessione che non invia nulla non occupa un thread all'infinito.
    timeout = 30
    csp = CSP_API

    def log_message(self, fmt, *args):
        sys.stderr.write('%s - %s\n' % (self.address_string(), fmt % args))

    def end_headers(self):
        for name, value in SECURITY_HEADERS:
            self.send_header(name, value)
        self.send_header('Content-Security-Policy', self.csp)
        super().end_headers()

    def _send_json(self, obj, status=200, etag=None, headers=()):
        body = json.dumps(obj).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        if etag:
            self.send_header('ETag', etag)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header('Content-Length', str(len(body)))
        # Mai cache per le risposte dinamiche: contenuto vecchio servito da
        # cache ha già causato confusione in passato (dati che sembravano
        # aggiornati e non lo erano).
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status, message, headers=()):
        self._send_json({'status': 'error', 'message': message}, status=status, headers=headers)

    def _send_file(self, path, content_type):
        try:
            with open(path, 'rb') as f:
                body = f.read()
        except OSError:
            self.send_error(404, 'File non trovato: ' + os.path.basename(path))
            return
        if content_type.startswith('text/html'):
            nonce = secrets.token_urlsafe(16)
            body = body.replace(b'<script', b'<script nonce="' + nonce.encode('ascii') + b'"')
            self.csp = csp_page(nonce)
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    # ----- Controlli comuni -----

    def _client_ip(self):
        ip = self.client_address[0]
        if TRUST_PROXY and ip in ('127.0.0.1', '::1'):
            forwarded = self.headers.get('X-Forwarded-For')
            if forwarded:
                # Il proxy aggiunge in fondo l'indirizzo che ha visto: solo
                # l'ultimo valore è affidabile.
                return forwarded.split(',')[-1].strip()[:64] or ip
        return ip

    def _reject_foreign_request(self, require_json):
        """Invia l'errore e restituisce True se la richiesta va rifiutata."""
        if self.headers.get('Host') not in ALLOWED_HOSTS:
            self._send_error_json(403, 'Host non consentito.')
            return True
        if require_json:
            # Solo richieste JSON per le operazioni che scrivono o agiscono: un
            # altro sito aperto nel browser non può inviarle senza un preflight
            # CORS, che questo server non accetta.
            if not (self.headers.get('Content-Type') or '').startswith('application/json'):
                self._send_error_json(415, 'Content-Type non valido: è richiesto application/json.')
                return True
            # Il browser indica da dove parte la richiesta: se c'è, deve essere questo sito.
            origin = self.headers.get('Origin')
            if origin is not None and urlsplit(origin).netloc not in ALLOWED_HOSTS:
                self._send_error_json(403, 'Origine non consentita.')
                return True
        return False

    def _session_token(self):
        raw = self.headers.get('Cookie')
        if not raw:
            return None
        try:
            cookie = SimpleCookie(raw)
        except Exception:
            return None
        morsel = cookie.get(COOKIE_NAME)
        return morsel.value if morsel else None

    def _cookie_header(self, token, max_age):
        parts = ['%s=%s' % (COOKIE_NAME, token), 'Path=/', 'HttpOnly', 'SameSite=Strict',
                 'Max-Age=%d' % max_age]
        if SECURE_COOKIE:
            parts.append('Secure')
        return ('Set-Cookie', '; '.join(parts))

    def _require_session(self, check_csrf):
        """Restituisce la sessione, oppure invia 401/403 e restituisce None."""
        session = auth.get_session(self._session_token())
        if session is None:
            self._send_error_json(401, 'Accesso richiesto.')
            return None
        if check_csrf and not auth.csrf_ok(session, self.headers.get('X-CSRF-Token')):
            self._send_error_json(403, 'Token CSRF mancante o non valido: ricarica la pagina.')
            return None
        return session

    def _throttle_user(self, session, bucket):
        limit, window = USER_LIMITS[bucket]
        key = '%s:%d' % (bucket, session['user_id'])
        wait = auth.THROTTLE.retry_after(key, limit, window)
        if wait:
            self._send_error_json(429, 'Troppe richieste: riprova tra %d secondi.' % wait,
                                  headers=[('Retry-After', str(wait))])
            return False
        auth.THROTTLE.hit(key, window)
        return True

    def _read_json(self, max_bytes):
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            raise HttpError(400, 'Content-Length non valido.')
        if length < 0:
            raise HttpError(400, 'Content-Length non valido.')
        if length > max_bytes:
            raise HttpError(413, 'Richiesta troppo grande.')
        try:
            body = json.loads(self.rfile.read(length).decode('utf-8')) if length > 0 else {}
        except (ValueError, UnicodeDecodeError):
            raise HttpError(400, 'Il corpo della richiesta non è un JSON valido.')
        if not isinstance(body, dict):
            raise HttpError(400, 'Il corpo della richiesta deve essere un oggetto JSON.')
        return body

    # ----- GET -----

    def do_GET(self):
        if self._reject_foreign_request(require_json=False):
            return
        if self.path in ('/', '/index.html'):
            self._send_file(DASHBOARD_FILE, 'text/html; charset=utf-8')
            return
        if self.path == '/api/auth/me':
            session = auth.get_session(self._session_token())
            info = {'registration': auth.REGISTRATION_MODE, 'email_enabled': mailer.ENABLED}
            if session is None:
                info['authenticated'] = False
            else:
                info.update(authenticated=True, email=session['email'], csrf=session['csrf'],
                            mfa_enabled=session['mfa_enabled'])
            self._send_json(info)
            return
        if self.path in ('/api/data', '/api/ai-info', '/api/ai-keys'):
            session = self._require_session(check_csrf=False)
            if session is None:
                return
            uid = session['user_id']
            try:
                if self.path == '/api/data':
                    data, version = auth.read_user_data(uid)
                    self._send_json(data, etag=version)
                elif self.path == '/api/ai-info':
                    self._send_json(ai_providers.info(uid))
                else:
                    self._send_json(ai_providers.list_keys(uid))
            except ai_providers.AdvisorError as e:
                self._send_error_json(e.status, str(e))
            return
        self.send_error(404, 'Percorso non gestito')

    # ----- POST -----

    def do_POST(self):
        if self._reject_foreign_request(require_json=True):
            return
        try:
            public = {
                '/api/auth/login': self._handle_login,
                '/api/auth/login-mfa': self._handle_login_mfa,
                '/api/auth/register': self._handle_register,
                '/api/auth/verify': self._handle_verify,
                '/api/auth/resend': self._handle_resend,
                '/api/auth/forgot': self._handle_forgot,
                '/api/auth/reset': self._handle_reset,
            }
            if self.path in public:
                public[self.path]()
            elif self.path in ('/api/auth/logout', '/api/data', '/api/ask', '/api/ai-keys',
                               '/api/import-statement', '/api/auth/change-password',
                               '/api/auth/mfa/setup', '/api/auth/mfa/enable', '/api/auth/mfa/disable'):
                session = self._require_session(check_csrf=True)
                if session is None:
                    return
                handler = {
                    '/api/auth/logout': self._handle_logout,
                    '/api/data': self._handle_save_data,
                    '/api/ask': self._handle_ask,
                    '/api/ai-keys': self._handle_ai_keys,
                    '/api/import-statement': self._handle_import_statement,
                    '/api/auth/change-password': self._handle_change_password,
                    '/api/auth/mfa/setup': self._handle_mfa_setup,
                    '/api/auth/mfa/enable': self._handle_mfa_enable,
                    '/api/auth/mfa/disable': self._handle_mfa_disable,
                }[self.path]
                handler(session)
            else:
                self.send_error(404, 'Percorso non gestito')
        except HttpError as e:
            self._send_error_json(e.status, str(e))
        except auth.AuthError as e:
            headers = [('Retry-After', str(e.retry_after))] if e.retry_after else []
            self._send_error_json(e.status, str(e), headers=headers)

    def _start_session(self, user_id, email, status=200):
        # Un accesso non riusa mai un token precedente (niente session fixation).
        auth.delete_session(self._session_token())
        token, csrf = auth.create_session(user_id)
        self._send_json({'status': 'ok', 'email': email, 'csrf': csrf}, status=status,
                        headers=[self._cookie_header(token, auth.SESSION_MAX_SECONDS)])

    def _handle_login(self):
        body = self._read_json(MAX_AUTH_BODY)
        user_id, email, mfa = auth.login(body.get('email'), body.get('password'), self._client_ip())
        if mfa:
            self._send_json({'status': 'mfa_required', 'mfa_token': auth.create_mfa_challenge(user_id)})
            return
        self._start_session(user_id, email)

    def _handle_login_mfa(self):
        body = self._read_json(MAX_AUTH_BODY)
        user_id, email = auth.complete_mfa(body.get('mfa_token'), body.get('code'), self._client_ip())
        self._start_session(user_id, email)

    def _handle_register(self):
        body = self._read_json(MAX_AUTH_BODY)
        user_id, email = auth.register(body.get('email'), body.get('password'),
                                       body.get('invite'), self._client_ip())
        if mailer.ENABLED:
            # Account da confermare via email; la risposta è la stessa anche se
            # l'indirizzo esisteva già.
            self._send_json({'status': 'verify'}, status=202)
            return
        self._start_session(user_id, email, status=201)

    def _handle_verify(self):
        body = self._read_json(MAX_AUTH_BODY)
        auth.verify_email(body.get('token'), self._client_ip())
        self._send_json({'status': 'ok'})

    def _handle_resend(self):
        body = self._read_json(MAX_AUTH_BODY)
        auth.request_verification(body.get('email'), self._client_ip())
        self._send_json({'status': 'ok'})

    def _handle_forgot(self):
        body = self._read_json(MAX_AUTH_BODY)
        auth.request_password_reset(body.get('email'), self._client_ip())
        self._send_json({'status': 'ok'})

    def _handle_reset(self):
        body = self._read_json(MAX_AUTH_BODY)
        auth.reset_password(body.get('token'), body.get('password'), self._client_ip())
        self._send_json({'status': 'ok'})

    def _handle_change_password(self, session):
        body = self._read_json(MAX_AUTH_BODY)
        auth.change_password(session['user_id'], body.get('current'), body.get('password'),
                             self._session_token())
        self._send_json({'status': 'ok'})

    def _handle_mfa_setup(self, session):
        self._read_json(MAX_AUTH_BODY)
        result = auth.mfa_begin(session['user_id'], session['email'])
        result['status'] = 'ok'
        self._send_json(result)

    def _handle_mfa_enable(self, session):
        body = self._read_json(MAX_AUTH_BODY)
        codes = auth.mfa_enable(session['user_id'], body.get('code'))
        self._send_json({'status': 'ok', 'recovery_codes': codes})

    def _handle_mfa_disable(self, session):
        body = self._read_json(MAX_AUTH_BODY)
        auth.mfa_disable(session['user_id'], body.get('password'), body.get('code'))
        self._send_json({'status': 'ok'})

    def _handle_logout(self, session):
        auth.delete_session(self._session_token())
        self._send_json({'status': 'ok'}, headers=[self._cookie_header('', 0)])

    def _handle_save_data(self, session):
        data = self._read_json(MAX_DATA_BYTES)
        expected = self.headers.get('If-Match')
        if not expected:
            # Le versioni precedenti della dashboard non inviano If-Match:
            # una loro scheda rimasta aperta non deve poter scrivere.
            self._send_error_json(428, 'Pagina di una versione precedente della dashboard: ricaricala.')
            return
        try:
            new_version = auth.write_user_data(session['user_id'], data, expected)
        except auth.VersionConflict:
            self._send_error_json(409, 'I dati sono stati modificati da un\'altra scheda o finestra: ricarica la pagina.')
            return
        self._send_json({'status': 'ok'}, etag=new_version)

    def _handle_ai_keys(self, session):
        if not self._throttle_user(session, 'ai-keys'):
            return
        body = self._read_json(MAX_SMALL_BODY)
        try:
            result = ai_providers.manage_keys(session['user_id'], body)
        except ai_providers.AdvisorError as e:
            self._send_error_json(e.status, str(e))
            return
        result['status'] = 'ok'
        self._send_json(result)

    def _handle_import_statement(self, session):
        if not self._throttle_user(session, 'import'):
            return
        # Il file arriva in base64 dentro un JSON (come tutte le POST, che devono
        # essere application/json). Il limite del corpo tiene conto del +33% del base64.
        body = self._read_json(statement_import.MAX_FILE_BYTES * 4 // 3 + 4096)
        try:
            content = base64.b64decode(body.get('content_b64') or '', validate=True)
        except (binascii.Error, ValueError):
            raise HttpError(400, 'Contenuto del file non valido.')
        try:
            result = statement_import.parse_statement(
                body.get('account'), body.get('kind'), body.get('filename'), content,
                since=body.get('since') or statement_import.IMPORT_START_DATE)
        except ValueError as e:
            self._send_error_json(400, str(e))
            return
        except statement_import.ImportError_ as e:
            self._send_error_json(e.status, str(e))
            return
        except Exception as e:
            print('Errore imprevisto in /api/import-statement: %r' % e, file=sys.stderr)
            self._send_error_json(500, 'errore imprevisto leggendo il file.')
            return
        result['status'] = 'ok'
        self._send_json(result)

    def _handle_ask(self, session):
        if not self._throttle_user(session, 'ask'):
            return
        body = self._read_json(MAX_ASK_BODY)
        prompt = body.get('prompt')
        if not isinstance(prompt, str) or not prompt.strip():
            raise HttpError(400, 'Campo "prompt" mancante o vuoto.')
        try:
            text = ai_providers.ask(session['user_id'], prompt)
        except ai_providers.AdvisorError as e:
            self._send_error_json(e.status, str(e))
            return
        except Exception as e:
            # Qualunque altro errore imprevisto torna comunque in chat come
            # messaggio, invece di chiudere la connessione (che nella pagina
            # sembrerebbe "server non raggiungibile"). Il dettaglio resta nel log.
            print('Errore imprevisto in /api/ask: %r' % e, file=sys.stderr)
            self._send_error_json(500, 'errore imprevisto nel server.')
            return
        self._send_json({'status': 'ok', 'text': text})


def main():
    if not os.path.exists(DASHBOARD_FILE):
        print('ERRORE: non trovo "financial-dashboard.html" nella '
              'stessa cartella di server.py (%s). Assicurati che i due file '
              'siano insieme.' % BASE_DIR, file=sys.stderr)
        sys.exit(1)

    local = not PUBLIC_URL
    if PUBLIC_URL and not auth.encryption_enabled():
        print('ERRORE: online serve DASHBOARD_MASTER_KEY (genera con: python3 auth.py gen-master-key).',
              file=sys.stderr)
        sys.exit(1)
    auth.init_db()
    if auth.encryption_enabled():
        try:
            auth._aesgcm()  # fallisce subito se la chiave o il pacchetto non vanno
            encrypted = auth.encrypt_existing()
        except RuntimeError as e:
            print('ERRORE: %s' % e, file=sys.stderr)
            sys.exit(1)
        if encrypted:
            print('Cifrati %d segreti salvati in chiaro.' % encrypted)
    url = PUBLIC_URL + '/' if PUBLIC_URL else 'http://localhost:%d/' % PORT
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        # Di solito significa che la dashboard è già in esecuzione (un'altra
        # finestra del terminale ancora aperta): invece di un errore
        # incomprensibile, si apre semplicemente quella già attiva.
        print('La porta %d è già in uso: probabilmente la dashboard è già '
              'avviata in un\'altra finestra del Terminale.' % PORT)
        if local:
            print('Apro ' + url + ' — se non si carica, chiudi le altre finestre '
                  'del Terminale che eseguono server.py e riprova.')
            try:
                webbrowser.open(url)
            except Exception:
                pass
        return
    print('Dashboard Finanziaria — server avviato su %s:%d.' % (HOST, PORT))
    print('Indirizzo: ' + url)
    print('Database: ' + auth.DB_FILE)
    print('Registrazione: ' + auth.REGISTRATION_MODE +
          (' (genera un invito con: python3 auth.py create-invite)' if auth.REGISTRATION_MODE == 'invite' else ''))
    print('Cifratura a riposo: ' + ('attiva' if auth.encryption_enabled() else
          'NON attiva (imposta DASHBOARD_MASTER_KEY)'))
    print('Email: ' + ('attive' if mailer.ENABLED else
          'NON configurate (nessuna verifica email né "password dimenticata")'))
    if not SECURE_COOKIE and not local:
        print('ATTENZIONE: DASHBOARD_PUBLIC_URL non è https: il cookie di sessione non sarà Secure.',
              file=sys.stderr)
    print('Per fermare il server: Ctrl+C (oppure SIGTERM).')
    if local and HOST in ('127.0.0.1', 'localhost'):
        try:
            webbrowser.open(url)
        except Exception:
            pass  # se non riesce ad aprire il browser da solo, l'utente lo apre a mano

    # Chiusura della finestra del Terminale (SIGHUP) o "kill" (SIGTERM):
    # chiusura ordinata. Le scritture sono transazioni SQLite, quindi non
    # restano mai a metà.
    def on_signal(signum, frame):
        request_shutdown(server)
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGHUP, on_signal)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()
    print('\nServer fermato.')


if __name__ == '__main__':
    main()
