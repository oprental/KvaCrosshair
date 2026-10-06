from contextlib import closing
from pathlib import Path
import tempfile
import time
import sqlite3
import unittest
from catalog_server import create_server
from accounts import authenticate, get_user
from subscriptions import create_order, activate, process_payment, PLANS


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.db = str(Path(self.folder.name) / 'catalog.db')
        self.server = create_server(port=0, database=self.db)
        self.account = authenticate(self.db, 'register', dict(username='ProTester', password='subscription-test-password'))
        self.user = self.account['user']

    def tearDown(self):
        self.server.server_close()
        self.folder.cleanup()

    def test_plans_activation_expiry_and_renewal(self):
        self.assertEqual([value[0] for value in PLANS.values()], [100, 250, 500, 900])
        self.assertFalse(self.user['premium'])
        order = create_order(self.db, self.user, 'month')
        self.assertEqual(create_order(self.db, self.user, 'month')['code'], order['code'])
        result = activate(self.db, order['code'], 'verified-payment-1', 100)
        self.assertTrue(result['premium'])
        self.assertAlmostEqual(result['premium_until'] - time.time(), 30 * 86400, delta=2)
        again = activate(self.db, order['code'], 'verified-payment-1', 100)
        self.assertEqual(again['premium_until'], result['premium_until'])
        renewal = create_order(self.db, self.user, 'quarter')
        with self.assertRaises(ValueError):
            activate(self.db, renewal['code'], 'verified-payment-1', 250)
        renewed = activate(self.db, renewal['code'], 'verified-payment-2', 250)
        self.assertGreater(renewed['premium_until'], result['premium_until'] + 80 * 86400)
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('UPDATE users SET premium_until=0')
        self.assertFalse(get_user(self.db, 'Bearer '+self.account['token'])['premium'])

    def test_unverified_wrong_amount_or_wrong_code_cannot_activate(self):
        order = create_order(self.db, self.user, 'month')
        payment = dict(id=100, type='donation', status='success', currency='RUB', sum='100.00', vars={'comment':order['code']})
        for change in [dict(status='pending'), dict(type='withdraw'), dict(currency='USD'), dict(sum='99'), dict(test=True), dict(vars={'comment':'wrong'})]:
            self.assertFalse(process_payment(self.db, dict(payment, **change)))
        self.assertFalse(get_user(self.db, 'Bearer '+self.account['token'])['premium'])
        self.assertTrue(process_payment(self.db, payment))
        until = get_user(self.db, 'Bearer '+self.account['token'])['premium_until']
        self.assertTrue(process_payment(self.db, payment))
        self.assertEqual(get_user(self.db, 'Bearer '+self.account['token'])['premium_until'], until)
