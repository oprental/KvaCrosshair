from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import urllib.request
from urllib.error import HTTPError
from catalog_server import create_server
from accounts import authenticate, get_user, logout, AccountError, password_hash
from main import DEFAULT
from unittest.mock import patch
from sharing import pack


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.database = str(Path(self.folder.name) / 'catalog.db')
        self.server = create_server(port=0, database=self.database)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.folder.cleanup()

    def request(self, endpoint, payload=None, token='', method=None):
        request = urllib.request.Request(self.base+endpoint, data=json.dumps(payload).encode() if payload is not None else None,
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer '+token}, method=method)
        with self.opener.open(request) as response:
            return json.load(response)

    def test_guest_browses_but_cannot_publish(self):
        self.assertEqual(self.request('/api/crosshairs'), {'items': []})
        for token in ('', 'forged-token-does-not-exist'):
            with self.assertRaises(HTTPError) as error:
                self.request('/api/crosshairs', pack(DEFAULT), token)
            self.assertEqual(error.exception.code, 401)

    def test_subscription_order_and_color_are_server_controlled(self):
        from subscriptions import activate
        account = self.request('/api/auth/register', dict(username='ProSecurity', password='pro-security-password'))
        token = account['token']
        with patch('subscriptions.payments_ready', return_value=True):
            order = self.request('/api/subscription/order', dict(plan='month', amount=1, user_id=999, premium=True), token)
        self.assertEqual(order['amount'], 100)
        self.assertFalse(self.request('/api/auth/me', token=token)['user']['premium'])
        for path, payload, status in [('/api/subscription/color', {'color':'#ff81bd'},403), ('/api/subscription/activate', {'code':order['code']},404)]:
            with self.assertRaises(HTTPError) as error:
                self.request(path, payload, token)
            self.assertEqual(error.exception.code, status)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute('SELECT user_id FROM subscription_orders WHERE code=?',(order['code'],)).fetchone()[0], account['user']['id'])
        activate(self.database, order['code'], 'verified-security-test', 100)
        with self.assertRaises(HTTPError) as error:
            self.request('/api/subscription/color', {'color':'<script>'}, token)
        self.assertEqual(error.exception.code, 400)
        user = self.request('/api/subscription/color', {'color':'#ff81bd'}, token)['user']
        self.assertTrue(user['premium'])
        self.assertEqual(user['nick_color'], '#ff81bd')

    def test_only_owner_can_delete_publication(self):
        owner = self.request('/api/auth/register', dict(username='Owner', password='owner-test-password'))
        other = self.request('/api/auth/register', dict(username='Other', password='other-test-password'))
        ident = self.request('/api/crosshairs', pack(DEFAULT, 'Other'), owner['token'])['id']
        item = self.request('/api/crosshairs')['items'][0]
        self.assertEqual(item['id'], ident)
        self.assertEqual(item['owner_id'], owner['user']['id'])
        endpoint = '/api/crosshairs/'+str(ident)
        for token, status in [('', 401), ('forged-token', 401), (other['token'], 403)]:
            with self.assertRaises(HTTPError) as error:
                self.request(endpoint, token=token, method='DELETE')
            self.assertEqual(error.exception.code, status)
            self.assertEqual(len(self.request('/api/crosshairs')['items']), 1)
        with closing(sqlite3.connect(self.database)) as db, db:
            legacy = db.execute('INSERT INTO crosshairs(package,created,owner_id) VALUES (?,0,NULL)', (json.dumps(pack(DEFAULT, 'Owner')),)).lastrowid
        with self.assertRaises(HTTPError) as error:
            self.request('/api/crosshairs/'+str(legacy), token=owner['token'], method='DELETE')
        self.assertEqual(error.exception.code, 403)
        self.assertTrue(self.request(endpoint, token=owner['token'], method='DELETE')['ok'])
        self.assertEqual([p['id'] for p in self.request('/api/crosshairs')['items']], [legacy])
        with self.assertRaises(HTTPError) as error:
            self.request(endpoint, token=owner['token'], method='DELETE')
        self.assertEqual(error.exception.code, 404)
        self.request('/api/auth/logout', {}, owner['token'])
        with self.assertRaises(HTTPError) as error:
            self.request('/api/crosshairs/'+str(legacy), token=owner['token'], method='DELETE')
        self.assertEqual(error.exception.code, 401)

    def test_register_login_identity_and_logout(self):
        credentials = dict(username='Player_1', password='long-test-password')
        registration = self.request('/api/auth/register', credentials)
        token = registration['token']
        self.assertEqual(self.request('/api/auth/me', token=token)['user']['username'], 'Player_1')
        package = pack(DEFAULT, 'Pretend_author')
        self.request('/api/crosshairs', package, token)
        self.assertEqual(self.request('/api/crosshairs')['items'][0]['author'], 'Player_1')
        with self.assertRaises(HTTPError) as error:
            self.request('/api/auth/register', dict(credentials, username='player_1'))
        self.assertEqual(error.exception.code, 409)
        with self.assertRaises(HTTPError) as error:
            self.request('/api/auth/login', dict(credentials, password='wrong-password-123'))
        self.assertEqual(error.exception.code, 401)
        logged_in = self.request('/api/auth/login', dict(credentials, username='PLAYER_1'))
        self.assertEqual(logged_in['user']['username'], 'Player_1')
        self.request('/api/auth/logout', {}, token)
        self.assertIsNone(get_user(self.database, 'Bearer '+token))
        with self.assertRaises(HTTPError) as error:
            self.request('/api/crosshairs', package, token)
        self.assertEqual(error.exception.code, 401)
        with closing(sqlite3.connect(self.database)) as db:
            salt, hashed = db.execute('SELECT salt,password_hash FROM users').fetchone()
            self.assertNotEqual(hashed, credentials['password'])
            self.assertEqual(len(salt), 32)
            self.assertTrue(hashed.startswith('scrypt-v2$'))
            hashes = [r[0] for r in db.execute('SELECT token_hash FROM sessions')]
            self.assertNotIn(logged_in['token'], hashes)

    def test_password_validation_expiry_and_throttling(self):
        for credentials in [dict(username='x', password='test-password-123'), dict(username='GoodName', password='short')]:
            with self.assertRaises(AccountError):
                authenticate(self.database, 'register', credentials)
        result = authenticate(self.database, 'register', dict(username='ExpiryUser', password='test-password-123'))
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('UPDATE sessions SET expires=0')
        self.assertIsNone(get_user(self.database, 'Bearer '+result['token']))
        for _ in range(12):
            with self.assertRaises(HTTPError) as error:
                self.request('/api/auth/login', dict(username='GoodName', password='short'))
            self.assertEqual(error.exception.code, 400)
        with self.assertRaises(HTTPError) as error:
            self.request('/api/auth/login', dict(username='GoodName', password='short'))
        self.assertEqual(error.exception.code, 429)

    def test_old_catalogue_migrates_without_losing_entries(self):
        old = str(Path(self.folder.name) / 'old.db')
        with closing(sqlite3.connect(old)) as db, db:
            db.execute('CREATE TABLE crosshairs (id INTEGER PRIMARY KEY, package TEXT, created REAL)')
            db.execute('INSERT INTO crosshairs VALUES (1,?,0)', (json.dumps(pack(DEFAULT, 'Legacy')),))
        server = create_server(port=0, database=old)
        server.server_close()
        with closing(sqlite3.connect(old)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM crosshairs').fetchone()[0], 1)
            self.assertIsNone(db.execute('SELECT owner_id FROM crosshairs').fetchone()[0])

    def test_legacy_password_hash_upgrades_without_reset(self):
        salt = 'aa'*16
        password = 'legacy-password-test'
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('INSERT INTO users(username,username_key,salt,password_hash,created) VALUES (?,?,?,?,0)',
                       ('LegacyUser','legacyuser',salt,password_hash(password,salt,legacy=True)))
        result = authenticate(self.database, 'login', dict(username='LegacyUser',password=password))
        self.assertEqual(result['user']['username'], 'LegacyUser')
        with closing(sqlite3.connect(self.database)) as db:
            self.assertTrue(db.execute('SELECT password_hash FROM users').fetchone()[0].startswith('scrypt-v2$'))


if __name__ == '__main__':
    unittest.main()
