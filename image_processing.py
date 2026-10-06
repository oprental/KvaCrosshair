"""Local anime cutout and contour tools; user images never leave the PC."""
import hashlib
from pathlib import Path
import urllib.request
from PIL import Image, ImageChops, ImageFilter, ImageOps

MODEL_URL = 'https://github.com/danielgatis/rembg/releases/download/v0.0.0/isnet-anime.onnx'
MODEL_SHA256 = 'f15622d853e8260172812b657053460e20806f04b9e05147d49af7bed31a6e99'
LINEART_URL = 'https://kvacrosshair.online/models/anime-lineart-v1.onnx'
LINEART_SHA256 = '68ab9bca75cdb036f4b29117b818f5b6f337179aae30c2c371aae14891c7b78b'


def download_model(folder, progress=lambda message: None, *, name='isnet-anime',
                   url=MODEL_URL, digest=MODEL_SHA256, license_name='anime-model-LICENSE.txt'):
    folder = Path(folder) / 'models'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (name + '.onnx')
    license_path = Path(__file__).resolve().parent / 'assets' / license_name
    if license_path.exists():
        (folder / license_name).write_bytes(license_path.read_bytes())
    if target.exists():
        with target.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() == digest:
                return target
    temporary = folder / (name + '.part')
    try:
        with urllib.request.urlopen(url, timeout=30) as response, temporary.open('wb') as stream:
            count = 0
            while chunk := response.read(1024 * 1024):
                count += len(chunk)
                if count > 300_000_000:
                    raise ValueError('Файл модели слишком большой')
                stream.write(chunk)
                progress(f'Загрузка модели: {count // (1024 * 1024)} МБ')
        with temporary.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                raise ValueError('Проверка модели не пройдена')
        temporary.replace(target)
        return target
    finally:
        temporary.unlink(missing_ok=True)


def already_isolated(image):
    """A padded opaque rectangle or one transparent pixel is not a cutout."""
    import numpy as np
    alpha = np.asarray(image.getchannel('A'))
    bounds = image.getchannel('A').point(lambda v: 255 if v >= 128 else 0).getbbox()
    if not bounds:
        return True
    x0, y0, x1, y1 = bounds
    cropped = alpha[y0:y1, x0:x1]
    band = max(1, min(cropped.shape) // 40)
    border = np.concatenate((cropped[:band].ravel(), cropped[-band:].ravel(), cropped[:, :band].ravel(), cropped[:, -band:].ravel()))
    return float((cropped < 32).mean()) > .12 and float((border < 32).mean()) > .65


def clean_mask(mask, strength=25):
    import cv2
    import numpy as np
    strength = max(0, min(60, int(strength))) / 100
    values = np.asarray(mask, dtype=np.float32) / 255
    result = (np.clip((values - strength) / max(.95 - strength, .1), 0, 1) * 255).astype('uint8')
    count, labels, stats, _ = cv2.connectedComponentsWithStats((result >= 128).astype('uint8'), 8)
    if count > 1:
        largest = int(stats[1:, cv2.CC_STAT_AREA].max())
        keep = np.zeros(count, dtype=bool)
        keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= max(8, largest * .002)
        result *= cv2.dilate(keep[labels].astype('uint8'), np.ones((5, 5), dtype='uint8'))
    return Image.fromarray(result)


def cutout(image, folder, progress=lambda message: None, strength=25):
    import numpy as np
    import onnxruntime as ort
    image = image.convert('RGBA')
    alpha = image.getchannel('A')
    # Already isolated transparent artwork needs no segmentation.
    if already_isolated(image):
        return image
    model = download_model(folder, progress)
    progress('Вырезаю персонажа…')
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.enable_cpu_mem_arena = False
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    session = ort.InferenceSession(str(model), sess_options=options, providers=['CPUExecutionProvider'])
    background = Image.new('RGBA', image.size, 'white')
    background.alpha_composite(image)
    rgb = np.asarray(background.convert('RGB').resize((1024, 1024), Image.Resampling.LANCZOS), dtype=np.float32)
    rgb /= max(float(rgb.max()), 1e-6)
    rgb -= np.array([.485, .456, .406], dtype=np.float32)
    inputs = np.transpose(rgb, (2, 0, 1))[None, ...]
    prediction = session.run(None, {session.get_inputs()[0].name: inputs})[0][0, 0]
    low, high = float(prediction.min()), float(prediction.max())
    mask = np.clip((prediction - low) / max(high - low, 1e-8) * 255, 0, 255).astype('uint8')
    mask = Image.fromarray(mask).resize(image.size, Image.Resampling.LANCZOS)
    image.putalpha(ImageChops.multiply(alpha, clean_mask(mask, strength)))
    return image


def neural_lineart(image, folder, progress=lambda message: None, color='#b693ff', width=1, threshold=35):
    """MIT Anime2Sketch improved model, CPU ONNX; retain soft, antialiased ink."""
    import numpy as np
    import cv2
    import onnxruntime as ort
    model = download_model(folder, progress, name='anime-lineart-v1', url=LINEART_URL,
                           digest=LINEART_SHA256, license_name='lineart-model-LICENSE.txt')
    progress('Нейросеть рисует линии лица, волос и одежды…')
    image = image.convert('RGBA')
    background = Image.new('RGBA', image.size, 'white')
    background.alpha_composite(image)
    # Preserve aspect ratio. A U-Net input needs multiples of 256, not a stretched square.
    scaled = background.convert('RGB')
    scaled.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
    w, h = scaled.size
    padded = Image.new('RGB', (((w + 255) // 256) * 256, ((h + 255) // 256) * 256), 'white')
    padded.paste(scaled)
    inputs = np.asarray(padded, dtype=np.float32) / 127.5 - 1
    inputs = np.transpose(inputs, (2, 0, 1))[None, ...]
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.enable_cpu_mem_arena = False
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    session = ort.InferenceSession(str(model), sess_options=options, providers=['CPUExecutionProvider'])
    prediction = session.run(None, {session.get_inputs()[0].name: inputs})[0][0, 0, :h, :w]
    ink = np.clip((1 - prediction) * 127.5, 0, 255)
    cutoff = max(15, min(100, int(threshold))) * .45
    ink = (np.clip((ink - cutoff) / (230 - cutoff), 0, 1) ** .7 * 255).astype('uint8')
    alpha = Image.fromarray(ink).resize(image.size, Image.Resampling.LANCZOS)
    width = max(1, min(6, int(width)))
    if width > 1:
        alpha = Image.fromarray(cv2.dilate(np.asarray(alpha), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (width, width))))
    alpha = ImageChops.multiply(alpha, image.getchannel('A'))
    result = Image.new('RGBA', image.size, color)
    result.putalpha(alpha)
    return result


def thin_lines(binary):
    """Zhang-Suen thinning keeps ink as connected single-pixel centerlines."""
    import numpy as np
    pixels = np.pad(binary.astype(bool), 1)
    for _ in range(64):
        changed = False
        for step in (0, 1):
            p = [pixels[:-2, 1:-1], pixels[:-2, 2:], pixels[1:-1, 2:], pixels[2:, 2:], pixels[2:, 1:-1], pixels[2:, :-2], pixels[1:-1, :-2], pixels[:-2, :-2]]
            neighbors = sum(v.astype('uint8') for v in p)
            transitions = sum((~p[i] & p[(i + 1) % 8]).astype('uint8') for i in range(8))
            first = p[0] & p[2] & (p[4] if step == 0 else p[6])
            second = (p[2] if step == 0 else p[0]) & p[4] & p[6]
            remove = pixels[1:-1, 1:-1] & (neighbors >= 2) & (neighbors <= 6) & (transitions == 1) & ~first & ~second
            if remove.any():
                pixels[1:-1, 1:-1][remove] = False
                changed = True
        if not changed:
            break
    return pixels[1:-1, 1:-1].astype('uint8') * 255


def contour(image, mode, color='#b693ff', width=1, details=True, threshold=35):
    image = image.convert('RGBA')
    if mode == 'cutout':
        return image.copy()
    width = max(1, min(6, int(width)))
    image = ImageOps.expand(image, border=width + 2, fill=(0, 0, 0, 0))
    alpha = image.getchannel('A').point(lambda value: 255 if value >= 128 else 0)
    dilated = alpha.filter(ImageFilter.MaxFilter(width * 2 + 1))
    if mode == 'outlined':
        background = Image.new('RGBA', image.size, color)
        background.putalpha(dilated)
        background.alpha_composite(image)
        return background
    if mode != 'lineart':
        raise ValueError('Неизвестный режим обработки')
    eroded = alpha.filter(ImageFilter.MinFilter(3))
    lines = ImageChops.subtract(alpha, eroded)
    if details:
        import cv2
        import numpy as np
        background = Image.new('RGBA', image.size, 'white')
        background.alpha_composite(image)
        gray = np.asarray(background.convert('L'))
        smooth = cv2.bilateralFilter(gray, 5, 25, 25)
        threshold = max(15, min(100, int(threshold)))
        ink = cv2.adaptiveThreshold(smooth, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, max(3, threshold / 5))
        foreground = np.asarray(eroded) > 0
        ink[~foreground] = 0
        edges_array = cv2.Canny(smooth, threshold * .6, threshold * 1.8, L2gradient=True)
        edges_array[cv2.dilate(ink, np.ones((5, 5), dtype='uint8')) > 0] = 0
        edges_array[~foreground] = 0
        edges = Image.fromarray(np.maximum(thin_lines(ink > 0), edges_array))
        lines = ImageChops.lighter(lines, edges)
    if width > 1:
        import cv2
        import numpy as np
        lines = Image.fromarray(cv2.dilate(np.asarray(lines), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (width, width))))
    result = Image.new('RGBA', image.size, color)
    result.putalpha(lines)
    return result
