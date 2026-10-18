#!/usr/bin/env python3
"""
Utenti, sessioni e archivio per-utente della Dashboard Finanziaria (SQLite).

Tutto sta in app.db (stessa cartella, permessi 600, fuori da git):
- users          email + hash della password (scrypt, sale casuale per utente)
- sessions       solo l'hash SHA-256 del token di sessione, più il token CSRF
- user_data      i dati della dashboard di ogni utente (l'ex dati.json) con un
                 numero di versione per il controllo di concorrenza (ETag)
- user_ai_config le chiavi API del consulente AI di ogni utente (l'ex ai-config.json)
- invites        codici d'invito monouso (solo l'hash)
- email_tokens   token monouso per verifica email e reimpostazione password (solo l'hash)
- recovery_codes codici di recupero dell'autenticazione a due fattori (solo l'hash)
- mfa_challenges accessi a metà: password giusta, in attesa del codice TOTP

Le chiavi API e i segreti TOTP sono cifrati con AES-256-GCM se è impostata
DASHBOARD_MASTER_KEY (obbligatoria quando il sito è online); la chiave maestra
sta nell'ambiente del servizio, mai nel database.

Ogni funzione che legge o scrive dati riceve lo user_id preso dalla SESSIONE,
mai da un parametro della richiesta.

Uso da riga di comando (sul server):
    python3 auth.py create-invite              genera un codice d'invito monouso
    python3 auth.py import-legacy EMAIL        copia dati.json e ai-config.json
                                               nell'account EMAIL (già registrato)
    python3 auth.py reset-password EMAIL       imposta una nuova password e chiude
                                               tutte le sessioni dell'utente
    python3 auth.py gen-master-key             stampa una nuova DASHBOARD_MASTER_KEY
    python3 auth.py encrypt-existing           cifra chiavi API e segreti TOTP ancora in chiaro
"""

import base64
import contextlib
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import struct
import sys
import threading
import time
import unicodedata
from urllib.parse import quote

import mailer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.environ.get('DASHBOARD_DB') or os.path.join(BASE_DIR, 'app.db')

# invite (default): serve un codice d'invito | open: chiunque | closed: nessuno
REGISTRATION_MODE = os.environ.get('DASHBOARD_REGISTRATION', 'invite')

SESSION_IDLE_SECONDS = 12 * 3600
SESSION_MAX_SECONDS = 14 * 24 * 3600
SESSION_TOUCH_SECONDS = 60

PASSWORD_MIN = 12
PASSWORD_MAX = 256
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 15, 8, 1
SCRYPT_MAXMEM = 64 * 1024 * 1024
# Ogni scrypt usa ~32 MB: limita quelli contemporanei.
_SCRYPT_SLOTS = threading.BoundedSemaphore(4)

EMAIL_RE = re.compile(r'^[^@\s]{1,64}@[^@\s]{1,250}\.[^@\s]{2,}$')

LOGIN_FAIL_PER_ACCOUNT = 10
LOGIN_FAIL_PER_IP = 20
REGISTER_FAIL_PER_IP = 10
THROTTLE_WINDOW = 15 * 60
EMAIL_REQUESTS_PER_HOUR = 5
VERIFY_TOKEN_SECONDS = 24 * 3600
RESET_TOKEN_SECONDS = 3600
MFA_CHALLENGE_SECONDS = 300
MFA_MAX_ATTEMPTS = 5
RECOVERY_CODES = 8

MASTER_KEY = os.environ.get('DASHBOARD_MASTER_KEY', '')
ENC_PREFIX = 'enc1:'
APP_NAME = 'Dashboard Finanziaria'


class AuthError(Exception):
    """Errore da mostrare nella pagina di accesso; status è il codice HTTP."""

    def __init__(self, message, status=400, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class VersionConflict(Exception):
    pass


# ===== Database =====

@contextlib.contextmanager
def db():
    # Una connessione per operazione (il server è multi-thread); autocommit,
    # le transazioni si aprono esplicitamente con BEGIN IMMEDIATE.
    conn = sqlite3.connect(DB_FILE, timeout=15, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        yield conn
    finally:
        conn.close()


def init_db():
    # Crea il file con permessi 600 prima che SQLite lo apra.
    fd = os.open(DB_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    os.close(fd)
    with db() as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                pw_hash TEXT NOT NULL,
                created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                csrf TEXT NOT NULL,
                created REAL NOT NULL,
                last_seen REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
            CREATE TABLE IF NOT EXISTS user_data(
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                data TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 0,
                updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS user_ai_config(
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                config TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS email_tokens(
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                purpose TEXT NOT NULL,
                expires REAL NOT NULL,
                used_at REAL);
            CREATE TABLE IF NOT EXISTS recovery_codes(
                code_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                used_at REAL);
            CREATE TABLE IF NOT EXISTS mfa_challenges(
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires REAL NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS invites(
                code_hash TEXT PRIMARY KEY,
                created REAL NOT NULL,
                used_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                used_at REAL);
        ''')
        # Colonne aggiunte dopo la prima versione: gli utenti già presenti
        # restano "verificati" e senza 2FA.
        columns = {row['name'] for row in conn.execute('PRAGMA table_info(users)')}
        for name, ddl in (('verified', 'INTEGER NOT NULL DEFAULT 1'),
                          ('totp_secret', 'TEXT'),
                          ('totp_enabled', 'INTEGER NOT NULL DEFAULT 0'),
                          ('totp_last_step', 'INTEGER NOT NULL DEFAULT 0')):
            if name not in columns:
                conn.execute('ALTER TABLE users ADD COLUMN %s %s' % (name, ddl))
    for suffix in ('', '-wal', '-shm'):
        try:
            os.chmod(DB_FILE + suffix, 0o600)
        except OSError:
            pass


# ===== Cifratura dei segreti a riposo =====

def encryption_enabled():
    return bool(MASTER_KEY)


def _aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise RuntimeError('Manca il pacchetto "cryptography": .venv/bin/python -m pip install cryptography')
    key = base64.urlsafe_b64decode(MASTER_KEY + '=' * (-len(MASTER_KEY) % 4))
    if len(key) != 32:
        raise RuntimeError('DASHBOARD_MASTER_KEY deve essere di 32 byte in base64 (usa: python3 auth.py gen-master-key).')
    return AESGCM(key)


def encrypt_secret(plaintext, context):
    """Cifra plaintext legandolo a context (es. 'ai:7'): un valore copiato in
    un'altra riga non si decifra. Senza chiave maestra restituisce il testo in chiaro."""
    if not MASTER_KEY or plaintext.startswith(ENC_PREFIX):
        return plaintext
    nonce = os.urandom(12)
    ciphertext = _aesgcm().encrypt(nonce, plaintext.encode('utf-8'), context.encode('utf-8'))
    return ENC_PREFIX + base64.urlsafe_b64encode(nonce + ciphertext).decode('ascii')


def decrypt_secret(stored, context):
    if not stored.startswith(ENC_PREFIX):
        return stored  # valore salvato prima dell'attivazione della cifratura
    if not MASTER_KEY:
        raise RuntimeError('Dati cifrati ma DASHBOARD_MASTER_KEY non è impostata.')
    raw = base64.urlsafe_b64decode(stored[len(ENC_PREFIX):])
    return _aesgcm().decrypt(raw[:12], raw[12:], context.encode('utf-8')).decode('utf-8')


def log_event(message):
    print('[sicurezza] %s' % message, file=sys.stderr)


def _may_mail(email):
    """Al massimo EMAIL_REQUESTS_PER_HOUR email all'ora per destinatario."""
    key = 'mail:' + email
    if THROTTLE.retry_after(key, EMAIL_REQUESTS_PER_HOUR, 3600):
        return False
    THROTTLE.hit(key, 3600)
    return True


# ===== Password =====

def _normalize_password(password):
    return unicodedata.normalize('NFKC', password)


def hash_password(password):
    salt = os.urandom(16)
    with _SCRYPT_SLOTS:
        digest = hashlib.scrypt(_normalize_password(password).encode('utf-8'), salt=salt,
                                n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, maxmem=SCRYPT_MAXMEM)
    return 'scrypt$%d$%d$%d$%s$%s' % (SCRYPT_N, SCRYPT_R, SCRYPT_P, salt.hex(), digest.hex())


def verify_password(password, stored):
    try:
        _, n, r, p, salt_hex, digest_hex = stored.split('$')
        salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
        with _SCRYPT_SLOTS:
            digest = hashlib.scrypt(_normalize_password(password).encode('utf-8'), salt=salt,
                                    n=int(n), r=int(r), p=int(p), maxmem=SCRYPT_MAXMEM,
                                    dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)


# Hash di confronto per gli utenti inesistenti: il login impiega lo stesso
# tempo, quindi non rivela quali email sono registrate.
_DUMMY_HASH = None


def _dummy_hash():
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_hex(8))
    return _DUMMY_HASH


def check_password_policy(password, email=''):
    if not isinstance(password, str):
        raise AuthError('Password non valida.')
    if len(password) < PASSWORD_MIN:
        raise AuthError('La password deve avere almeno %d caratteri.' % PASSWORD_MIN)
    if len(password) > PASSWORD_MAX:
        raise AuthError('La password può avere al massimo %d caratteri.' % PASSWORD_MAX)
    if email and password.lower() in (email, email.split('@')[0]):
        raise AuthError('La password non può coincidere con l\'email.')
    if len(set(password)) < 5:
        raise AuthError('La password è troppo semplice: usa caratteri più vari.')


def normalize_email(email):
    if not isinstance(email, str):
        raise AuthError('Email non valida.')
    email = email.strip().lower()
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise AuthError('Email non valida.')
    return email


# ===== Limitazione dei tentativi =====

class Throttle:
    """Contatore a finestra scorrevole in memoria (si azzera al riavvio)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._events = {}

    def _prune(self, key, window, now):
        events = [t for t in self._events.get(key, ()) if now - t < window]
        if events:
            self._events[key] = events
        else:
            self._events.pop(key, None)
        return events

    def retry_after(self, key, limit, window):
        """Secondi da attendere se key ha già raggiunto limit eventi, altrimenti 0."""
        now = time.time()
        with self._lock:
            events = self._prune(key, window, now)
            if len(events) >= limit:
                return max(1, int(window - (now - events[0])))
        return 0

    def hit(self, key, window):
        now = time.time()
        with self._lock:
            if len(self._events) > 20000:
                for k in list(self._events):
                    self._prune(k, window, now)
            self._events.setdefault(key, []).append(now)

    def clear(self, key):
        with self._lock:
            self._events.pop(key, None)


THROTTLE = Throttle()


def enforce_limit(key, limit, window, message='Troppi tentativi: riprova più tardi.'):
    wait = THROTTLE.retry_after(key, limit, window)
    if wait:
        raise AuthError(message, 429, retry_after=wait)
    THROTTLE.hit(key, window)


# ===== Registrazione e accesso =====

def _hash_secret(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def register(email, password, invite, ip):
    """Restituisce (user_id, email); user_id è None se l'indirizzo esisteva già
    (con le email attive la risposta resta identica)."""
    if REGISTRATION_MODE == 'closed':
        raise AuthError('Le registrazioni sono chiuse.', 403)
    wait = THROTTLE.retry_after('reg:' + ip, REGISTER_FAIL_PER_IP, THROTTLE_WINDOW)
    if wait:
        raise AuthError('Troppi tentativi: riprova più tardi.', 429, retry_after=wait)
    email = normalize_email(email)
    check_password_policy(password, email)
    invite_hash = None
    if REGISTRATION_MODE == 'invite':
        if not isinstance(invite, str) or not invite.strip():
            THROTTLE.hit('reg:' + ip, THROTTLE_WINDOW)
            raise AuthError('Serve un codice d\'invito.', 403)
        invite_hash = _hash_secret(invite.strip())
    pw_hash = hash_password(password)
    verified = 0 if mailer.ENABLED else 1
    generic = AuthError('Registrazione non riuscita: invito non valido o email già in uso.', 400)
    now = time.time()
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        try:
            if invite_hash:
                claimed = conn.execute(
                    'UPDATE invites SET used_at=? WHERE code_hash=? AND used_at IS NULL',
                    (now, invite_hash)).rowcount
                if claimed != 1:
                    conn.execute('ROLLBACK')
                    THROTTLE.hit('reg:' + ip, THROTTLE_WINDOW)
                    raise generic
            try:
                cur = conn.execute('INSERT INTO users(email, pw_hash, created, verified) VALUES(?,?,?,?)',
                                   (email, pw_hash, now, verified))
            except sqlite3.IntegrityError:
                conn.execute('ROLLBACK')
                THROTTLE.hit('reg:' + ip, THROTTLE_WINDOW)
                if mailer.ENABLED:
                    # Stessa risposta di una registrazione nuova (non si rivela
                    # quali indirizzi esistono); l'avviso arriva solo al titolare.
                    if _may_mail(email):
                        mailer.send_already_registered(email)
                    return None, email
                raise generic
            user_id = cur.lastrowid
            conn.execute('INSERT INTO user_data(user_id, data, version, updated) VALUES(?,?,0,?)',
                         (user_id, '{}', now))
            if invite_hash:
                conn.execute('UPDATE invites SET used_by=? WHERE code_hash=?', (user_id, invite_hash))
            conn.execute('COMMIT')
        except AuthError:
            raise
        except Exception:
            conn.execute('ROLLBACK')
            raise
    log_event('registrazione: utente %d' % user_id)
    if not verified and _may_mail(email):
        mailer.send_verification(email, create_email_token(user_id, 'verify', VERIFY_TOKEN_SECONDS))
    return user_id, email


def login(email, password, ip):
    """Verifica le credenziali e restituisce (user_id, email, serve_il_secondo_fattore)."""
    if not isinstance(email, str) or not isinstance(password, str) or len(password) > PASSWORD_MAX:
        raise AuthError('Email o password errata.', 401)
    email = email.strip().lower()[:254]
    for key, limit in (('ip:' + ip, LOGIN_FAIL_PER_IP), ('acct:' + email, LOGIN_FAIL_PER_ACCOUNT)):
        wait = THROTTLE.retry_after(key, limit, THROTTLE_WINDOW)
        if wait:
            raise AuthError('Troppi tentativi falliti: riprova tra qualche minuto.', 429, retry_after=wait)
    with db() as conn:
        row = conn.execute('SELECT id, email, pw_hash, verified, totp_enabled FROM users WHERE email=?',
                           (email,)).fetchone()
    ok = verify_password(password, row['pw_hash'] if row else _dummy_hash())
    if not (row and ok):
        THROTTLE.hit('ip:' + ip, THROTTLE_WINDOW)
        THROTTLE.hit('acct:' + email, THROTTLE_WINDOW)
        log_event('accesso fallito: %s da %s' % (email, ip))
        raise AuthError('Email o password errata.', 401)
    # Solo dopo la password giusta: chi non la conosce non scopre lo stato dell'account.
    if not row['verified']:
        raise AuthError('Email non ancora verificata: apri il link che ti abbiamo inviato.', 403)
    THROTTLE.clear('acct:' + email)
    return row['id'], row['email'], bool(row['totp_enabled'])


# ===== Sessioni =====

def create_session(user_id):
    """Crea una sessione nuova (un accesso non riusa mai un token precedente)."""
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    now = time.time()
    with db() as conn:
        conn.execute('DELETE FROM sessions WHERE created < ? OR last_seen < ?',
                     (now - SESSION_MAX_SECONDS, now - SESSION_IDLE_SECONDS))
        conn.execute('INSERT INTO sessions(token_hash, user_id, csrf, created, last_seen) VALUES(?,?,?,?,?)',
                     (_hash_secret(token), user_id, csrf, now, now))
    return token, csrf


def get_session(token):
    """Restituisce {user_id, email, csrf} se il token è una sessione valida."""
    if not token or len(token) > 200:
        return None
    token_hash = _hash_secret(token)
    now = time.time()
    with db() as conn:
        row = conn.execute(
            'SELECT s.user_id, s.csrf, s.created, s.last_seen, u.email, u.totp_enabled FROM sessions s '
            'JOIN users u ON u.id = s.user_id WHERE s.token_hash=?', (token_hash,)).fetchone()
        if row is None:
            return None
        if now - row['created'] > SESSION_MAX_SECONDS or now - row['last_seen'] > SESSION_IDLE_SECONDS:
            conn.execute('DELETE FROM sessions WHERE token_hash=?', (token_hash,))
            return None
        if now - row['last_seen'] > SESSION_TOUCH_SECONDS:
            conn.execute('UPDATE sessions SET last_seen=? WHERE token_hash=?', (now, token_hash))
    return {'user_id': row['user_id'], 'email': row['email'], 'csrf': row['csrf'],
            'mfa_enabled': bool(row['totp_enabled'])}


def delete_session(token):
    if token:
        with db() as conn:
            conn.execute('DELETE FROM sessions WHERE token_hash=?', (_hash_secret(token),))


def csrf_ok(session, header_value):
    return bool(header_value) and hmac.compare_digest(session['csrf'], header_value)


# ===== Token email: verifica indirizzo e reimpostazione password =====

def create_email_token(user_id, purpose, ttl):
    """Un solo token valido per scopo e utente: il nuovo annulla i precedenti."""
    token = secrets.token_urlsafe(32)
    with db() as conn:
        conn.execute('DELETE FROM email_tokens WHERE user_id=? AND purpose=?', (user_id, purpose))
        conn.execute('DELETE FROM email_tokens WHERE expires < ?', (time.time(),))
        conn.execute('INSERT INTO email_tokens(token_hash, user_id, purpose, expires) VALUES(?,?,?,?)',
                     (_hash_secret(token), user_id, purpose, time.time() + ttl))
    return token


def consume_email_token(token, purpose):
    """Segna il token come usato e restituisce lo user_id; AuthError se non valido."""
    invalid = AuthError('Link non valido o scaduto: richiedine uno nuovo.', 400)
    if not isinstance(token, str) or not 20 <= len(token) <= 100:
        raise invalid
    now = time.time()
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT user_id FROM email_tokens WHERE token_hash=? AND purpose=? '
                           'AND used_at IS NULL AND expires > ?', (_hash_secret(token), purpose, now)).fetchone()
        if row is None:
            conn.execute('ROLLBACK')
            raise invalid
        conn.execute('UPDATE email_tokens SET used_at=? WHERE token_hash=?', (now, _hash_secret(token)))
        conn.execute('COMMIT')
    return row['user_id']


def verify_email(token, ip):
    enforce_limit('verify:' + ip, 20, THROTTLE_WINDOW)
    user_id = consume_email_token(token, 'verify')
    with db() as conn:
        conn.execute('UPDATE users SET verified=1 WHERE id=?', (user_id,))
    log_event('email verificata: utente %d' % user_id)


def _find_user(email):
    with db() as conn:
        return conn.execute('SELECT id, email, verified FROM users WHERE email=?', (email,)).fetchone()


def request_verification(email, ip):
    """Nuovo link di verifica. La risposta è sempre la stessa."""
    enforce_limit('resend:' + ip, 10, 3600)
    if not mailer.ENABLED or not isinstance(email, str):
        return
    email = email.strip().lower()[:254]
    user = _find_user(email)
    if user and not user['verified'] and _may_mail(email):
        mailer.send_verification(email, create_email_token(user['id'], 'verify', VERIFY_TOKEN_SECONDS))


def request_password_reset(email, ip):
    """Invia il link di reimpostazione se l'account esiste. La risposta è sempre la stessa."""
    enforce_limit('forgot:' + ip, 10, 3600)
    if not isinstance(email, str):
        return
    email = email.strip().lower()[:254]
    if not mailer.ENABLED:
        print('Reimpostazione password richiesta ma SMTP non è configurato.', file=sys.stderr)
        return
    user = _find_user(email)
    if user and _may_mail(email):
        log_event('richiesta reimpostazione password: utente %d' % user['id'])
        mailer.send_password_reset(email, create_email_token(user['id'], 'reset', RESET_TOKEN_SECONDS))


def _set_password(conn, user_id, password, keep_token_hash=None):
    """Aggiorna la password e chiude le sessioni (tranne keep_token_hash)."""
    conn.execute('UPDATE users SET pw_hash=? WHERE id=?', (hash_password(password), user_id))
    conn.execute('DELETE FROM sessions WHERE user_id=? AND token_hash IS NOT ?', (user_id, keep_token_hash))
    conn.execute('DELETE FROM mfa_challenges WHERE user_id=?', (user_id,))


def reset_password(token, password, ip):
    enforce_limit('reset:' + ip, 20, THROTTLE_WINDOW)
    # Prima il criterio sulla password: un errore non brucia il link.
    check_password_policy(password)
    user_id = consume_email_token(token, 'reset')
    with db() as conn:
        user = conn.execute('SELECT email FROM users WHERE id=?', (user_id,)).fetchone()
        _set_password(conn, user_id, password)
        # Chi apre il link dimostra di controllare l'indirizzo. Il 2FA resta richiesto.
        conn.execute('UPDATE users SET verified=1 WHERE id=?', (user_id,))
        conn.execute("DELETE FROM email_tokens WHERE user_id=? AND purpose='reset'", (user_id,))
    log_event('password reimpostata: utente %d' % user_id)
    mailer.send_security_notice(user['email'], 'La password del tuo account è stata reimpostata.')


def change_password(user_id, current, new, session_token):
    if not isinstance(current, str) or not isinstance(new, str):
        raise AuthError('Dati non validi.')
    enforce_limit('chpw:%d' % user_id, 10, THROTTLE_WINDOW)
    with db() as conn:
        user = conn.execute('SELECT email, pw_hash FROM users WHERE id=?', (user_id,)).fetchone()
    if not verify_password(current, user['pw_hash']):
        raise AuthError('La password attuale non è corretta.', 403)
    check_password_policy(new, user['email'])
    with db() as conn:
        _set_password(conn, user_id, new, keep_token_hash=_hash_secret(session_token))
    log_event('password cambiata: utente %d' % user_id)
    mailer.send_security_notice(user['email'], 'La password del tuo account è stata cambiata.')


# ===== Autenticazione a due fattori (TOTP, RFC 6238) =====

def _totp_code(secret_b32, step):
    key = base64.b32decode(secret_b32)
    digest = hmac.new(key, struct.pack('>Q', step), 'sha1').digest()
    offset = digest[-1] & 15
    value = (struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7fffffff) % 1000000
    return '%06d' % value


def _check_totp(secret_b32, code, last_step):
    """Restituisce lo step accettato, oppure None. Uno step già usato non vale
    di nuovo (un codice intercettato non si riusa); tolleranza di ±30 secondi."""
    code = (code or '').replace(' ', '')
    if not (len(code) == 6 and code.isdigit()):
        return None
    now_step = int(time.time()) // 30
    accepted = None
    for step in (now_step - 1, now_step, now_step + 1):
        if hmac.compare_digest(_totp_code(secret_b32, step), code) and step > last_step:
            accepted = step
    return accepted


def _normalize_recovery(code):
    return (code or '').strip().lower().replace('-', '').replace(' ', '')


def _generate_recovery_codes(conn, user_id):
    conn.execute('DELETE FROM recovery_codes WHERE user_id=?', (user_id,))
    codes = []
    for _ in range(RECOVERY_CODES):
        raw = secrets.token_hex(5)
        conn.execute('INSERT INTO recovery_codes(code_hash, user_id) VALUES(?,?)', (_hash_secret(raw), user_id))
        codes.append(raw[:5] + '-' + raw[5:])
    return codes


def _verify_second_factor(conn, user_id, code):
    """True se code è un TOTP valido o un codice di recupero (che si consuma)."""
    row = conn.execute('SELECT totp_secret, totp_last_step FROM users WHERE id=?', (user_id,)).fetchone()
    secret = decrypt_secret(row['totp_secret'], 'totp:%d' % user_id)
    step = _check_totp(secret, code, row['totp_last_step'])
    if step is not None:
        conn.execute('UPDATE users SET totp_last_step=? WHERE id=?', (step, user_id))
        return True
    recovery = _normalize_recovery(code)
    if len(recovery) == 10:
        used = conn.execute('UPDATE recovery_codes SET used_at=? WHERE code_hash=? AND user_id=? AND used_at IS NULL',
                            (time.time(), _hash_secret(recovery), user_id)).rowcount
        if used:
            log_event('codice di recupero usato: utente %d' % user_id)
            return True
    return False


def mfa_begin(user_id, email):
    """Genera un segreto in attesa di conferma e lo restituisce (una volta)."""
    with db() as conn:
        row = conn.execute('SELECT totp_enabled FROM users WHERE id=?', (user_id,)).fetchone()
        if row['totp_enabled']:
            raise AuthError('L\'autenticazione a due fattori è già attiva.')
        secret = base64.b32encode(os.urandom(20)).decode('ascii')
        conn.execute('UPDATE users SET totp_secret=?, totp_last_step=0 WHERE id=?',
                     (encrypt_secret(secret, 'totp:%d' % user_id), user_id))
    uri = 'otpauth://totp/%s:%s?secret=%s&issuer=%s&digits=6&period=30' % (
        quote(APP_NAME), quote(email), secret, quote(APP_NAME))
    return {'secret': secret, 'uri': uri}


def mfa_enable(user_id, code):
    enforce_limit('mfa-setup:%d' % user_id, 10, THROTTLE_WINDOW)
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT totp_secret, totp_enabled, totp_last_step FROM users WHERE id=?',
                           (user_id,)).fetchone()
        if row['totp_enabled'] or not row['totp_secret']:
            conn.execute('ROLLBACK')
            raise AuthError('Avvia prima la configurazione.')
        secret = decrypt_secret(row['totp_secret'], 'totp:%d' % user_id)
        step = _check_totp(secret, code, row['totp_last_step'])
        if step is None:
            conn.execute('ROLLBACK')
            raise AuthError('Codice non corretto: controlla l\'ora del telefono e riprova.')
        conn.execute('UPDATE users SET totp_enabled=1, totp_last_step=? WHERE id=?', (step, user_id))
        codes = _generate_recovery_codes(conn, user_id)
        conn.execute('COMMIT')
    log_event('2FA attivata: utente %d' % user_id)
    return codes


def mfa_disable(user_id, password, code):
    enforce_limit('mfa-setup:%d' % user_id, 10, THROTTLE_WINDOW)
    with db() as conn:
        row = conn.execute('SELECT email, pw_hash, totp_enabled FROM users WHERE id=?', (user_id,)).fetchone()
        if not row['totp_enabled']:
            raise AuthError('L\'autenticazione a due fattori non è attiva.')
        if not isinstance(password, str) or not verify_password(password, row['pw_hash']):
            raise AuthError('Password non corretta.', 403)
        conn.execute('BEGIN IMMEDIATE')
        if not _verify_second_factor(conn, user_id, code):
            conn.execute('ROLLBACK')
            raise AuthError('Codice non corretto.', 403)
        conn.execute('UPDATE users SET totp_enabled=0, totp_secret=NULL WHERE id=?', (user_id,))
        conn.execute('DELETE FROM recovery_codes WHERE user_id=?', (user_id,))
        conn.execute('COMMIT')
    log_event('2FA disattivata: utente %d' % user_id)
    mailer.send_security_notice(row['email'], 'L\'autenticazione a due fattori è stata disattivata.')


def create_mfa_challenge(user_id):
    token = secrets.token_urlsafe(32)
    with db() as conn:
        conn.execute('DELETE FROM mfa_challenges WHERE expires < ?', (time.time(),))
        conn.execute('INSERT INTO mfa_challenges(token_hash, user_id, expires) VALUES(?,?,?)',
                     (_hash_secret(token), user_id, time.time() + MFA_CHALLENGE_SECONDS))
    return token


def complete_mfa(token, code, ip):
    """Secondo passo dell'accesso: restituisce (user_id, email)."""
    expired = AuthError('Accesso scaduto: inserisci di nuovo email e password.', 401)
    if not isinstance(token, str) or not isinstance(code, str) or len(token) > 100 or len(code) > 32:
        raise expired
    enforce_limit('mfa-ip:' + ip, 30, THROTTLE_WINDOW)
    token_hash = _hash_secret(token)
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        ch = conn.execute('SELECT user_id, expires, attempts FROM mfa_challenges WHERE token_hash=?',
                          (token_hash,)).fetchone()
        if ch is None or ch['expires'] < time.time() or ch['attempts'] >= MFA_MAX_ATTEMPTS:
            conn.execute('DELETE FROM mfa_challenges WHERE token_hash=?', (token_hash,))
            conn.execute('COMMIT')
            raise expired
        user_id = ch['user_id']
        if _verify_second_factor(conn, user_id, code):
            conn.execute('DELETE FROM mfa_challenges WHERE token_hash=?', (token_hash,))
            email = conn.execute('SELECT email FROM users WHERE id=?', (user_id,)).fetchone()['email']
            conn.execute('COMMIT')
            return user_id, email
        conn.execute('UPDATE mfa_challenges SET attempts=attempts+1 WHERE token_hash=?', (token_hash,))
        conn.execute('COMMIT')
    log_event('codice 2FA errato: utente %d da %s' % (user_id, ip))
    raise AuthError('Codice non corretto.', 401)


# ===== Dati dell'utente (ex dati.json) =====

def _etag(version):
    return '"v%d"' % version


def read_user_data(user_id):
    """Restituisce (dati, etag)."""
    with db() as conn:
        row = conn.execute('SELECT data, version FROM user_data WHERE user_id=?', (user_id,)).fetchone()
    if row is None:
        return {}, _etag(0)
    try:
        return json.loads(row['data']), _etag(row['version'])
    except json.JSONDecodeError:
        print('ATTENZIONE: i dati dell\'utente %d non sono leggibili.' % user_id, file=sys.stderr)
        return {}, _etag(row['version'])


def write_user_data(user_id, data, expected_etag):
    """Scrive solo se i dati sono ancora alla versione da cui è partita la
    pagina (altrimenti VersionConflict). Restituisce il nuovo etag."""
    content = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    match = re.fullmatch(r'"v(\d+)"', expected_etag or '')
    if not match:
        raise VersionConflict()
    expected = int(match.group(1))
    with db() as conn:
        updated = conn.execute(
            'UPDATE user_data SET data=?, version=version+1, updated=? WHERE user_id=? AND version=?',
            (content, time.time(), user_id, expected)).rowcount
    if updated != 1:
        raise VersionConflict()
    return _etag(expected + 1)


# ===== Chiavi API del consulente (ex ai-config.json) =====

def read_ai_config(user_id):
    with db() as conn:
        row = conn.execute('SELECT config FROM user_ai_config WHERE user_id=?', (user_id,)).fetchone()
    if not row:
        return None
    config = json.loads(row['config'])
    for key in config.get('keys', []):
        if isinstance(key.get('value'), str):
            key['value'] = decrypt_secret(key['value'], 'ai:%d' % user_id)
    return config


def write_ai_config(user_id, config):
    config = json.loads(json.dumps(config))  # copia: l'originale resta in chiaro per chi lo usa
    for key in config.get('keys', []):
        if isinstance(key.get('value'), str):
            key['value'] = encrypt_secret(key['value'], 'ai:%d' % user_id)
    with db() as conn:
        conn.execute('INSERT INTO user_ai_config(user_id, config) VALUES(?,?) '
                     'ON CONFLICT(user_id) DO UPDATE SET config=excluded.config',
                     (user_id, json.dumps(config, ensure_ascii=False)))


# ===== Riga di comando =====

def _user_id_by_email(conn, email):
    row = conn.execute('SELECT id FROM users WHERE email=?', (email.strip().lower(),)).fetchone()
    if row is None:
        sys.exit('Nessun utente con email %s.' % email)
    return row['id']


def cli_create_invite():
    code = secrets.token_urlsafe(12)
    with db() as conn:
        conn.execute('INSERT INTO invites(code_hash, created) VALUES(?,?)', (_hash_secret(code), time.time()))
    print('Codice d\'invito (monouso, mostrato una sola volta):\n  ' + code)


def cli_import_legacy(email):
    with db() as conn:
        user_id = _user_id_by_email(conn, email)
    imported = []
    data_path = os.path.join(BASE_DIR, 'dati.json')
    if os.path.exists(data_path):
        with open(data_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            sys.exit('dati.json non contiene un oggetto JSON.')
        _, etag = read_user_data(user_id)
        write_user_data(user_id, data, etag)
        imported.append('dati.json')
    config_path = os.path.join(BASE_DIR, 'ai-config.json')
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            write_ai_config(user_id, json.load(f))
        imported.append('ai-config.json')
    print('Importati: %s.' % (', '.join(imported) if imported else 'niente (file non trovati)'))
    if imported:
        print('Verifica dal sito, poi sposta i file originali fuori dal server.')


def cli_reset_password(email):
    password = getpass.getpass('Nuova password: ')
    if password != getpass.getpass('Ripeti la password: '):
        sys.exit('Le due password non coincidono.')
    try:
        check_password_policy(password, email.strip().lower())
    except AuthError as e:
        sys.exit(str(e))
    pw_hash = hash_password(password)
    with db() as conn:
        user_id = _user_id_by_email(conn, email)
        conn.execute('UPDATE users SET pw_hash=? WHERE id=?', (pw_hash, user_id))
        conn.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
    print('Password aggiornata; tutte le sessioni dell\'utente sono state chiuse.')


def encrypt_existing():
    """Cifra chiavi API e segreti TOTP salvati in chiaro (idempotente)."""
    if not MASTER_KEY:
        return 0
    changed = 0
    with db() as conn:
        user_ids = [r['user_id'] for r in conn.execute('SELECT user_id FROM user_ai_config')]
    for user_id in user_ids:
        with db() as conn:
            raw = json.loads(conn.execute('SELECT config FROM user_ai_config WHERE user_id=?',
                                          (user_id,)).fetchone()['config'])
        if any(isinstance(k.get('value'), str) and not k['value'].startswith(ENC_PREFIX)
               for k in raw.get('keys', [])):
            write_ai_config(user_id, read_ai_config(user_id))
            changed += 1
    with db() as conn:
        for row in conn.execute('SELECT id, totp_secret FROM users WHERE totp_secret IS NOT NULL').fetchall():
            if not row['totp_secret'].startswith(ENC_PREFIX):
                conn.execute('UPDATE users SET totp_secret=? WHERE id=?',
                             (encrypt_secret(row['totp_secret'], 'totp:%d' % row['id']), row['id']))
                changed += 1
    return changed


def main(argv):
    if argv[:1] == ['gen-master-key']:
        print(base64.urlsafe_b64encode(os.urandom(32)).decode('ascii'))
        return
    init_db()
    if argv[:1] == ['create-invite']:
        cli_create_invite()
    elif len(argv) == 2 and argv[0] == 'import-legacy':
        cli_import_legacy(argv[1])
    elif len(argv) == 2 and argv[0] == 'reset-password':
        cli_reset_password(argv[1])
    elif argv[:1] == ['encrypt-existing']:
        if not MASTER_KEY:
            sys.exit('Imposta prima DASHBOARD_MASTER_KEY.')
        print('Righe cifrate: %d.' % encrypt_existing())
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main(sys.argv[1:])
