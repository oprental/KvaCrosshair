"""Bounded GIF decoding and pixel-only export for shared animations."""
import io
from PIL import Image

MAX_FRAMES = 200
MAX_PIXELS = 32_000_000


def clean_gif(raw):
    if len(raw) > 4_500_000:
        raise ValueError('GIF слишком большой: максимум 4,5 МБ')
    frames, durations = [], []
    with Image.open(io.BytesIO(raw)) as source:
        if source.format != 'GIF' or max(source.size) > 1024:
            raise ValueError('Нужен GIF не больше 1024 × 1024')
        count = source.n_frames
        if count > MAX_FRAMES or count * source.width * source.height > MAX_PIXELS:
            raise ValueError('GIF слишком длинный или тяжёлый: максимум 200 кадров и 32 млн пикселей суммарно')
        loop = source.info.get('loop', 0)
        for index in range(count):
            source.seek(index)
            frame = source.convert('RGBA')
            palette = frame.convert('RGB').quantize(colors=255)
            # Reserve palette index zero for transparency in every frame.
            pixels = bytes(value + 1 for value in palette.tobytes())
            result = Image.frombytes('P', frame.size, pixels)
            result.putpalette([0, 0, 0] + palette.getpalette()[:765])
            result.paste(0, mask=frame.getchannel('A').point(lambda value: 255 if value < 128 else 0))
            frames.append(result)
            durations.append(max(20, min(10000, int(source.info.get('duration', 100)))))
    output = io.BytesIO()
    frames[0].save(output, format='GIF', save_all=True, append_images=frames[1:], duration=durations,
                   loop=loop, disposal=2, transparency=0, background=0, optimize=False)
    if output.tell() > 4_500_000:
        raise ValueError('После обработки GIF превышает 4,5 МБ')
    return output.getvalue()
