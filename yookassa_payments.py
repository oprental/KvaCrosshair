"""Server-only YooKassa checkout; a notification alone can never grant PRO."""
from contextlib import closing
import base64
from decimal import Decimal, InvalidOperation
import hashlib
import http.client
import json
from pathlib import Path
import re
import sqlite3
import socket
import threading
import time
import urllib.request
from urllib.parse import urlsplit

API = 'https://api.yookassa.ru/v3'
ORIGIN = 'https://kvacrosshair.online'
PAYMENT_ID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
creation_lock = threading.Lock()

class ProviderHTTPSConnection(http.client.HTTPSConnection):
    """Try another DNS address on transport failure; always validate hostname/TLS."""
    good_addresses = {}
    def connect(self):
        addresses=list(dict.fromkeys((family,kind,protocol,address) for family,kind,protocol,_,address
            in socket.getaddrinfo(self.host,self.port,type=socket.SOCK_STREAM)))
        cached=self.good_addresses.get((self.host,self.port))
        if cached and time.monotonic()-cached[1]<60:
            addresses.sort(key=lambda entry:entry[3]!=cached[0])
        error=None
        for family,kind,protocol,address in addresses[:3]:
            connection=socket.socket(family,kind,protocol)
            try:
                connection.settimeout(min(4,self.timeout or 4))
                connection.connect(address)
                self.sock=self._context.wrap_socket(connection,server_hostname=self.host)
                self.sock.settimeout(self.timeout)
                self.good_addresses[(self.host,self.port)]=(address,time.monotonic())
                return
            except OSError as exc:
                error=exc
                connection.close()
        if error:raise error
        raise OSError('Payment API has no available address')

class ProviderHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self,request):
        return self.do_open(ProviderHTTPSConnection,request,context=self._context)

class PaymentError(ValueError):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise PaymentError('Платёжный сервис вернул неподдерживаемый ответ.')

def configured(database):
    return (Path(database).parent / 'yookassa-shop.json').exists()

def config(database):
    folder = Path(database).parent
    try:
        value = json.loads((folder/'yookassa-shop.json').read_text())
        key = (folder/'yookassa.key').read_text().strip()
        if not str(value.get('shop_id', '')).isdigit() or not key:
            raise ValueError()
        return value, key
    except (OSError, ValueError, TypeError):
        raise PaymentError('Оплата временно недоступна.') from None

def receipt_email(value):
    if not isinstance(value, str) or len(value) > 254 or not re.fullmatch(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}', value.strip()):
        raise PaymentError('Укажи корректный email для получения чека.', 400)
    return value.strip()

def confirmation_url(value):
    if not isinstance(value, str) or len(value) > 4096:
        raise PaymentError('Платёжный сервис не прислал ссылку на оплату.')
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or parsed.hostname not in ('yoomoney.ru', 'yookassa.ru') or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise PaymentError('Некорректная ссылка платёжного сервиса.')
    return value

def request(database, method, path, payload=None, idempotency=None):
    settings, key = config(database)
    headers = {'Authorization': 'Basic '+base64.b64encode((str(settings['shop_id'])+':'+key).encode()).decode(),
               'Content-Type': 'application/json', 'User-Agent': 'CrosshairKVA-payments'}
    if idempotency:
        headers['Idempotence-Key'] = idempotency
    body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = urllib.request.Request(API+path, data=body, headers=headers, method=method)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect(),ProviderHTTPSHandler()).open(req, timeout=12) as response:
            raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            raise ValueError()
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (OSError, ValueError):
        raise PaymentError('ЮKassa временно недоступна. Повтори попытку чуть позже.') from None

def migrate(db):
    db.execute('''CREATE TABLE IF NOT EXISTS yookassa_payments (
        order_code TEXT PRIMARY KEY, provider_id TEXT UNIQUE,
        confirmation_url TEXT, email TEXT NOT NULL, created REAL NOT NULL,
        last_check REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'creating')''')

def ready(database):
    try:
        settings, key = config(database)
        marker = json.loads((Path(database).parent/'yookassa-ready.json').read_text())
        return settings.get('enabled') is True and marker['shop_id'] == str(settings['shop_id']) and marker['key_hash'] == hashlib.sha256(key.encode()).hexdigest() and time.time()-marker['checked'] < 300
    except (OSError, ValueError, KeyError, TypeError):
        return False

def create_checkout(database, user, plan, email):
    import subscriptions
    if not isinstance(plan,str) or plan not in subscriptions.PLANS:
        raise PaymentError('Неизвестный тариф.', 400)
    email = receipt_email(email)
    settings, _ = config(database)
    if not ready(database):
        raise PaymentError('Оплата временно недоступна.')
    with creation_lock:
        with closing(sqlite3.connect(database)) as db:
            row = db.execute('''SELECT p.order_code,p.provider_id,p.confirmation_url,p.email,p.created
                FROM yookassa_payments p JOIN subscription_orders o ON o.code=p.order_code
                WHERE o.user_id=? AND o.plan=? AND o.activated IS NULL
                AND p.status IN ('creating','pending') AND p.created>? ORDER BY p.created DESC LIMIT 1''',
                (user['id'], plan, time.time()-23*3600)).fetchone()
        if row and row[1] and row[2]:
            return dict(code=row[0], amount=subscriptions.PLANS[plan][0], period=subscriptions.PLANS[plan][2],
                        url=confirmation_url(row[2]), provider='yookassa', activation='automatic')
        if row:
            code, email = row[0], row[3]
        else:
            code = subscriptions.create_order_record(database, user, plan, reuse=False)['code']
            with closing(sqlite3.connect(database)) as db, db:
                db.execute('INSERT INTO yookassa_payments(order_code,email,created) VALUES (?,?,?)', (code,email,time.time()))
        amount = subscriptions.PLANS[plan][0]
        payload = dict(amount=dict(value=f'{amount:.2f}',currency='RUB'), capture=True,
            confirmation=dict(type='redirect',return_url=ORIGIN+'/payment-result.html'),
            description=f'KVA PRO — {subscriptions.PLANS[plan][2]}',
            metadata=dict(order_code=code,user_id=str(user['id']),plan=plan))
        if settings.get('fiscalization_enabled'):
            if type(settings.get('vat_code')) is not int or not 1 <= settings['vat_code'] <= 12:
                raise PaymentError('Настройка чеков пока не завершена.')
            payload['receipt'] = dict(customer=dict(email=email), items=[dict(
                description=payload['description'],quantity='1.00',amount=payload['amount'],
                vat_code=settings['vat_code'],payment_mode='full_payment',payment_subject='service')])
        payment = request(database,'POST','/payments',payload,code)
        ident = payment.get('id')
        if not isinstance(ident,str) or not PAYMENT_ID.fullmatch(ident):
            raise PaymentError('Некорректный номер платежа.')
        if payment.get('test') is not settings.get('test',False):
            raise PaymentError('Режим платёжного сервиса не совпадает с настройками магазина.')
        url = confirmation_url(payment.get('confirmation',{}).get('confirmation_url'))
        if payment.get('status') != 'pending':
            raise PaymentError('Не удалось открыть страницу оплаты.')
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("UPDATE yookassa_payments SET provider_id=?,confirmation_url=?,status='pending' WHERE order_code=?", (ident,url,code))
        return dict(code=code,amount=amount,period=subscriptions.PLANS[plan][2],url=url,provider='yookassa',activation='automatic')

def check_payment(database, ident, force=False):
    import subscriptions
    if not isinstance(ident,str) or not PAYMENT_ID.fullmatch(ident):
        return False
    with closing(sqlite3.connect(database)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('''SELECT p.order_code,p.last_check,p.status,o.user_id,o.amount,o.plan
            FROM yookassa_payments p JOIN subscription_orders o ON o.code=p.order_code WHERE p.provider_id=?''', (ident,)).fetchone()
        if not row or row[2] not in ('pending','creating') or (not force and time.time()-row[1]<10):
            return False
        db.execute('UPDATE yookassa_payments SET last_check=? WHERE provider_id=?',(time.time(),ident))
    payment = request(database,'GET','/payments/'+ident)
    settings,_ = config(database)
    if payment.get('id') != ident or payment.get('test') is not settings.get('test',False) or str(payment.get('recipient',{}).get('account_id')) != str(settings['shop_id']):
        return False
    metadata = payment.get('metadata',{})
    if not isinstance(metadata,dict) or metadata.get('order_code') != row[0] or metadata.get('user_id') != str(row[3]) or metadata.get('plan') != row[5]:
        return False
    amount = payment.get('amount',{})
    try:
        valid = isinstance(amount,dict) and amount.get('currency')=='RUB' and Decimal(str(amount.get('value'))) == Decimal(row[4])
    except InvalidOperation:
        valid = False
    if not valid:
        return False
    status = payment.get('status')
    if status == 'succeeded' and payment.get('paid') is True:
        subscriptions.activate(database,row[0],'yookassa:'+ident,row[4])
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("UPDATE yookassa_payments SET status='succeeded' WHERE provider_id=?",(ident,))
        return True
    if status == 'canceled':
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("UPDATE yookassa_payments SET status='canceled' WHERE provider_id=?",(ident,))
    return False

def refresh_user(database, user_id):
    with closing(sqlite3.connect(database)) as db:
        ids=[row[0] for row in db.execute('''SELECT p.provider_id FROM yookassa_payments p
            JOIN subscription_orders o ON o.code=p.order_code WHERE o.user_id=? AND p.status='pending'
            ORDER BY p.created DESC LIMIT 2''',(user_id,))]
    for ident in ids:
        try:check_payment(database,ident)
        except PaymentError:pass

def start_worker(database):
    stop = threading.Event()
    def work():
        next_health = 0
        while not stop.is_set():
            try:
                settings,key=config(database)
                if settings.get('enabled') is not True:
                    stop.wait(15)
                    continue
                if time.monotonic() >= next_health:
                    merchant=request(database,'GET','/me')
                    if str(merchant.get('account_id')) != str(settings['shop_id']) or merchant.get('test') is not settings.get('test',False) or merchant.get('status') != 'enabled':
                        raise PaymentError('Магазин недоступен.')
                    marker=dict(shop_id=str(settings['shop_id']),key_hash=hashlib.sha256(key.encode()).hexdigest(),checked=time.time())
                    (Path(database).parent/'yookassa-ready.json').write_text(json.dumps(marker))
                    next_health=time.monotonic()+90
                with closing(sqlite3.connect(database)) as db:
                    ids=[r[0] for r in db.execute("SELECT provider_id FROM yookassa_payments WHERE status='pending' ORDER BY last_check LIMIT 10")]
                for ident in ids:
                    if stop.is_set():break
                    try:check_payment(database,ident)
                    except PaymentError:pass
            except (OSError, ValueError, sqlite3.Error):
                # Do not log credentials, customer email or payment API responses.
                pass
            stop.wait(15)
    threading.Thread(target=work,name='yookassa-payments',daemon=True).start()
    return stop
