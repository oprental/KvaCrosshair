"""Server-owned subscription orders and periods; payments use YooKassa."""
from contextlib import closing
from datetime import datetime, timezone
import calendar
import secrets
import sqlite3
import time
import re

PLANS = {'month': (100, 1, '30 дней'), 'quarter': (250, 3, '3 месяца'),
         'half': (500, 6, '6 месяцев'), 'year': (900, 12, 'Год')}
DEFAULT_COLOR = '#ffca72'


def normalize_color(value):
    if not isinstance(value,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',value):
        raise ValueError('Укажи цвет в формате #RRGGBB')
    return value.lower()


def payments_ready(database):
    import yookassa_payments
    return yookassa_payments.ready(database)


def start_worker(database):
    import yookassa_payments
    if not yookassa_payments.configured(database):
        return None
    return yookassa_payments.start_worker(database)


def migrate(db):
    columns = {row[1] for row in db.execute('PRAGMA table_info(users)')}
    for name, definition in [('premium_until', 'REAL NOT NULL DEFAULT 0'), ('nick_color', "TEXT NOT NULL DEFAULT '#ffca72'")]:
        if name not in columns:
            db.execute(f'ALTER TABLE users ADD COLUMN {name} {definition}')
    db.execute('CREATE TABLE IF NOT EXISTS subscription_orders (code TEXT PRIMARY KEY, user_id INTEGER NOT NULL, plan TEXT NOT NULL, amount INTEGER NOT NULL, created REAL NOT NULL, activated REAL, payment_id TEXT UNIQUE)')
    import yookassa_payments
    yookassa_payments.migrate(db)
    import bundle
    bundle.migrate(db)


def enrich(db, user):
    until, color = db.execute('SELECT premium_until,nick_color FROM users WHERE id=?', (user['id'],)).fetchone()
    try:
        color=normalize_color(color)
    except ValueError:
        color=DEFAULT_COLOR
    return dict(user, premium=until > time.time(), premium_until=until, nick_color=color)


def create_order(database, user, plan, email=None):
    import yookassa_payments
    return yookassa_payments.create_checkout(database,user,plan,email)


def create_order_record(database, user, plan, reuse=True):
    if not isinstance(plan, str) or plan not in PLANS:
        raise ValueError('Неизвестный тариф')
    code = 'KVA-' + secrets.token_hex(8).upper()
    with closing(sqlite3.connect(database)) as db, db:
        pending = db.execute('SELECT code FROM subscription_orders WHERE user_id=? AND plan=? AND activated IS NULL AND created>? ORDER BY created DESC LIMIT 1', (user['id'], plan, time.time() - 86400)).fetchone()
        if pending and reuse:
            code = pending[0]
        else:
            db.execute('INSERT INTO subscription_orders(code,user_id,plan,amount,created) VALUES (?,?,?,?,?)', (code, user['id'], plan, PLANS[plan][0], time.time()))
    return dict(code=code, amount=PLANS[plan][0], period=PLANS[plan][2])


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
        import bundle
        if bundle.configured(database):
            linked=db.execute('SELECT vpn_until FROM bundle_links WHERE user_id=?',(order[0],)).fetchone()
            if linked:previous=max(previous,linked[0])
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
        import bundle
        if bundle.configured(database):
            bundle.schedule(db,order[0])
        return enrich(db, {'id': order[0]})
