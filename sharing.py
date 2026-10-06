"""Portable crosshair packages. Paths from other users are never trusted."""
import base64
import io
import re
import uuid
from pathlib import Path
from PIL import Image, PngImagePlugin
from gif_support import clean_gif

# Crosshair packages have no use for large compressed textual PNG metadata.
PngImagePlugin.MAX_TEXT_CHUNK = 64 * 1024
PngImagePlugin.MAX_TEXT_MEMORY = 256 * 1024

MAX_PACKAGE = 8_000_000
LIMITS = {'size': (2, 4096), 'gap': (0, 30), 'thickness': (1, 12),
          'opacity': (10, 100), 'offset_x': (-500, 500), 'offset_y': (-500, 500)}
STYLES = ['Крест', 'Точка', 'Круг', 'Т-образный', 'Изображение']


def validate(package):
    if not isinstance(package, dict) or type(package.get('version')) is not int or package['version'] != 1:
        raise ValueError('Неизвестный формат прицела')
    settings = package.get('settings')
    if not isinstance(settings, dict):
        raise ValueError('Нет настроек прицела')
    result = {}
    for key, (low, high) in LIMITS.items():
        value = settings.get(key)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'Некорректный параметр: {key}')
        result[key] = value
    for key in ('dot', 'outline'):
        if type(settings.get(key)) is not bool:
            raise ValueError(f'Некорректный параметр: {key}')
        result[key] = settings[key]
    if settings.get('style') not in STYLES:
        raise ValueError('Неизвестная форма')
    color = settings.get('color', '')
    if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
        raise ValueError('Некорректный цвет')
    name = settings.get('name', '')
    if not isinstance(name, str) or not name.strip() or len(name) > 80:
        raise ValueError('Название должно содержать от 1 до 80 символов')
    author = package.get('author', '')
    if not isinstance(author, str) or len(author) > 60:
        raise ValueError('Слишком длинное имя автора')
    try:
        name.encode('utf-8')
        author.encode('utf-8')
    except UnicodeError:
        raise ValueError('Некорректная кодировка текста') from None
    if any(ord(char) < 32 for char in name+author):
        raise ValueError('Управляющие символы в тексте запрещены')
    result.update(name=name.strip(), style=settings['style'], color=color, image='')
    encoded = ''
    gif = ''
    if result['style'] == 'Изображение':
        gif = package.get('image_gif', '')
        if gif:
            if not isinstance(gif, str) or len(gif) > 6_000_000:
                raise ValueError('Некорректное изображение GIF')
            try:
                raw_gif = clean_gif(base64.b64decode(gif, validate=True))
                gif = base64.b64encode(raw_gif).decode('ascii')
                poster = io.BytesIO()
                with Image.open(io.BytesIO(raw_gif)) as animation:
                    pixels = animation.convert('RGBA')
                    pixels.info.clear()
                    pixels.save(poster, format='PNG')
                encoded = base64.b64encode(poster.getvalue()).decode('ascii')
                if len(gif) + len(encoded) > 7_900_000:
                    raise ValueError('GIF вместе с предпросмотром превышает размер публикации')
            except (OSError, ValueError) as error:
                raise ValueError(str(error)) from error
            # A PNG poster lets earlier clients browse these publications.
            return dict(version=1, settings=result, author=author.strip(), image_png=encoded, image_gif=gif)
        encoded = package.get('image_png', '')
        if not isinstance(encoded, str) or len(encoded) > 6_000_000:
            raise ValueError('Изображение слишком большое')
        try:
            raw = base64.b64decode(encoded, validate=True)
            with Image.open(io.BytesIO(raw)) as picture:
                if picture.format != 'PNG' or max(picture.size) > 2048:
                    raise ValueError('Нужен PNG не больше 2048 × 2048')
                picture.load()
                # Re-encode only pixels: strip extra chunks and appended payloads.
                clean = io.BytesIO()
                pixels = picture.convert('RGBA')
                pixels.info.clear()
                pixels.save(clean, format='PNG')
                encoded = base64.b64encode(clean.getvalue()).decode('ascii')
        except (OSError, ValueError) as error:
            raise ValueError('Некорректное изображение PNG') from error
    return dict(version=1, settings=result, author=author.strip(), image_png=encoded)


def pack(settings, author=''):
    settings = settings.copy()
    encoded = ''
    if settings['style'] == 'Изображение':
        with Image.open(settings['image']) as image:
            if image.format == 'GIF':
                image.seek(0)
                image.fp.seek(0)
                raw = image.fp.read(4_500_001)
                settings['image'] = ''
                return validate(dict(version=1, settings=settings, author=author, image_png='', image_gif=base64.b64encode(raw).decode('ascii')))
            image = image.convert('RGBA')
            image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format='PNG')
            encoded = base64.b64encode(output.getvalue()).decode('ascii')
    settings['image'] = ''
    return validate(dict(version=1, settings=settings, author=author, image_png=encoded))


def install(package, data):
    package = validate(package)
    settings = package['settings'].copy()
    if package.get('image_gif') or package['image_png']:
        folder = Path(data) / 'images'
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / (uuid.uuid4().hex + ('.gif' if package.get('image_gif') else '.png'))
        target.write_bytes(base64.b64decode(package.get('image_gif') or package['image_png']))
        settings['image'] = str(target)
    return settings


def preview_settings(package):
    """Return settings and an in-memory image for catalogue thumbnails."""
    package = validate(package)
    settings = package['settings'].copy()
    if package.get('image_gif') or package['image_png']:
        settings['image'] = io.BytesIO(base64.b64decode(package.get('image_gif') or package['image_png']))
    return settings
