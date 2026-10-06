import base64
import io
from pathlib import Path
import tempfile
import unittest
from PIL import Image, ImageDraw
from main import DEFAULT
from sharing import pack, validate, install
from gif_support import clean_gif


def example_gif():
    frames = []
    for color in ('red', 'green', 'blue'):
        frame = Image.new('RGBA', (32, 32))
        ImageDraw.Draw(frame).rectangle((8, 8, 23, 23), fill=color)
        frames.append(frame)
    stream = io.BytesIO()
    frames[0].save(stream, format='GIF', save_all=True, append_images=frames[1:], duration=[70, 130, 210], loop=0, disposal=2, transparency=0)
    return stream.getvalue()


class GifTests(unittest.TestCase):
    def test_animation_is_portable_and_cleaned(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / 'source.gif'
            source.write_bytes(example_gif() + b'extra payload')
            package = pack(dict(DEFAULT, style='Изображение', image=str(source)), 'Tester')
            self.assertTrue(package['image_gif'])
            self.assertTrue(package['image_png'])
            raw = base64.b64decode(package['image_gif'])
            self.assertNotIn(b'extra payload', raw)
            restored = install(package, Path(root) / 'installed')
            with Image.open(restored['image']) as image:
                self.assertEqual(image.n_frames, 3)
                self.assertEqual(image.info['loop'], 0)
                colors, durations = [], []
                for i in range(3):
                    image.seek(i)
                    frame = image.convert('RGBA')
                    self.assertEqual(frame.getpixel((0, 0))[3], 0)
                    colors.append(frame.getpixel((16, 16)))
                    durations.append(image.info['duration'])
                self.assertEqual(len(set(colors)), 3)
                self.assertEqual(durations, [70, 130, 210])

    def test_invalid_and_excessive_gifs_rejected(self):
        for raw in (b'not gif', b'x' * 4_500_001):
            with self.assertRaises((ValueError, OSError)):
                clean_gif(raw)
        stream = io.BytesIO()
        Image.new('RGB', (1025, 10)).save(stream, format='GIF')
        with self.assertRaises(ValueError):
            clean_gif(stream.getvalue())
        package = pack(DEFAULT)
        package['settings']['style'] = 'Изображение'
        package['image_gif'] = 'not base64'
        with self.assertRaises(ValueError):
            validate(package)
