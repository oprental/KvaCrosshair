import base64
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
from urllib.error import HTTPError
from PIL import Image
from main import DEFAULT
from sharing import pack, install, validate
from catalog_server import create_server


class SharingTests(unittest.TestCase):
    def test_custom_image_is_portable(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'original.png'
            Image.new('RGBA', (600, 200), (180, 100, 255, 128)).save(source)
            package = pack(dict(DEFAULT, style='Изображение', image=str(source)), 'Автор')
            source.unlink()
            installed = install(json.loads(json.dumps(package)), Path(folder) / 'other-user')
            self.assertNotEqual(installed['image'], str(source))
            with Image.open(installed['image']) as image:
                self.assertEqual(image.size, (600, 200))
                self.assertEqual(image.mode, 'RGBA')
            self.assertEqual(package['settings']['image'], '')

    def test_untrusted_path_is_discarded(self):
        package = pack(DEFAULT)
        package['settings']['image'] = 'C:/Windows/secret'
        self.assertEqual(validate(package)['settings']['image'], '')

    def test_invalid_settings_and_images_rejected(self):
        for key, value in [('size', 99999), ('color', 'invalid'), ('outline', 'yes')]:
            package = pack(DEFAULT)
            package['settings'][key] = value
            with self.assertRaises(ValueError):
                validate(package)
        package = pack(DEFAULT)
        package['settings']['style'] = 'Изображение'
        package['image_png'] = base64.b64encode(b'not an image').decode()
        with self.assertRaises(ValueError):
            validate(package)

    def test_server_publication_download_and_persistence(self):
        with tempfile.TemporaryDirectory() as folder:
            database = str(Path(folder) / 'catalog.db')
            server = create_server(port=0, database=database)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f'http://127.0.0.1:{server.server_port}/api/crosshairs'
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                registration = urllib.request.Request(url.replace('/crosshairs', '/auth/register'), data=json.dumps(dict(username='Tester', password='test-password-123')).encode(), headers={'Content-Type': 'application/json'})
                with opener.open(registration) as response:
                    token = json.load(response)['token']
                artwork = Path(folder) / 'art.png'
                Image.new('RGBA', (1920, 1080), (150, 110, 238, 180)).save(artwork)
                package = pack(dict(DEFAULT, name='Shared crosshair', style='Изображение', image=str(artwork), size=960), 'Tester')
                request = urllib.request.Request(url, data=json.dumps(package).encode(), headers={'Content-Type': 'application/json', 'Authorization': 'Bearer '+token})
                with opener.open(request) as response:
                    self.assertEqual(response.status, 201)
                with opener.open(url) as response:
                    downloaded = json.load(response)['items']
                self.assertEqual([validate(p) for p in downloaded], [package])
                with self.assertRaises(HTTPError) as error:
                    opener.open(request)
                self.assertEqual(error.exception.code, 429)
                broken = urllib.request.Request(url, data=b'{}', headers={'Authorization': 'Bearer '+token})
                with self.assertRaises(HTTPError) as error:
                    opener.open(broken)
                self.assertEqual(error.exception.code, 400)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
            server = create_server(port=0, database=database)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with opener.open(f'http://127.0.0.1:{server.server_port}/api/crosshairs') as response:
                    self.assertEqual(json.load(response)['items'][0]['settings']['name'], 'Shared crosshair')
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == '__main__':
    unittest.main()
