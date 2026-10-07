"""Shared paid entitlement with Kwyjibo; one-time account linking and retryable sync."""
from contextlib import closing
import hashlib,hmac,json,secrets,sqlite3,threading,time,uuid
from pathlib import Path
import urllib.request
from accounts import AccountError

def configured(database):return (Path(database).parent/'bundle.json').exists()
def settings(database):
    folder=Path(database).parent
    try:
        data=json.loads((folder/'bundle.json').read_text())
        key=(folder/'bundle.key').read_text().strip()
        if data.get('peer_url')!='https://api.kwyjibo.ru:8443' or len(key)<40:raise ValueError()
        return data,key
    except (ValueError,OSError):raise AccountError(503,'Связь с VPN временно недоступна.') from None

def trusted(database,key):
    try:return isinstance(key,str) and hmac.compare_digest(settings(database)[1],key)
    except AccountError:return False

def migrate(db):
    db.execute('CREATE TABLE IF NOT EXISTS bundle_shadow(user_id INTEGER PRIMARY KEY)')
    db.execute('CREATE TABLE IF NOT EXISTS bundle_aliases(source_id INTEGER PRIMARY KEY,target_id INTEGER NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS bundle_links(user_id INTEGER PRIMARY KEY,vpn_id TEXT UNIQUE NOT NULL,managed INTEGER NOT NULL DEFAULT 1,revision INTEGER NOT NULL DEFAULT 1,vpn_until REAL NOT NULL DEFAULT 0)')
    if 'vpn_until' not in {r[1] for r in db.execute('PRAGMA table_info(bundle_links)')}:db.execute('ALTER TABLE bundle_links ADD COLUMN vpn_until REAL NOT NULL DEFAULT 0')
    db.execute('CREATE TABLE IF NOT EXISTS bundle_codes(hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL,expires REAL NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS bundle_outbox(user_id INTEGER PRIMARY KEY,last_try REAL NOT NULL DEFAULT 0)')

def schedule(db,user_id):
    row=db.execute('SELECT vpn_id FROM bundle_links WHERE user_id=?',(user_id,)).fetchone()
    if not row:
        db.execute('INSERT INTO bundle_links(user_id,vpn_id) VALUES (?,?)',(user_id,str(uuid.uuid4())))
    db.execute('INSERT OR REPLACE INTO bundle_outbox(user_id,last_try) VALUES (?,0)',(user_id,))

def create_code(database,user):
    if not configured(database):raise AccountError(503,'Общая подписка пока не подключена.')
    code=secrets.token_hex(12)
    with closing(sqlite3.connect(database)) as db,db:
        db.execute('DELETE FROM bundle_codes WHERE expires<? OR user_id=?',(time.time(),user['id']))
        db.execute('INSERT INTO bundle_codes VALUES (?,?,?)',(hashlib.sha256(code.encode()).hexdigest(),user['id'],time.time()+600))
    return dict(code=code,expires_in=600,bot_url='https://t.me/Kwyjibovpnbot?start=kva_'+code)

def redeem(database,payload):
    code=payload.get('code')
    if not isinstance(code,str) or len(code)!=24 or any(c not in '0123456789abcdef' for c in code):raise AccountError(400,'Некорректный код KVA.')
    requested=payload.get('vpn_id')
    if requested is not None:
        try:
            if str(uuid.UUID(requested))!=requested:raise ValueError()
        except (ValueError,TypeError,AttributeError):raise AccountError(400,'Некорректный VPN-аккаунт.')
    with closing(sqlite3.connect(database)) as db,db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT user_id FROM bundle_codes WHERE hash=? AND expires>?',(hashlib.sha256(code.encode()).hexdigest(),time.time())).fetchone()
        if not row:raise AccountError(400,'Код истёк или уже использован. Получи новый код в KVA.')
        uid=row[0]
        link=db.execute('SELECT vpn_id,managed,revision FROM bundle_links WHERE user_id=?',(uid,)).fetchone()
        old=None
        if requested:
            occupied=db.execute('SELECT user_id FROM bundle_links WHERE vpn_id=?',(requested,)).fetchone()
            if occupied and occupied[0]!=uid:
                source=occupied[0]
                if not db.execute('SELECT 1 FROM bundle_shadow WHERE user_id=?',(source,)).fetchone():raise AccountError(409,'VPN-аккаунт уже связан с другим аккаунтом KVA.')
                paid=db.execute('SELECT premium_until FROM users WHERE id=?',(source,)).fetchone()[0]
                db.execute('UPDATE users SET premium_until=max(premium_until,?) WHERE id=?',(paid,uid))
                db.execute('UPDATE users SET premium_until=0 WHERE id=?',(source,))
                db.execute('INSERT INTO bundle_aliases(source_id,target_id) VALUES (?,?)',(source,uid))
                db.execute('DELETE FROM bundle_outbox WHERE user_id=?',(source,))
                db.execute('DELETE FROM bundle_links WHERE user_id=?',(source,))
            if link and link[0]!=requested and not link[1]:raise AccountError(409,'KVA уже связан с другим VPN-аккаунтом. Обратись в поддержку.')
            if link and link[0]!=requested:old=link[0]
            db.execute('INSERT INTO bundle_links(user_id,vpn_id,managed,revision) VALUES (?,?,0,1) ON CONFLICT(user_id) DO UPDATE SET vpn_id=excluded.vpn_id,managed=0,revision=bundle_links.revision+1',(uid,requested))
        elif not link:
            db.execute('INSERT INTO bundle_links(user_id,vpn_id) VALUES (?,?)',(uid,str(uuid.uuid4())))
        eligible=payload.get('paid_until',0)
        baseline=payload.get('vpn_until',0)
        if isinstance(baseline,(float,int)) and not isinstance(baseline,bool) and 0<baseline<time.time()+10*366*86400:
            db.execute('UPDATE bundle_links SET vpn_until=max(vpn_until,?) WHERE user_id=?',(baseline,uid))
        if isinstance(eligible,(float,int)) and not isinstance(eligible,bool) and 0<eligible<time.time()+10*366*86400:
            db.execute('UPDATE users SET premium_until=max(premium_until,?) WHERE id=?',(eligible,uid))
        db.execute('DELETE FROM bundle_codes WHERE user_id=?',(uid,))
        schedule(db,uid)
        result=record(db,uid)
        result['retired_vpn_id']=old
        return result

def record(db,uid):
    row=db.execute('SELECT u.username,u.premium_until,l.vpn_id,l.managed,l.revision FROM users u JOIN bundle_links l ON l.user_id=u.id WHERE u.id=?',(uid,)).fetchone()
    if not row:raise AccountError(409,'Сначала свяжи аккаунт KVA PRO.')
    return dict(kva_id=uid,username=row[0],premium_until=row[1],vpn_id=row[2],managed=bool(row[3]),revision=row[4],shadow=bool(db.execute('SELECT 1 FROM bundle_shadow WHERE user_id=?',(uid,)).fetchone()))

def ensure_billing(database,payload):
    import accounts
    ident=payload.get('vpn_id')
    try:
        if str(uuid.UUID(ident))!=ident:raise ValueError()
    except (ValueError,TypeError,AttributeError):raise AccountError(400,'Некорректный VPN-аккаунт.')
    with closing(sqlite3.connect(database)) as db,db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM bundle_links WHERE vpn_id=?',(ident,)).fetchone():return
        name='VPN_'+secrets.token_hex(10)
        while db.execute('SELECT 1 FROM users WHERE username_key=?',(name.casefold(),)).fetchone():name='VPN_'+secrets.token_hex(10)
        salt=secrets.token_hex(16)
        hashed=accounts.password_hash(secrets.token_urlsafe(40),salt)
        uid=db.execute('INSERT INTO users(username,username_key,salt,password_hash,created) VALUES (?,?,?,?,?)',(name,name.casefold(),salt,hashed,time.time())).lastrowid
        baseline=payload.get('vpn_until',0)
        if not isinstance(baseline,(int,float)) or isinstance(baseline,bool) or not 0<=baseline<time.time()+10*366*86400:baseline=0
        db.execute('INSERT INTO bundle_shadow VALUES (?)',(uid,))
        db.execute('INSERT INTO bundle_links(user_id,vpn_id,managed,vpn_until) VALUES (?,?,0,?)',(uid,ident,baseline))

def linked_user(database,vpn_id):
    with closing(sqlite3.connect(database)) as db:
        row=db.execute('SELECT u.id,u.username FROM users u JOIN bundle_links l ON l.user_id=u.id WHERE l.vpn_id=?',(vpn_id,)).fetchone()
    if not row:raise AccountError(409,'Сначала свяжи аккаунт KVA PRO через одноразовый код.')
    return dict(id=row[0],username=row[1])

def internal(database,action,payload):
    if action=='redeem':return redeem(database,payload)
    if action=='order':
        import subscriptions,yookassa_payments
        if payload.get('plan') not in subscriptions.PLANS:raise AccountError(400,'Неизвестный тариф.')
        yookassa_payments.receipt_email(payload.get('email'))
        if not yookassa_payments.ready(database):raise AccountError(503,'Оплата временно недоступна.')
        ensure_billing(database,payload)
    user=linked_user(database,payload.get('vpn_id'))
    if action=='legacy':
        deadline=payload.get('paid_until')
        if not isinstance(deadline,(int,float)) or isinstance(deadline,bool) or not time.time()<deadline<time.time()+10*366*86400:raise AccountError(400,'Некорректный срок подписки.')
        with closing(sqlite3.connect(database)) as db,db:
            db.execute('UPDATE users SET premium_until=max(premium_until,?) WHERE id=?',(deadline,user['id']))
            schedule(db,user['id'])
            return record(db,user['id'])
    if action=='order':
        import subscriptions
        return subscriptions.create_order(database,user,payload.get('plan'),payload.get('email'))
    if action=='status':
        import yookassa_payments
        yookassa_payments.refresh_user(database,user['id'])
        with closing(sqlite3.connect(database)) as db:
            sources=db.execute('SELECT source_id FROM bundle_aliases WHERE target_id=?',(user['id'],)).fetchall()
        for source in sources:yookassa_payments.refresh_user(database,source[0])
        with closing(sqlite3.connect(database)) as db:
            result=record(db,user['id'])
            code=payload.get('code')
            if code:
                row=db.execute('SELECT p.status FROM subscription_orders o JOIN yookassa_payments p ON p.order_code=o.code WHERE (o.user_id=? OR o.user_id IN (SELECT source_id FROM bundle_aliases WHERE target_id=?)) AND o.code=?',(user['id'],user['id'],code)).fetchone()
                if not row:raise AccountError(404,'Заказ не найден.')
                result['status']=row[0]
            return result
    raise AccountError(404,'Неизвестная операция.')

def send(database,payload):
    from yookassa_payments import NoRedirect,ProviderHTTPSHandler
    config,key=settings(database)
    req=urllib.request.Request(config['peer_url']+'/api/bundle/internal/sync',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','x-bundle-key':key},method='POST')
    with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect(),ProviderHTTPSHandler()).open(req,timeout=12) as response:
        data=response.read(65537)
    if len(data)>65536:raise ValueError()
    result=json.loads(data)
    if result.get('success') is not True:raise ValueError()
    return result

def sync_once(database):
    with closing(sqlite3.connect(database)) as db,db:
        rows=db.execute('SELECT user_id FROM bundle_outbox WHERE last_try<? ORDER BY last_try LIMIT 5',(time.time()-15,)).fetchall()
        packets=[record(db,row[0]) for row in rows]
        for p in packets:db.execute('UPDATE bundle_outbox SET last_try=? WHERE user_id=?',(time.time(),p['kva_id']))
    for packet in packets:
        try:
            send(database,packet)
            with closing(sqlite3.connect(database)) as db,db:
                # A concurrent renewal/link must remain queued.
                if record(db,packet['kva_id'])==packet:db.execute('DELETE FROM bundle_outbox WHERE user_id=?',(packet['kva_id'],))
        except (OSError,ValueError,AccountError):pass

def start_worker(database):
    if not configured(database):return None
    with closing(sqlite3.connect(database)) as db,db:
        for row in db.execute('SELECT id FROM users WHERE premium_until>? AND id NOT IN (SELECT user_id FROM bundle_links)',(time.time(),)).fetchall():schedule(db,row[0])
    stop=threading.Event()
    def work():
        while not stop.is_set():
            try:sync_once(database)
            except (OSError,ValueError,sqlite3.Error):pass
            stop.wait(15)
    threading.Thread(target=work,name='bundle-sync',daemon=True).start()
    return stop
