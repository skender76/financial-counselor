"""Strumenti comuni ai test: server di prova su DB temporaneo e client HTTP."""

import base64
import http.client
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Chiave solo per i test (32 byte in base64): non è quella di .env.
TEST_MASTER_KEY = base64.urlsafe_b64encode(b'chiave-solo-per-i-test-0123456!!').decode('ascii')
TEST_PASSWORD = 'password-di-prova-123'

# Il server apre il browser all'avvio in locale: nei test no.
LAUNCHER = (
    "import runpy, webbrowser; webbrowser.open = lambda *a, **k: False; "
    "runpy.run_path('server.py', run_name='__main__')"
)


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class TestServer:
    """Avvia server.py su una porta libera con un DB temporaneo."""

    def __init__(self):
        self.port = free_port()
        self.tmp = tempfile.mkdtemp(prefix='dashboard-test-')
        self.db = os.path.join(self.tmp, 'test.db')
        self.proc = None

    def start(self):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith('DASHBOARD_')}
        env.update(
            DASHBOARD_PORT=str(self.port),
            DASHBOARD_HOST='127.0.0.1',
            DASHBOARD_DB=self.db,
            DASHBOARD_REGISTRATION='open',
            DASHBOARD_MASTER_KEY=TEST_MASTER_KEY,
        )
        self.proc = subprocess.Popen(
            [sys.executable, '-c', LAUNCHER], cwd=ROOT, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        deadline = time.time() + 15
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError('Il server di prova si è fermato: %s' %
                                   self.proc.stderr.read().decode('utf-8', 'replace'))
            try:
                socket.create_connection(('127.0.0.1', self.port), timeout=0.2).close()
                return self
            except OSError:
                time.sleep(0.1)
        self.stop()
        raise RuntimeError('Il server di prova non si è avviato in tempo.')

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if self.proc and self.proc.stderr:
            self.proc.stderr.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


class Client:
    """Client HTTP con cookie di sessione e token CSRF."""

    def __init__(self, server):
        self.server = server
        self.cookie = None
        self.csrf = None

    def request(self, method, path, body=None, headers=None, raw=None, csrf=True):
        """Restituisce (status, headers, json|testo). `raw` = corpo già pronto."""
        hdrs = {'Host': 'localhost:%d' % self.server.port}
        data = raw
        if body is not None:
            data = json.dumps(body).encode('utf-8')
        if method == 'POST':
            hdrs['Content-Type'] = 'application/json'
            if csrf and self.csrf:
                hdrs['X-CSRF-Token'] = self.csrf
        if self.cookie:
            hdrs['Cookie'] = self.cookie
        hdrs.update(headers or {})
        conn = http.client.HTTPConnection('127.0.0.1', self.server.port, timeout=20)
        try:
            conn.request(method, path, body=data if method == 'POST' else None, headers=hdrs)
            resp = conn.getresponse()
            payload = resp.read().decode('utf-8')
            set_cookie = resp.getheader('Set-Cookie')
            if set_cookie:
                pair = set_cookie.split(';', 1)[0]
                # Cookie svuotato dal logout: lo dimentichiamo.
                self.cookie = pair if not pair.endswith('=') else None
            try:
                parsed = json.loads(payload)
            except ValueError:
                parsed = payload
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, parsed
        finally:
            conn.close()

    def get(self, path, **kw):
        return self.request('GET', path, **kw)

    def post(self, path, body=None, **kw):
        return self.request('POST', path, body=body if body is not None else {}, **kw)

    def register(self, email=None, password=TEST_PASSWORD):
        email = email or 'utente-%s@example.test' % uuid.uuid4().hex[:10]
        status, _, body = self.post('/api/auth/register', {'email': email, 'password': password})
        if status != 201:
            raise AssertionError('registrazione fallita: %s %s' % (status, body))
        self.csrf = body['csrf']
        return email

    def login(self, email, password=TEST_PASSWORD):
        status, _, body = self.post('/api/auth/login', {'email': email, 'password': password})
        if status == 200:
            self.csrf = body['csrf']
        return status, body
