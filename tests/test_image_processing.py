import unittest
from unittest.mock import patch
from types import SimpleNamespace
from PIL import Image, ImageDraw
from image_processing import contour, cutout, already_isolated, clean_mask, neural_lineart


class ProcessingTests(unittest.TestCase):
    def test_neural_preserves_size_aspect_ratio_and_transparency(self):
        import numpy as np
        image = Image.new('RGBA', (340, 600), 'white')
        image.putpixel((0, 0), (255, 255, 255, 0))
        captured = []
        class Session:
            def __init__(self, *args, **kwargs):
                pass
            def get_inputs(self):
                return [SimpleNamespace(name='image')]
            def run(self, _, feed):
                data = feed['image']
                captured.append(data)
                output = np.ones((1, 1, data.shape[2], data.shape[3]), dtype=np.float32)
                output[:, :, 0, 0] = -1
                output[:, :, 100:102, 100:150] = -1
                return [output]
        with patch('image_processing.download_model', return_value='verified.onnx'), patch('onnxruntime.InferenceSession', Session):
            result = neural_lineart(image, '.', color='#ff81bd')
        self.assertEqual(result.size, image.size)
        self.assertEqual(captured[0].shape, (1, 3, 768, 512))
        self.assertEqual(result.getpixel((0, 0))[3], 0)
        self.assertEqual(result.getpixel((110, 100)), (255, 129, 189, 255))
        self.assertEqual(result.getpixel((200, 200))[3], 0)

    def test_padded_opaque_picture_still_needs_segmentation(self):
        image = Image.new('RGBA', (100, 100))
        image.paste(Image.new('RGBA', (80, 80), 'white'), (10, 10))
        self.assertFalse(already_isolated(image))
        image.putpixel((10, 10), (0, 0, 0, 0))
        self.assertFalse(already_isolated(image))
        self.assertTrue(already_isolated(self.character()))

    def test_cleanup_removes_background_haze(self):
        mask = Image.new('L', (100, 100), 40)
        ImageDraw.Draw(mask).ellipse((20, 10, 80, 90), fill=255)
        result = clean_mask(mask)
        self.assertEqual(result.getpixel((0, 0)), 0)
        self.assertEqual(result.getpixel((50, 50)), 255)

    def test_ink_is_thin_and_contains_face(self):
        result = contour(self.character(), 'lineart', width=1)
        alpha = result.getchannel('A')
        self.assertTrue(alpha.crop((36, 31, 49, 49)).getbbox())
        self.assertTrue(alpha.crop((36, 65, 71, 71)).getbbox())
        self.assertEqual(alpha.getpixel((53, 53)), 0)
        self.assertEqual(sum(alpha.getpixel((53, y)) > 0 for y in range(65, 72)), 1)

    def character(self):
        image = Image.new('RGBA', (100, 100))
        draw = ImageDraw.Draw(image)
        draw.ellipse((15, 10, 85, 90), fill='white')
        draw.ellipse((35, 30, 43, 42), fill='black')
        draw.ellipse((57, 30, 65, 42), fill='black')
        draw.line((35, 65, 65, 65), fill='black', width=3)
        return image

    def test_cutout_outline_and_inner_details(self):
        image = self.character()
        self.assertEqual(cutout(image, '.').tobytes(), image.tobytes())
        cut = contour(image, 'cutout')
        self.assertEqual(cut.tobytes(), image.tobytes())
        outlined = contour(image, 'outlined', '#ff81bd', 2)
        self.assertGreater(outlined.getchannel('A').getbbox()[2] - outlined.getchannel('A').getbbox()[0], image.getchannel('A').getbbox()[2] - image.getchannel('A').getbbox()[0])
        silhouette = contour(image, 'lineart', width=1, details=False)
        detailed = contour(image, 'lineart', width=1, details=True)
        self.assertGreater(sum(detailed.getchannel('A').tobytes()), sum(silhouette.getchannel('A').tobytes()))
        self.assertEqual(detailed.getpixel((0,0))[3], 0)
        self.assertEqual(detailed.getpixel((53,53))[3], 0)
