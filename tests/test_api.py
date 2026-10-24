"""Smoke test dell'API di server.py (account, sessioni, /api/data, CSRF, Host)."""

import unittest

from helpers import Client, TestServer, TEST_PASSWORD


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = TestServer().start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def client(self):
        return Client(self.server)

    # ----- Account e sessioni (AT-3.1, AT-3.2) -----

    def test_registrazione_accesso_uscita(self):
        c = self.client()
        email = c.register()
        status, _, body = c.get('/api/auth/me')
        self.assertEqual(status, 200)
        self.assertTrue(body['authenticated'])
        self.assertEqual(body['email'], email)

        status, _, _ = c.post('/api/auth/logout')
        self.assertEqual(status, 200)
        self.assertFalse(c.get('/api/auth/me')[2]['authenticated'])
        self.assertEqual(c.get('/api/data')[0], 401)

        # Un nuovo accesso con le stesse credenziali funziona.
        status, _ = c.login(email)
        self.assertEqual(status, 200)
        self.assertTrue(c.get('/api/auth/me')[2]['authenticated'])

    def test_cookie_di_sessione_httponly_samesite(self):
        c = self.client()
        status, headers, _ = c.post('/api/auth/register', {
            'email': 'cookie@example.test', 'password': TEST_PASSWORD})
        self.assertEqual(status, 201)
        cookie = headers['set-cookie']
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)

    def test_password_errata(self):
        c = self.client()
        email = c.register()
        other = self.client()
        status, _ = other.login(email, 'password-sbagliata-999')
        self.assertEqual(status, 401)

    def test_password_troppo_corta(self):
        status, _, _ = self.client().post('/api/auth/register', {
            'email': 'corta@example.test', 'password': 'breve'})
        self.assertEqual(status, 400)

    # ----- Sessione richiesta (AT-3.3) -----

    def test_endpoint_protetti_senza_sessione(self):
        c = self.client()
        for path in ('/api/data', '/api/ai-info', '/api/ai-keys'):
            self.assertEqual(c.get(path)[0], 401, path)
        for path in ('/api/data', '/api/ask', '/api/import-statement', '/api/auth/logout'):
            self.assertEqual(c.post(path, {}, csrf=False)[0], 401, path)

    # ----- /api/data con ETag / If-Match (AT-3.4) -----

    def test_dati_etag_if_match(self):
        c = self.client()
        c.register()
        status, headers, body = c.get('/api/data')
        self.assertEqual(status, 200)
        self.assertEqual(body, {})
        etag0 = headers['etag']

        status, headers, _ = c.post('/api/data', {'skender_fin_v5': {'a': 1}},
                                    headers={'If-Match': etag0})
        self.assertEqual(status, 200)
        etag1 = headers['etag']
        self.assertNotEqual(etag0, etag1)

        status, headers, body = c.get('/api/data')
        self.assertEqual(body, {'skender_fin_v5': {'a': 1}})
        self.assertEqual(headers['etag'], etag1)

        # Versione vecchia: un'altra scheda ha già scritto.
        status, _, _ = c.post('/api/data', {'skender_fin_v5': {'a': 2}},
                              headers={'If-Match': etag0})
        self.assertEqual(status, 409)
        self.assertEqual(c.get('/api/data')[2], {'skender_fin_v5': {'a': 1}})

        # Senza If-Match: pagina di una versione precedente.
        status, _, _ = c.post('/api/data', {'skender_fin_v5': {'a': 3}})
        self.assertEqual(status, 428)
        self.assertEqual(c.get('/api/data')[2], {'skender_fin_v5': {'a': 1}})

    def test_dati_separati_tra_utenti(self):
        a, b = self.client(), self.client()
        a.register()
        b.register()
        etag = a.get('/api/data')[1]['etag']
        a.post('/api/data', {'segreto': 'solo-di-a'}, headers={'If-Match': etag})
        self.assertEqual(a.get('/api/data')[2], {'segreto': 'solo-di-a'})
        self.assertEqual(b.get('/api/data')[2], {})

    # ----- CSRF, Content-Type, Host (AT-3.5, AT-3.6) -----

    def test_csrf_mancante_o_errato(self):
        c = self.client()
        c.register()
        etag = c.get('/api/data')[1]['etag']
        status, _, _ = c.post('/api/data', {'x': 1}, headers={'If-Match': etag}, csrf=False)
        self.assertEqual(status, 403)
        status, _, _ = c.post('/api/data', {'x': 1},
                              headers={'If-Match': etag, 'X-CSRF-Token': 'sbagliato'})
        self.assertEqual(status, 403)
        # Nulla è stato scritto.
        self.assertEqual(c.get('/api/data')[2], {})

    def test_content_type_non_json(self):
        c = self.client()
        c.register()
        status, _, _ = c.post('/api/auth/logout', raw=b'x=1',
                              headers={'Content-Type': 'text/plain'})
        self.assertEqual(status, 415)

    def test_origin_estraneo(self):
        c = self.client()
        c.register()
        status, _, _ = c.post('/api/auth/logout', headers={'Origin': 'http://evil.example'})
        self.assertEqual(status, 403)

    def test_host_non_consentito(self):
        c = self.client()
        self.assertEqual(c.get('/api/auth/me', headers={'Host': 'evil.example'})[0], 403)

    def test_intestazioni_di_sicurezza(self):
        status, headers, _ = self.client().get('/api/auth/me')
        self.assertEqual(headers['cache-control'], 'no-store')
        self.assertEqual(headers['x-content-type-options'], 'nosniff')
        self.assertIn("default-src 'none'", headers['content-security-policy'])

    def test_pagina_con_nonce_csp(self):
        status, headers, body = self.client().get('/')
        self.assertEqual(status, 200)
        nonce = headers['content-security-policy'].split("'nonce-")[1].split("'")[0]
        self.assertIn('<script nonce="%s"' % nonce, body)
        self.assertNotIn('<script>', body)


if __name__ == '__main__':
    unittest.main()
