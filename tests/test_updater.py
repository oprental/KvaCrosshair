import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature
from updater import ORIGIN, verify_manifest, download_release, version_tuple, ps_quote, cleanup_updates


class UpdatesTest(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.public = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.binary = b'MZ' + b'example executable' * 100
        self.release = dict(version='0.4.5', url=ORIGIN + '/downloads/CrosshairKVA.exe', size=len(self.binary),
                            sha256=hashlib.sha256(self.binary).hexdigest())

    def signed(self, release):
        payload = json.dumps(release, sort_keys=True, separators=(',', ':')).encode()
        return json.dumps(dict(release=release, signature=base64.b64encode(self.key.sign(payload)).decode()))

    def test_signed_metadata_and_tampering(self):
        self.assertEqual(verify_manifest(self.signed(self.release), self.public), self.release)
        envelope = json.loads(self.signed(self.release))
        envelope['release']['sha256'] = '0' * 64
        with self.assertRaises(InvalidSignature):
            verify_manifest(json.dumps(envelope), self.public)
        wrong_host = dict(self.release, url='https://example.com/fake.exe')
        with self.assertRaises(ValueError):
            verify_manifest(self.signed(wrong_host), self.public)

    def test_download_verified_and_bad_data_removed(self):
        class Opener:
            def __init__(self, data): self.data = data
            def open(self, *args, **kwargs): return io.BytesIO(self.data)
        with tempfile.TemporaryDirectory() as folder:
            target = download_release(Opener(self.binary), self.release, Path(folder))
            self.assertEqual(target.read_bytes(), self.binary)
            target.write_bytes(b'corrupted cache')
            for bad in (self.binary[:-1], b'MZ' + b'x' * len(self.binary)):
                with self.assertRaises(ValueError):
                    download_release(Opener(bad), self.release, Path(folder))
                self.assertFalse(list(Path(folder).glob('*.part')))
            self.assertEqual(download_release(Opener(self.binary), self.release, Path(folder)).read_bytes(), self.binary)

    def test_version_and_literal_windows_paths(self):
        self.assertGreater(version_tuple('0.4.10'), version_tuple('0.4.9'))
        with self.assertRaises(ValueError): version_tuple('../evil')
        self.assertEqual(ps_quote("C:/test's/app.exe"), "'C:/test''s/app.exe'")

    def test_successful_install_cleanup_preserves_newer_downloads_and_user_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'updates'
            folder.mkdir()
            current = root / 'app.exe'
            current.write_bytes(self.binary)
            backup = Path(str(current) + '.previous')
            backup.write_bytes(b'backup')
            for name in ('CrosshairKVA-0.5.0.exe', 'CrosshairKVA-0.5.1.exe', 'CrosshairKVA-0.5.1.part', 'install-update.ps1', 'install-helper.log'):
                (folder / name).write_bytes(b'temporary')
            for name in ('CrosshairKVA-0.5.2.exe', 'my-file.png', 'CrosshairKVA-invalid.exe'):
                (folder / name).write_bytes(b'keep')
            (folder / 'installed.json').write_text(json.dumps(dict(version='0.5.1', executable=str(current), sha256=hashlib.sha256(self.binary).hexdigest())))
            cleanup_updates(folder, '0.5.1', current)
            self.assertFalse(backup.exists())
            self.assertEqual(set(p.name for p in folder.iterdir()), {'CrosshairKVA-0.5.2.exe', 'my-file.png', 'CrosshairKVA-invalid.exe'})
            self.assertEqual(current.read_bytes(), self.binary)

    def test_failed_install_preserves_rollback_and_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            current = folder / 'CrosshairKVA-0.5.1.exe'
            current.write_bytes(self.binary)
            backup = Path(str(current) + '.previous')
            backup.write_bytes(b'backup')
            (folder / 'install-error.txt').write_text('Failure')
            (folder / 'installed.json').write_text(json.dumps(dict(version='0.5.1', executable=str(current), sha256='0' * 64)))
            cleanup_updates(folder, '0.5.1', current)
            self.assertTrue(current.exists())
            self.assertTrue(backup.exists())
            self.assertTrue((folder / 'install-error.txt').exists())

    def test_legacy_installer_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'updates'
            folder.mkdir()
            current = root / "user's app.exe"
            current.write_bytes(self.binary)
            backup = Path(str(current) + '.previous')
            backup.write_bytes(b'backup')
            script = '\n'.join('$' + name + ' = ' + ps_quote(value) for name, value in (
                ('updateFile', folder / 'CrosshairKVA-0.5.1.exe'), ('appFile', current), ('expectedHash', hashlib.sha256(self.binary).hexdigest())))
            (folder / 'install-update.ps1').write_text(script, encoding='utf-8-sig')
            cleanup_updates(folder, '0.5.1', current)
            self.assertFalse(backup.exists())
            self.assertFalse((folder / 'install-update.ps1').exists())


if __name__ == '__main__':
    unittest.main()
