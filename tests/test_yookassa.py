from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError
import uuid
import socket
from accounts import authenticate, get_user
from catalog_server import create_server
import yookassa_payments as pay

class YooKassaTests(unittest.TestCase):
    def test_api_transport_retries_dns_addresses_with_hostname_verification(self):
        from unittest.mock import MagicMock
        pay.ProviderHTTPSConnection.good_addresses.clear()
        connection=pay.ProviderHTTPSConnection('api.yookassa.ru',timeout=12)
        bad,good,tls=MagicMock(),MagicMock(),MagicMock()
        bad.connect.side_effect=TimeoutError('unreachable address')
        context=MagicMock();context.wrap_socket.return_value=tls
        connection._context=context
        records=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('192.0.2.1',443)),(socket.AF_INET,socket.SOCK_STREAM,6,'',('192.0.2.2',443))]
        with patch('socket.getaddrinfo',return_value=records),patch('socket.socket',side_effect=[bad,good]):
            connection.connect()
        bad.close.assert_called_once()
        context.wrap_socket.assert_called_once_with(good,server_hostname='api.yookassa.ru')
        self.assertIs(connection.sock,tls)
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.folder=Path(self.temporary.name)
        self.database=str(self.folder/'catalog.db')
        self.server=create_server(port=0,database=self.database)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.account=authenticate(self.database,'register',dict(username='PaymentTester',password='payment-test-password'))
        self.user=self.account['user'];self.payments={};self.calls=[]
        settings=dict(shop_id='123456',test=False,enabled=True,fiscalization_enabled=False)
        (self.folder/'yookassa-shop.json').write_text(json.dumps(settings))
        (self.folder/'yookassa.key').write_text('isolated-unit-test-key')
        marker=dict(shop_id='123456',checked=time.time(),key_hash=hashlib.sha256(b'isolated-unit-test-key').hexdigest())
        (self.folder/'yookassa-ready.json').write_text(json.dumps(marker))
        self.api=patch('yookassa_payments.request',side_effect=self.vendor);self.api.start()

    def tearDown(self):
        self.api.stop();self.server.shutdown();self.server.server_close();self.temporary.cleanup()

    def vendor(self,database,method,path,payload=None,idempotency=None):
        self.calls.append((method,path,payload,idempotency))
        if method=='POST':
            ident=str(uuid.uuid4())
            result=dict(id=ident,status='pending',paid=False,test=False,recipient=dict(account_id='123456'),
                amount=payload['amount'],metadata=payload['metadata'],
                confirmation=dict(confirmation_url='https://yoomoney.ru/checkout/payments/v2/contract?orderId='+ident))
            self.payments[ident]=result
            return result
        return self.payments[path.rsplit('/',1)[-1]]

    def http(self,path,body=None,token=None):
        headers={}
        if token:headers['Authorization']='Bearer '+token
        raw=json.dumps(body).encode() if body is not None else None
        req=Request(f'http://127.0.0.1:{self.server.server_port}'+path,data=raw,headers=headers)
        with build_opener(ProxyHandler({})).open(req,timeout=5) as response:return json.loads(response.read())

    def checkout(self,plan='month'):
        return pay.create_checkout(self.database,self.user,plan,'buyer@example.com')

    def paid(self,ident):
        self.payments[ident].update(status='succeeded',paid=True)

    def test_price_binding_reuse_and_activation_once(self):
        order=self.http('/api/subscription/order',dict(plan='month',provider='yookassa',email='buyer@example.com',amount=1,user_id=999,premium=True),self.account['token'])
        self.assertEqual(order['amount'],100)
        self.assertEqual(self.calls[0][2]['amount'],dict(value='100.00',currency='RUB'))
        self.assertEqual(self.calls[0][2]['metadata']['user_id'],str(self.user['id']))
        self.assertNotIn('save_payment_method',self.calls[0][2])
        self.assertEqual(self.checkout()['url'],order['url'])
        self.assertEqual(len(self.payments),1)
        ident=next(iter(self.payments));self.assertFalse(pay.check_payment(self.database,ident,force=True))
        self.paid(ident);self.assertTrue(pay.check_payment(self.database,ident,force=True))
        until=get_user(self.database,'Bearer '+self.account['token'])['premium_until']
        self.assertAlmostEqual(until-time.time(),30*86400,delta=3)
        pay.check_payment(self.database,ident,force=True)
        self.assertEqual(get_user(self.database,'Bearer '+self.account['token'])['premium_until'],until)

    def test_forged_webhook_uses_authoritative_payment_not_body(self):
        self.checkout();ident=next(iter(self.payments))
        notification=dict(type='notification',event='payment.succeeded',object=dict(id=ident,status='succeeded',paid=True))
        self.assertTrue(self.http('/api/payments/yookassa',notification)['ok'])
        self.assertFalse(get_user(self.database,'Bearer '+self.account['token'])['premium'])
        before=len(self.calls)
        notification['object']['id']=str(uuid.uuid4())
        self.http('/api/payments/yookassa',notification)
        self.assertEqual(len(self.calls),before)

    def test_wrong_shop_mode_amount_currency_owner_or_metadata_cannot_activate(self):
        self.checkout();ident=next(iter(self.payments));self.paid(ident)
        valid=dict(self.payments[ident])
        changes=[dict(test=True),dict(paid=False),dict(recipient={'account_id':'other'}),
            dict(amount={'value':'99.00','currency':'RUB'}),dict(amount={'value':'100.00','currency':'USD'}),
            dict(metadata=dict(valid['metadata'],user_id='999')),dict(metadata=dict(valid['metadata'],order_code='forged'))]
        for change in changes:
            self.payments[ident]=dict(valid,**change)
            self.assertFalse(pay.check_payment(self.database,ident,force=True))
        self.assertFalse(get_user(self.database,'Bearer '+self.account['token'])['premium'])

    def test_receipt_email_required_and_legacy_clients_rejected(self):
        with self.assertRaises(HTTPError) as result:
            self.http('/api/subscription/order',dict(plan='month'),self.account['token'])
        self.assertEqual(result.exception.code,426)
        with self.assertRaises(HTTPError) as result:
            self.http('/api/subscription/order',dict(plan='month',provider='yookassa',email='invalid'),self.account['token'])
        self.assertEqual(result.exception.code,400)
        self.assertEqual(len(self.payments),0)
        with self.assertRaises(ValueError):pay.confirmation_url('https://evil.example/checkout')
        with self.assertRaises(ValueError):pay.confirmation_url('https://user:secret@yoomoney.ru/checkout')

    def test_failed_creation_retries_same_idempotency_key(self):
        with patch('yookassa_payments.request',side_effect=pay.PaymentError('timeout')):
            with self.assertRaises(pay.PaymentError):self.checkout()
        with closing(sqlite3.connect(self.database)) as db:
            code=db.execute('SELECT order_code FROM yookassa_payments').fetchone()[0]
        order=self.checkout();self.assertEqual(order['code'],code)
        self.assertEqual(self.calls[-1][3],code)

    def test_cancel_then_new_checkout_does_not_reuse_canceled_order(self):
        old=self.checkout();ident=next(iter(self.payments));self.payments[ident]['status']='canceled'
        self.assertFalse(pay.check_payment(self.database,ident,force=True))
        new=self.checkout();self.assertNotEqual(old['code'],new['code'])
        self.assertFalse(get_user(self.database,'Bearer '+self.account['token'])['premium'])
