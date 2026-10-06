"""Sign release metadata with a private key kept outside the repository."""
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
from cryptography.hazmat.primitives import serialization

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from updater import VERSION, ORIGIN, PUBLIC_KEY

exe = Path('dist/release/CrosshairKVA.exe')
key_path = Path(os.environ['LOCALAPPDATA']) / 'CrosshairKVARelease/signing-key.pem'
key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
assert key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw) == PUBLIC_KEY
release = dict(version=VERSION, url=ORIGIN + '/downloads/CrosshairKVA.exe', size=exe.stat().st_size,
               sha256=hashlib.sha256(exe.read_bytes()).hexdigest())
payload = json.dumps(release, sort_keys=True, separators=(',', ':')).encode('utf-8')
manifest = dict(release=release, signature=base64.b64encode(key.sign(payload)).decode('ascii'))
Path('website/latest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
Path('website/SHA256.txt').write_text(release['sha256'] + '  CrosshairKVA.exe\n', encoding='ascii')
print('Signed release:', VERSION)
