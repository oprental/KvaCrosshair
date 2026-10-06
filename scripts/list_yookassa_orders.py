"""Private operator report for issuing NPD receipts; never expose via HTTP."""
import argparse
from contextlib import closing
import json
import sqlite3

parser=argparse.ArgumentParser()
parser.add_argument('--database',default='/var/lib/crosshair-kva/catalog.sqlite3')
args=parser.parse_args()
with closing(sqlite3.connect(args.database)) as db:
    for row in db.execute('''SELECT o.code,u.username,o.amount,o.plan,p.email,p.provider_id,o.activated
        FROM subscription_orders o JOIN yookassa_payments p ON p.order_code=o.code
        JOIN users u ON u.id=o.user_id WHERE o.activated IS NOT NULL ORDER BY o.activated DESC LIMIT 100'''):
        print(json.dumps(dict(zip(('order','username','amount','plan','receipt_email','payment_id','paid_at'),row)),ensure_ascii=False))
