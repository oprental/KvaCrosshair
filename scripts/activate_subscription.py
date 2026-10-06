"""Run on the VPS only after independently verifying the payment in YooKassa."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from subscriptions import activate

parser = argparse.ArgumentParser()
parser.add_argument('code')
parser.add_argument('--payment-id', required=True)
parser.add_argument('--amount', type=int, required=True)
parser.add_argument('--database', default='/var/lib/crosshair-kva/catalog.sqlite3')
args = parser.parse_args()
user = activate(args.database, args.code, args.payment_id, args.amount)
print('Activated user', user['id'], 'until', user['premium_until'])
