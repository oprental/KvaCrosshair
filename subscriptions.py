"""Server-owned orders, verified DonatePay payments and subscription periods."""
from contextlib import closing
from datetime import datetime, timezone
import calendar
import secrets
import sqlite3
import time
from pathlib import Path
import hashlib
import json
import re
import threading
import urllib.request
from urllib.parse import urlencode
from decimal import Decimal, InvalidOperation

PLANS = {'month': (100, 1, '30 дней'), 'quarter': (250, 3, '3 месяца'),
         'half': (500, 6, '6 месяцев'), 'year': (900, 12, 'Год')}
COLORS = ['#ffca72', '#b693ff', '#ff81bd', '#65f7a5', '#ffffff']
PAY_URL = 'https://donatepay.ru/don/1536419'


def payments_ready(database):
    folder = Path(database).parent
    try:
        ready = json.loads((folder / 'payments-ready.json').read_text())
        key_hash = hashlib.sha256((folder / 'donatepay.key').read_bytes().strip()).hexdigest()
        return ready['key_hash'] == key_hash and time.time() - ready['checked'] < 300
    except (OSError, ValueError, KeyError, TypeError):
        return False


def process_payment(database, transaction):
    if not isinstance(transaction, dict) or transaction.get('type') != 'donation' or transaction.get('status') != 'success' or transaction.get('test'):
        return False
    variables = transaction.get('vars', {})
    if not isinstance(variables, dict):
        return False
    comment = variables.get('comment', transaction.get('comment', ''))
    if not isinstance(comment, str):
        return False
    codes = re.findall(r'(?<![A-Z0-9])KVA-[A-F0-9]{16}(?![A-Z0-9])', comment.upper())
    if len(set(codes)) != 1:
        return False
    currency = transaction.get('currency', variables.get('currency', 'RUB'))
    if str(currency).upper() not in ('RUB', 'RUR'):
        return False
    try:
        amount = Decimal(str(transaction.get('sum', variables.get('sum'))))
        if not amount.is_finite() or amount != amount.to_integral_value():
            return False
        payment_id = transaction.get('id')
        if type(payment_id) not in (int, str):
            return False
        activate(database, codes[0], 'donatepay:' + str(payment_id), int(amount))
        return True
    except (ValueError, InvalidOperation):
        return False


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Payment API redirect rejected')


def start_worker(database):
    folder = Path(database).parent
    if not (folder / 'donatepay.key').exists():
        return None
    stop = threading.Event()
    def work():
        opener = urllib.request.build_opener(NoRedirect())
        while not stop.is_set():
            try:
                key = (folder / 'donatepay.key').read_text().strip()
                url = 'https://donatepay.ru/api/v1/transactions?' + urlencode(dict(access_token=key, type='donation', order='DESC', limit=100))
                with opener.open(url, timeout=20) as response:
                    raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ValueError('Payment response too large')
                result = json.loads(raw)
                if result.get('status') != 'success' or not isinstance(result.get('data'), list):
                    raise ValueError('Payment API unavailable')
                for transaction in result['data']:
                    process_payment(database, transaction)
                ready = dict(key_hash=hashlib.sha256(key.encode()).hexdigest(), checked=time.time())
                (folder / 'payments-ready.json').write_text(json.dumps(ready))
            except Exception:
                # Never log exception URLs: the provider uses a query API key.
                pass
            stop.wait(90)
    threading.Thread(target=work, name='payments', daemon=True).start()
    return stop


def migrate(db):
    columns = {row[1] for row in db.execute('PRAGMA table_info(users)')}
    for name, definition in [('premium_until', 'REAL NOT NULL DEFAULT 0'), ('nick_color', "TEXT NOT NULL DEFAULT '#ffca72'")]:
        if name not in columns:
            db.execute(f'ALTER TABLE users ADD COLUMN {name} {definition}')
    db.execute('CREATE TABLE IF NOT EXISTS subscription_orders (code TEXT PRIMARY KEY, user_id INTEGER NOT NULL, plan TEXT NOT NULL, amount INTEGER NOT NULL, created REAL NOT NULL, activated REAL, payment_id TEXT UNIQUE)')


def enrich(db, user):
    until, color = db.execute('SELECT premium_until,nick_color FROM users WHERE id=?', (user['id'],)).fetchone()
    return dict(user, premium=until > time.time(), premium_until=until, nick_color=color if color in COLORS else COLORS[0])


def create_order(database, user, plan):
    if not isinstance(plan, str) or plan not in PLANS:
        raise ValueError('Неизвестный тариф')
    code = 'KVA-' + secrets.token_hex(8).upper()
    with closing(sqlite3.connect(database)) as db, db:
        pending = db.execute('SELECT code FROM subscription_orders WHERE user_id=? AND plan=? AND activated IS NULL AND created>? ORDER BY created DESC LIMIT 1', (user['id'], plan, time.time() - 86400)).fetchone()
        if pending:
            code = pending[0]
        else:
            db.execute('INSERT INTO subscription_orders(code,user_id,plan,amount,created) VALUES (?,?,?,?,?)', (code, user['id'], plan, PLANS[plan][0], time.time()))
    return dict(code=code, amount=PLANS[plan][0], period=PLANS[plan][2], url=PAY_URL, activation='automatic')


def activate(database, code, payment_id, amount):
    if not isinstance(payment_id, str) or not payment_id.strip() or len(payment_id) > 100:
        raise ValueError('Нужен номер проверенного платежа')
    with closing(sqlite3.connect(database)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        order = db.execute('SELECT user_id,plan,amount,activated,payment_id FROM subscription_orders WHERE code=?', (code,)).fetchone()
        if not order or amount != order[2]:
            raise ValueError('Заказ не найден или сумма не совпадает')
        if order[3] is not None:
            if order[4] == payment_id:
                return enrich(db, {'id': order[0]})
            raise ValueError('Заказ уже активирован')
        if db.execute('SELECT 1 FROM subscription_orders WHERE payment_id=?', (payment_id,)).fetchone():
            raise ValueError('Этот платёж уже использован')
        previous = db.execute('SELECT premium_until FROM users WHERE id=?', (order[0],)).fetchone()[0]
        start = max(time.time(), previous)
        months = PLANS[order[1]][1]
        if months == 1:
            expires = start + 30 * 86400
        else:
            date = datetime.fromtimestamp(start, timezone.utc)
            month = date.year * 12 + date.month - 1 + months
            year, index = divmod(month, 12)
            expires = date.replace(year=year, month=index + 1, day=min(date.day, calendar.monthrange(year, index + 1)[1])).timestamp()
        db.execute('UPDATE users SET premium_until=? WHERE id=?', (expires, order[0]))
        db.execute('UPDATE subscription_orders SET activated=?,payment_id=? WHERE code=?', (time.time(), payment_id, code))
        return enrich(db, {'id': order[0]})
