"""Small shared catalogue server. Run behind HTTPS for internet access."""
import argparse
import json
import re
import sqlite3
import threading
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from sharing import validate, MAX_PACKAGE
from accounts import migrate, authenticate, get_user, logout, AccountError
import subscriptions
import yookassa_payments
import bundle


class BoundedHTTPServer(ThreadingHTTPServer):
    request_queue_size = 32
    daemon_threads = True

    def server_close(self):
        if getattr(self, 'payment_stop', None) is not None:
            self.payment_stop.set()
        if getattr(self,'bundle_stop',None) is not None:
            self.bundle_stop.set()
        super().server_close()

    def __init__(self, *args, **kwargs):
        self.workers = threading.BoundedSemaphore(16)
        super().__init__(*args, **kwargs)

    def process_request(self, request, address):
        if not self.workers.acquire(blocking=False):
            try:
                request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.workers.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.workers.release()


def create_server(host='127.0.0.1', port=8765, database='catalog.sqlite3'):
    database = str(Path(database).resolve())
    with closing(sqlite3.connect(database)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS crosshairs (id INTEGER PRIMARY KEY, package TEXT NOT NULL, created REAL NOT NULL)')
    migrate(database)
    lock = threading.Lock()
    last_post = {}
    auth_attempts = {}
    auth_slots = threading.BoundedSemaphore(2)
    upload_slots = threading.BoundedSemaphore(2)
    read_slots = threading.BoundedSemaphore(2)
    payment_slots = threading.BoundedSemaphore(2)

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, value):
            raw = json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == '/api/subscription/plans':
                self.reply(200, {'plans': [dict(id=key, amount=value[0], period=value[2]) for key,value in subscriptions.PLANS.items()],
                    'enabled': subscriptions.payments_ready(database), 'provider': 'yookassa',
                    'email_required': True})
                return
            if self.path == '/api/auth/me':
                user = get_user(database, self.headers.get('Authorization', ''))
                if user and yookassa_payments.configured(database) and payment_slots.acquire(blocking=False):
                    try:
                        yookassa_payments.refresh_user(database,user['id'])
                        user = get_user(database,self.headers.get('Authorization',''))
                    finally:
                        payment_slots.release()
                self.reply(200 if user else 401, {'user': user} if user else {'error': 'Для публикации войди в аккаунт'})
                return
            if self.path != '/api/crosshairs':
                self.reply(404, {'error': 'not found'})
                return
            if not read_slots.acquire(blocking=False):
                self.reply(503, {'error': 'Сервер занят. Попробуй позже.'})
                return
            try:
                items = []
                total = 0
                with closing(sqlite3.connect(database)) as db:
                    rows = db.execute('SELECT c.package,c.id,c.owner_id,u.premium_until,u.nick_color FROM crosshairs c LEFT JOIN users u ON u.id=c.owner_id ORDER BY c.id DESC LIMIT 100')
                    for row in rows:
                        total += len(row[0].encode('utf-8'))
                        if total > 10_000_000:
                            break
                        premium = bool(row[3] and row[3] > time.time())
                        try:
                            nick_color=subscriptions.normalize_color(row[4]) if premium else None
                        except ValueError:
                            nick_color=subscriptions.DEFAULT_COLOR
                        items.append(dict(json.loads(row[0]), id=row[1], owner_id=row[2], premium=premium,
                                          nick_color=nick_color))
                self.reply(200, {'items': items})
            finally:
                read_slots.release()

        def do_POST(self):
            if self.path=='/api/bundle/code' or self.path.startswith('/api/bundle/internal/'):
                self.handle_bundle()
                return
            if self.path == '/api/payments/yookassa':
                if not yookassa_payments.configured(database):
                    self.reply(404, {'error':'not found'})
                    return
                if not payment_slots.acquire(blocking=False):
                    self.reply(503, {'error':'Проверка оплаты занята.'})
                    return
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if not 0 < length <= 65536:
                        raise ValueError()
                    payload=json.loads(self.rfile.read(length))
                    if not isinstance(payload,dict) or payload.get('type')!='notification' or payload.get('event') not in ('payment.succeeded','payment.canceled') or not isinstance(payload.get('object'),dict):
                        raise ValueError()
                    yookassa_payments.check_payment(database,payload['object'].get('id'))
                    self.reply(200, {'ok':True})
                except yookassa_payments.PaymentError:
                    self.reply(503, {'error':'Проверка оплаты временно недоступна.'})
                except (ValueError,TypeError,OSError):
                    self.reply(400, {'error':'Некорректное уведомление.'})
                finally:
                    payment_slots.release()
                return
            if self.path in ('/api/subscription/order', '/api/subscription/color'):
                self.handle_subscription()
                return
            if self.path in ('/api/auth/register', '/api/auth/login', '/api/auth/logout'):
                self.handle_auth()
                return
            if self.path != '/api/crosshairs':
                self.reply(404, {'error': 'not found'})
                return
            user = get_user(database, self.headers.get('Authorization', ''))
            if not user:
                self.reply(401, {'error': 'Для публикации войди в аккаунт'})
                return
            if not upload_slots.acquire(blocking=False):
                self.reply(503, {'error': 'Сервер занят. Попробуй позже.'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= MAX_PACKAGE:
                    raise ValueError('invalid body size')
                package = validate(json.loads(self.rfile.read(length)))
                package['author'] = user['username']
            except (ValueError, OSError, RecursionError):
                self.reply(400, {'error': 'invalid crosshair package'})
                return
            finally:
                upload_slots.release()
            with lock:
                now = time.monotonic()
                ip = self.client_address[0]
                if ip in ('127.0.0.1', '::1'):
                    ip = self.headers.get('X-Real-IP', ip)
                if now-last_post.get(ip, -100) < 5:
                    self.reply(429, {'error': 'wait 5 seconds between publications'})
                    return
                last_post[ip] = now
                # Expire old entries so rate-limit bookkeeping stays bounded.
                for address in list(last_post):
                    if now-last_post[address] > 60:
                        del last_post[address]
            with closing(sqlite3.connect(database)) as db, db:
                db.execute('BEGIN IMMEDIATE')
                serialized = json.dumps(package, ensure_ascii=False)
                total = db.execute('SELECT COALESCE(SUM(length(CAST(package AS BLOB))),0) FROM crosshairs').fetchone()[0]
                if total+len(serialized.encode('utf-8')) > 100_000_000:
                    self.reply(507, {'error': 'catalogue storage limit reached'})
                    return
                cursor = db.execute('INSERT INTO crosshairs(package,created,owner_id) VALUES (?,?,?)', (serialized, time.time(), user['id']))
                ident = cursor.lastrowid
            self.reply(201, {'id': ident})

        def do_DELETE(self):
            match = re.fullmatch(r'/api/crosshairs/([1-9][0-9]{0,17})', self.path)
            if not match:
                self.reply(404, {'error': 'Публикация не найдена'})
                return
            user = get_user(database, self.headers.get('Authorization', ''))
            if not user:
                self.reply(401, {'error': 'Для удаления войди в аккаунт'})
                return
            with closing(sqlite3.connect(database)) as db, db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT owner_id FROM crosshairs WHERE id=?', (int(match[1]),)).fetchone()
                if row is None:
                    self.reply(404, {'error': 'Публикация уже удалена или не найдена'})
                    return
                if row[0] != user['id']:
                    self.reply(403, {'error': 'Удалять публикацию может только её автор'})
                    return
                db.execute('DELETE FROM crosshairs WHERE id=? AND owner_id=?', (int(match[1]), user['id']))
            self.reply(200, {'ok': True, 'id': int(match[1])})

        def handle_auth(self):
            if self.path == '/api/auth/logout':
                logout(database, self.headers.get('Authorization', ''))
                self.reply(200, {'ok': True})
                return
            ip = self.client_address[0]
            if ip in ('127.0.0.1', '::1'):
                ip = self.headers.get('X-Real-IP', ip)
            with lock:
                now = time.monotonic()
                for address in list(auth_attempts):
                    if now-auth_attempts[address][0] > 600:
                        del auth_attempts[address]
                first, count = auth_attempts.get(ip, (now, 0))
                if count >= 12:
                    self.reply(429, {'error': 'Слишком много попыток. Попробуй через 10 минут.'})
                    return
                auth_attempts[ip] = (first, count+1)
            if not auth_slots.acquire(blocking=False):
                self.reply(503, {'error': 'Сервер занят. Попробуй через несколько секунд.'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096:
                    raise ValueError('invalid body size')
                payload = json.loads(self.rfile.read(length))
                result = authenticate(database, self.path.rsplit('/', 1)[1], payload)
                self.reply(201 if self.path.endswith('/register') else 200, result)
            except AccountError as error:
                self.reply(error.status, {'error': str(error)})
            except (ValueError, OSError, RecursionError):
                self.reply(400, {'error': 'Некорректные данные аккаунта'})
            finally:
                auth_slots.release()

        def handle_bundle(self):
            if not payment_slots.acquire(blocking=False):
                self.reply(503,{'error':'Сервис занят. Повтори позже.'})
                return
            try:
                if self.path=='/api/bundle/code':
                    user=get_user(database,self.headers.get('Authorization',''))
                    if not user:raise AccountError(401,'Войди в аккаунт KVA.')
                elif not bundle.trusted(database,self.headers.get('x-bundle-key','')):
                    raise AccountError(401,'Unauthorized')
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=4096:raise AccountError(400,'Некорректный запрос.')
                payload=json.loads(self.rfile.read(length))
                if not isinstance(payload,dict):raise AccountError(400,'Некорректный запрос.')
                result=bundle.create_code(database,user) if self.path=='/api/bundle/code' else bundle.internal(database,self.path.rsplit('/',1)[-1],payload)
                self.reply(200,result)
            except (AccountError,yookassa_payments.PaymentError) as error:
                self.reply(error.status,{'error':str(error)})
            except (ValueError,TypeError,OSError):
                self.reply(400,{'error':'Некорректные данные общей подписки.'})
            finally:payment_slots.release()

        def handle_subscription(self):
            user = get_user(database, self.headers.get('Authorization', ''))
            if not user:
                self.reply(401, {'error': 'Войди в аккаунт для подписки'})
                return
            if not payment_slots.acquire(blocking=False):
                self.reply(503, {'error':'Сервис оплаты занят. Попробуй позже.'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096:
                    raise ValueError('Некорректный запрос')
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError('Некорректный запрос')
                if self.path.endswith('/order'):
                    if payload.get('provider') != 'yookassa':
                        self.reply(426, {'error':'Обнови приложение: оплата теперь проходит через ЮKassa.'})
                        return
                    if not subscriptions.payments_ready(database):
                        self.reply(503, {'error': 'Оплата временно недоступна. Попробуй позже.'})
                        return
                    self.reply(201, subscriptions.create_order(database, user, payload.get('plan'), payload.get('email')))
                else:
                    if not user['premium']:
                        self.reply(403, {'error': 'Цвет ника доступен с подпиской KVA PRO'})
                        return
                    color = subscriptions.normalize_color(payload.get('color'))
                    with closing(sqlite3.connect(database)) as db, db:
                        db.execute('UPDATE users SET nick_color=? WHERE id=?', (color, user['id']))
                    self.reply(200, {'user': get_user(database, self.headers.get('Authorization', ''))})
            except yookassa_payments.PaymentError as error:
                self.reply(error.status, {'error':str(error)})
            except (ValueError, OSError, TypeError):
                self.reply(400, {'error': 'Некорректные данные подписки'})
            finally:
                payment_slots.release()

        def setup(self):
            super().setup()
            self.connection.settimeout(15)

    server = BoundedHTTPServer((host, port), Handler)
    server.payment_stop = subscriptions.start_worker(database)
    server.bundle_stop = bundle.start_worker(database)
    return server


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--database', default='catalog.sqlite3')
    args = parser.parse_args()
    server = create_server(args.host, args.port, args.database)
    print(f'Catalogue listening on http://{args.host}:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
