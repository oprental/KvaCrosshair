from contextlib import closing
import json,sqlite3,tempfile,threading,time,unittest,uuid
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request,build_opener,ProxyHandler
from urllib.error import HTTPError
from accounts import authenticate
from catalog_server import create_server
import bundle,subscriptions

class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name);self.database=str(self.folder/'catalog.db')
        self.server=create_server(port=0,database=self.database);threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.key='isolated-bundle-test-key-'+'x'*40
        (self.folder/'bundle.key').write_text(self.key);(self.folder/'bundle.json').write_text(json.dumps(dict(peer_url='https://api.kwyjibo.ru:8443')))
        self.a=authenticate(self.database,'register',dict(username='BundleA',password='bundle-test-password'))
        self.b=authenticate(self.database,'register',dict(username='BundleB',password='bundle-test-password'))
    def tearDown(self):self.server.shutdown();self.server.server_close();self.temp.cleanup()
    def http(self,path,body,token='',key=''):
        r=Request('http://127.0.0.1:'+str(self.server.server_port)+path,data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+token,'x-bundle-key':key,'Content-Type':'application/json'})
        with build_opener(ProxyHandler({})).open(r) as response:return json.load(response)
    def test_code_ownership_one_use_and_conflicts(self):
        with self.assertRaises(HTTPError) as error:self.http('/api/bundle/code',{})
        self.assertEqual(error.exception.code,401)
        code=self.http('/api/bundle/code',{},self.a['token'])['code'];vpn=str(uuid.uuid4())
        with self.assertRaises(HTTPError) as error:self.http('/api/bundle/internal/redeem',dict(code=code,vpn_id=vpn,paid_until=time.time()+86400))
        self.assertEqual(error.exception.code,401)
        result=self.http('/api/bundle/internal/redeem',dict(code=code,vpn_id=vpn),key=self.key)
        self.assertEqual(result['kva_id'],self.a['user']['id']);self.assertEqual(result['premium_until'],0)
        with self.assertRaises(HTTPError) as error:self.http('/api/bundle/internal/redeem',dict(code=code),key=self.key)
        self.assertEqual(error.exception.code,400)
        bcode=bundle.create_code(self.database,self.b['user'])['code']
        with self.assertRaises(Exception):bundle.redeem(self.database,dict(code=bcode,vpn_id=vpn))
        fresh=bundle.redeem(self.database,dict(code=bcode,vpn_id=str(uuid.uuid4())))
        self.assertEqual(fresh['kva_id'],self.b['user']['id'])
    def test_payment_auto_provisions_and_sync_retries_without_extending(self):
        order=subscriptions.create_order_record(self.database,self.a['user'],'quarter')
        paid=subscriptions.activate(self.database,order['code'],'verified-bundle-test',250)
        again=subscriptions.activate(self.database,order['code'],'verified-bundle-test',250)
        self.assertEqual(paid['premium_until'],again['premium_until'])
        with patch('bundle.send',side_effect=OSError('unavailable')):bundle.sync_once(self.database)
        with closing(sqlite3.connect(self.database)) as db,db:
            self.assertEqual(db.execute('SELECT count(*) FROM bundle_outbox').fetchone()[0],1)
            db.execute('UPDATE bundle_outbox SET last_try=0')
        packets=[]
        with patch('bundle.send',side_effect=lambda db,p:packets.append(p) or {'success':True}):bundle.sync_once(self.database)
        self.assertEqual(packets[0]['premium_until'],paid['premium_until'])
        with closing(sqlite3.connect(self.database)) as db:self.assertEqual(db.execute('SELECT count(*) FROM bundle_outbox').fetchone()[0],0)
    def test_transfer_managed_account_then_prevent_other_relink(self):
        code=bundle.create_code(self.database,self.a['user'])['code']
        automatic=bundle.redeem(self.database,dict(code=code))
        vpn=str(uuid.uuid4());code=bundle.create_code(self.database,self.a['user'])['code']
        linked=bundle.redeem(self.database,dict(code=code,vpn_id=vpn,paid_until=time.time()+86400))
        self.assertEqual(linked['retired_vpn_id'],automatic['vpn_id']);self.assertEqual(linked['vpn_id'],vpn)
        self.assertGreater(linked['premium_until'],time.time())
        code=bundle.create_code(self.database,self.a['user'])['code']
        with self.assertRaises(Exception):bundle.redeem(self.database,dict(code=code,vpn_id=str(uuid.uuid4())))
    def test_code_expiry_and_rotation(self):
        old=bundle.create_code(self.database,self.a['user'])['code'];new=bundle.create_code(self.database,self.a['user'])['code']
        with self.assertRaises(Exception):bundle.redeem(self.database,dict(code=old))
        with closing(sqlite3.connect(self.database)) as db,db:db.execute('UPDATE bundle_codes SET expires=0')
        with self.assertRaises(Exception):bundle.redeem(self.database,dict(code=new))

    def test_optional_link_preserves_paid_and_pending_orders(self):
        for paid_first in (True,False):
            vpn=str(uuid.uuid4())
            bundle.ensure_billing(self.database,dict(vpn_id=vpn))
            bundle.ensure_billing(self.database,dict(vpn_id=vpn))
            hidden=bundle.linked_user(self.database,vpn)
            order=subscriptions.create_order_record(self.database,hidden,'month')
            payment='optional-'+str(paid_first)
            if paid_first:paid=subscriptions.activate(self.database,order['code'],payment,100)
            actual=self.a['user'] if paid_first else self.b['user']
            linked=bundle.redeem(self.database,dict(vpn_id=vpn,code=bundle.create_code(self.database,actual)['code']))
            self.assertFalse(linked['shadow'])
            result=subscriptions.activate(self.database,order['code'],payment,100)
            self.assertEqual(result['id'],actual['id'])
            self.assertGreater(result['premium_until'],time.time())
            if paid_first:self.assertEqual(result['premium_until'],paid['premium_until'])
            again=subscriptions.activate(self.database,order['code'],payment,100)
            self.assertEqual(again['premium_until'],result['premium_until'])
            with closing(sqlite3.connect(self.database)) as db:
                self.assertEqual(db.execute('SELECT user_id FROM subscription_orders WHERE code=?',(order['code'],)).fetchone()[0],hidden['id'])
            with patch('yookassa_payments.refresh_user') as refresh:
                bundle.internal(self.database,'status',dict(vpn_id=vpn))
                self.assertEqual({c.args[1] for c in refresh.call_args_list},{actual['id'],hidden['id']})

    def test_checkout_creates_hidden_billing_without_registration(self):
        vpn=str(uuid.uuid4())
        with patch('yookassa_payments.ready',return_value=True),patch('subscriptions.create_order',side_effect=lambda database,user,plan,email:dict(owner=user['id'],plan=plan)):
            first=bundle.internal(self.database,'order',dict(vpn_id=vpn,plan='month',email='vpn@example.com'))
            again=bundle.internal(self.database,'order',dict(vpn_id=vpn,plan='month',email='vpn@example.com'))
        self.assertEqual(first,again)
        with closing(sqlite3.connect(self.database)) as db:
            row=bundle.record(db,first['owner'])
            self.assertTrue(row['shadow']);self.assertEqual(row['premium_until'],0)

    def test_renewal_preserves_vpn_time_without_granting_pro_for_trial(self):
        code=bundle.create_code(self.database,self.a['user'])['code'];baseline=time.time()+45*86400
        linked=bundle.redeem(self.database,dict(code=code,vpn_id=str(uuid.uuid4()),vpn_until=baseline,paid_until=0))
        self.assertEqual(linked['premium_until'],0)
        order=subscriptions.create_order_record(self.database,self.a['user'],'month')
        paid=subscriptions.activate(self.database,order['code'],'verified-bundle-renewal',100)
        self.assertAlmostEqual(paid['premium_until'],baseline+30*86400,delta=2)
