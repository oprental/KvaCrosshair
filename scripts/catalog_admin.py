"""Manage catalogue records on the VPS without exposing admin HTTP routes."""
import argparse
from contextlib import closing
import json
import sqlite3
import time

parser = argparse.ArgumentParser()
parser.add_argument('--database', default='/var/lib/crosshair-kva/catalog.sqlite3')
parser.add_argument('action', choices=['list', 'delete', 'seed', 'backup', 'delete-user'])
parser.add_argument('value', nargs='?')
args = parser.parse_args()
with closing(sqlite3.connect(args.database)) as db, db:
    if args.action == 'list':
        for ident, raw in db.execute('SELECT id,package FROM crosshairs ORDER BY id DESC LIMIT 100'):
            package = json.loads(raw)
            print(ident, package['settings']['name'], '/', package['author'])
    elif args.action == 'delete':
        if not args.value:
            parser.error('delete requires an id')
        cursor = db.execute('DELETE FROM crosshairs WHERE id=?', (int(args.value),))
        print('Deleted:', cursor.rowcount)
    elif args.action == 'seed':
        if not args.value:
            parser.error('seed requires a JSON file')
        if db.execute('SELECT count(*) FROM crosshairs').fetchone()[0] == 0:
            with open(args.value, encoding='utf-8') as stream:
                packages = json.load(stream)
            db.executemany('INSERT INTO crosshairs(package,created) VALUES (?,?)', [(json.dumps(p,ensure_ascii=False), time.time()) for p in packages])
            print('Seeded:', len(packages))
        else:
            print('Existing catalogue preserved')
    elif args.action == 'backup':
        if not args.value:
            parser.error('backup requires a destination file')
        with closing(sqlite3.connect(args.value)) as target:
            db.backup(target)
        print('Backup created')
    elif args.action == 'delete-user':
        if not args.value:
            parser.error('delete-user requires a username')
        row = db.execute('SELECT id FROM users WHERE username_key=?', (args.value.casefold(),)).fetchone()
        if row:
            db.execute('DELETE FROM sessions WHERE user_id=?', (row[0],))
            db.execute('DELETE FROM crosshairs WHERE owner_id=?', (row[0],))
            db.execute('DELETE FROM users WHERE id=?', (row[0],))
            print('User and their publications deleted')
        else:
            print('User not found')
