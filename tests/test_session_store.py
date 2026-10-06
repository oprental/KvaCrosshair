from pathlib import Path
import tempfile
import unittest
import sys
import session_store


@unittest.skipUnless(sys.platform == 'win32', 'Windows DPAPI')
class SessionTests(unittest.TestCase):
    def test_encrypted_session_origin_and_clear(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            origin = 'https://kvacrosshair.online'
            token = 'a-secret-session-token-for-tests'
            session_store.save(folder, origin, token)
            self.assertNotIn(token.encode(), (folder / 'session.dat').read_bytes())
            self.assertEqual(session_store.load(folder, origin), token)
            self.assertEqual(session_store.load(folder, 'https://another.example'), '')
            (folder / 'session.dat').write_bytes(b'corrupt')
            self.assertEqual(session_store.load(folder, origin), '')
            session_store.clear(folder)
            self.assertEqual(session_store.load(folder, origin), '')
