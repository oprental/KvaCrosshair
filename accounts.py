"""Account storage: salted password hashes and expiring opaque sessions."""
from contextlib import closing
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
import subscriptions

USERNAME = re.compile(r'[A-Za-z0-9А-Яа-яЁё_]{3,24}')


class AccountError(ValueError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def migrate(database):
    with closing(sqlite3.connect(database)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT NOT NULL, username_key TEXT NOT NULL UNIQUE, salt TEXT NOT NULL, password_hash TEXT NOT NULL, created REAL NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), expires REAL NOT NULL)')
        columns = {row[1] for row in db.execute('PRAGMA table_info(crosshairs)')}
        if 'owner_id' not in columns:
            db.execute('ALTER TABLE crosshairs ADD COLUMN owner_id INTEGER REFERENCES users(id)')
        subscriptions.migrate(db)


def password_hash(password, salt, legacy=False):
    cost = 1 if legacy else 5
    digest = hashlib.scrypt(password.encode('utf-8'), salt=bytes.fromhex(salt), n=16384, r=8, p=cost, dklen=32).hex()
    return digest if legacy else 'scrypt-v2$'+digest


def session(db, user):
    token = secrets.token_urlsafe(32)
    db.execute('DELETE FROM sessions WHERE expires < ?', (time.time(),))
    db.execute('INSERT INTO sessions VALUES (?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), user['id'], time.time()+7*86400))
    # Bound active sessions per user while permitting several devices.
    db.execute('DELETE FROM sessions WHERE user_id=? AND token_hash NOT IN (SELECT token_hash FROM sessions WHERE user_id=? ORDER BY expires DESC LIMIT 10)', (user['id'], user['id']))
    return dict(token=token, user=subscriptions.enrich(db, user))


def authenticate(database, action, payload):
    if not isinstance(payload, dict):
        raise AccountError(400, 'Некорректные данные')
    name, password = payload.get('username'), payload.get('password')
    if not isinstance(name, str) or not USERNAME.fullmatch(name):
        raise AccountError(400, 'Логин: 3–24 буквы, цифры или знак _')
    if not isinstance(password, str) or not 10 <= len(password) <= 128:
        raise AccountError(400, 'Пароль: от 10 до 128 символов')
    key = name.casefold()
    with closing(sqlite3.connect(database)) as db, db:
        if action == 'register':
            salt = secrets.token_hex(16)
            hashed = password_hash(password, salt)
            try:
                cursor = db.execute('INSERT INTO users(username,username_key,salt,password_hash,created) VALUES (?,?,?,?,?)', (name, key, salt, hashed, time.time()))
            except sqlite3.IntegrityError:
                raise AccountError(409, 'Этот логин уже занят') from None
            user = dict(id=cursor.lastrowid, username=name)
        elif action == 'login':
            row = db.execute('SELECT id,username,salt,password_hash FROM users WHERE username_key=?', (key,)).fetchone()
            legacy = bool(row and not row[3].startswith('scrypt-v2$'))
            hashed = password_hash(password, row[2] if row else '00'*16, legacy=legacy)
            if not row or not hmac.compare_digest(hashed, row[3]):
                raise AccountError(401, 'Неверный логин или пароль')
            if legacy:
                db.execute('UPDATE users SET password_hash=? WHERE id=?', (password_hash(password, row[2]), row[0]))
            user = dict(id=row[0], username=row[1])
        else:
            raise AccountError(404, 'Неизвестная операция')
        return session(db, user)


def get_user(database, authorization):
    if not isinstance(authorization, str) or not authorization.startswith('Bearer '):
        return None
    token = authorization[7:]
    if not 20 <= len(token) <= 100:
        return None
    hashed = hashlib.sha256(token.encode()).hexdigest()
    with closing(sqlite3.connect(database)) as db:
        row = db.execute('SELECT users.id,users.username FROM sessions JOIN users ON users.id=sessions.user_id WHERE token_hash=? AND expires>?', (hashed, time.time())).fetchone()
        return subscriptions.enrich(db, dict(id=row[0], username=row[1])) if row else None


def logout(database, authorization):
    if authorization.startswith('Bearer '):
        hashed = hashlib.sha256(authorization[7:].encode()).hexdigest()
        with closing(sqlite3.connect(database)) as db, db:
            db.execute('DELETE FROM sessions WHERE token_hash=?', (hashed,))
