"""Keep catalogue sessions encrypted for the current Windows user."""
import ctypes
from ctypes import wintypes
import json


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def protect(raw, decrypt=False):
    buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    source = Blob(len(raw), buffer)
    result = Blob()
    api = ctypes.WinDLL('crypt32', use_last_error=True)
    method = api.CryptUnprotectData if decrypt else api.CryptProtectData
    method.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    method.restype = wintypes.BOOL
    if not method(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


def save(folder, origin, token):
    raw = json.dumps(dict(origin=origin, token=token)).encode('utf-8')
    temporary = folder / 'session.tmp'
    temporary.write_bytes(protect(raw))
    temporary.replace(folder / 'session.dat')


def load(folder, origin):
    try:
        path = folder / 'session.dat'
        if path.stat().st_size > 16384:
            return ''
        saved = json.loads(protect(path.read_bytes(), decrypt=True))
        token = saved.get('token')
        if saved.get('origin') == origin and isinstance(token, str) and 20 <= len(token) <= 100:
            return token
    except (OSError, ValueError, AttributeError):
        pass
    return ''


def clear(folder):
    (folder / 'session.dat').unlink(missing_ok=True)
