import base64
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
from urllib.error import HTTPError
from PIL import Image, PngImagePlugin
from catalog import SafeRedirectHandler
from catalog_server import create_server
from main import DEFAULT
from sharing import validate, pack


class SecurityTests(unittest.TestCase):
    def test_redirects_cannot_leak_session_or_password(self):
        handler = SafeRedirectHandler()
        for request, target in [
            (urllib.request.Request('https://example.com/api/auth/login', data=b'credentials'), 'https://example.com/new'),
            (urllib.request.Request('https://example.com/api', headers={'Authorization':'Bearer secret'}), 'https://example.com/new'),
            (urllib.request.Request('https://example.com/api'), 'http://example.com/api'),
            (urllib.request.Request('https://example.com/api'), 'https://other.example/api')]:
            with self.assertRaises(HTTPError):
                handler.redirect_request(request,None,302,'redirect',{},target)
        request = urllib.request.Request('http://example.com/api')
        redirected = handler.redirect_request(request,None,302,'redirect',{},'https://example.com:8443/api')
        self.assertEqual(redirected.full_url,'https://example.com:8443/api')

    def test_png_metadata_and_appended_data_removed(self):
        image = Image.new('RGBA',(10,10),'purple')
        info = PngImagePlugin.PngInfo()
        info.add_text('payload','unwanted metadata')
        stream = io.BytesIO()
        image.save(stream,format='PNG',pnginfo=info,icc_profile=b'private profile data')
        package = pack(DEFAULT)
        package['settings']['style'] = 'Изображение'
        package['image_png'] = base64.b64encode(stream.getvalue()+b'appended executable content').decode()
        cleaned = validate(package)
        raw = base64.b64decode(cleaned['image_png'])
        self.assertNotIn(b'appended executable content',raw)
        with Image.open(io.BytesIO(raw)) as decoded:
            self.assertNotIn('payload',decoded.info)
            self.assertNotIn('icc_profile',decoded.info)
        info = PngImagePlugin.PngInfo()
        info.add_text('large','x'*100_000,zip=True)
        stream = io.BytesIO()
        image.save(stream,format='PNG',pnginfo=info)
        package['image_png'] = base64.b64encode(stream.getvalue()).decode()
        with self.assertRaises(ValueError):
            validate(package)

    def test_invalid_text_and_version_rejected(self):
        for name in ('bad\nname','\ud800'):
            package = pack(DEFAULT)
            package['settings']['name'] = name
            with self.assertRaises(ValueError):
                validate(package)
        package = pack(DEFAULT)
        package['version'] = True
        with self.assertRaises(ValueError):
            validate(package)

    def test_malformed_auth_and_injection_do_not_damage_database(self):
        with tempfile.TemporaryDirectory() as folder:
            server = create_server(port=0,database=Path(folder)/'db.sqlite3')
            thread = threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            base = f'http://127.0.0.1:{server.server_port}'
            try:
                for payload in [b'['*1500+b']'*1500, json.dumps(dict(username="' OR 1=1 --",password='test-password-123')).encode()]:
                    with self.assertRaises(HTTPError) as error:
                        opener.open(urllib.request.Request(base+'/api/auth/login',data=payload))
                    self.assertEqual(error.exception.code,400)
                with opener.open(base+'/api/crosshairs') as response:
                    self.assertEqual(json.load(response),{'items':[]})
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == '__main__':
    unittest.main()
