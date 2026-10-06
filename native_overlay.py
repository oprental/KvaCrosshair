"""Small, unowned Win32 layered surface. Static crosshairs need one upload."""
import ctypes as C
from ctypes import wintypes as W
from PIL import Image, ImageChops


class BitmapHeader(C.Structure):
    _fields_ = [('size', W.DWORD), ('width', W.LONG), ('height', W.LONG),
                ('planes', W.WORD), ('bits', W.WORD), ('compression', W.DWORD),
                ('image_size', W.DWORD), ('xppm', W.LONG), ('yppm', W.LONG),
                ('used', W.DWORD), ('important', W.DWORD)]


class Blend(C.Structure):
    _fields_ = [('operation', W.BYTE), ('flags', W.BYTE),
                ('opacity', W.BYTE), ('alpha_format', W.BYTE)]


class NativeOverlay:
    def __init__(self):
        self.user = C.WinDLL('user32', use_last_error=True)
        self.gdi = C.WinDLL('gdi32', use_last_error=True)
        self.user.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD,
            C.c_int, C.c_int, C.c_int, C.c_int, W.HWND, W.HMENU, W.HINSTANCE, C.c_void_p]
        self.user.CreateWindowExW.restype = W.HWND
        self.user.UpdateLayeredWindow.argtypes = [W.HWND, W.HDC, C.POINTER(W.POINT),
            C.POINTER(W.SIZE), W.HDC, C.POINTER(W.POINT), W.DWORD, C.POINTER(Blend), W.DWORD]
        self.user.UpdateLayeredWindow.restype = W.BOOL
        self.user.SetWindowPos.argtypes = [W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT]
        self.user.SetWindowPos.restype = W.BOOL
        self.user.ShowWindow.argtypes = [W.HWND, C.c_int]
        self.user.DestroyWindow.argtypes = [W.HWND]
        self.gdi.CreateCompatibleDC.argtypes = [W.HDC]
        self.gdi.CreateCompatibleDC.restype = W.HDC
        self.gdi.CreateDIBSection.argtypes = [W.HDC, C.c_void_p, W.UINT,
                                             C.POINTER(C.c_void_p), W.HANDLE, W.DWORD]
        self.gdi.CreateDIBSection.restype = W.HBITMAP
        self.gdi.SelectObject.argtypes = [W.HDC, W.HANDLE]
        self.gdi.SelectObject.restype = W.HANDLE
        self.gdi.DeleteObject.argtypes = [W.HANDLE]
        self.gdi.DeleteDC.argtypes = [W.HDC]
        # Click-through, no activation, no taskbar button or owner relation to Tk.
        flags = 0x00080000 | 0x00000020 | 0x08000000 | 0x00000080
        self.hwnd = self.user.CreateWindowExW(flags, 'STATIC', 'KVA crosshair',
            0x80000000, 0, 0, 1, 1, None, None, None, None)
        if not self.hwnd:
            raise C.WinError(C.get_last_error())
        self.dc = self.gdi.CreateCompatibleDC(None)
        if not self.dc:
            self.user.DestroyWindow(self.hwnd)
            raise C.WinError(C.get_last_error())
        self.bitmap = None
        self.previous_object = None
        self.dimensions = None
        self.pixels = C.c_void_p()
        self.visible = False
        self.upload_count = 0
        self.last_bounds = None
        self.last_image = None

    def _surface(self, dimensions):
        if dimensions == self.dimensions:
            return
        header = BitmapHeader(C.sizeof(BitmapHeader), dimensions[0], -dimensions[1], 1, 32, 0)
        # BITMAPINFO consists of a header and one unused RGBQUAD for BI_RGB.
        info = C.create_string_buffer(C.sizeof(header) + 4)
        C.memmove(info, C.byref(header), C.sizeof(header))
        pixels = C.c_void_p()
        bitmap = self.gdi.CreateDIBSection(self.dc, info, 0, C.byref(pixels), None, 0)
        if not bitmap:
            raise C.WinError(C.get_last_error())
        old = self.gdi.SelectObject(self.dc, bitmap)
        if self.bitmap:
            self.gdi.DeleteObject(self.bitmap)
        else:
            self.previous_object = old
        self.bitmap, self.pixels, self.dimensions = bitmap, pixels, dimensions

    def display(self, image, x, y, opacity=100):
        image = image.convert('RGBA')
        bounds = image.getchannel('A').getbbox()
        if bounds:
            x, y = x + bounds[0], y + bounds[1]
            image = image.crop(bounds)
        else:
            image = Image.new('RGBA', (1, 1))
        self._surface(image.size)
        r, g, b, alpha = image.split()
        # UpdateLayeredWindow requires premultiplied BGRA, not a color-key backing.
        data = Image.merge('RGBA', (ImageChops.multiply(b, alpha),
            ImageChops.multiply(g, alpha), ImageChops.multiply(r, alpha), alpha)).tobytes()
        C.memmove(self.pixels, data, len(data))
        destination, origin = W.POINT(x, y), W.POINT(0, 0)
        size = W.SIZE(*image.size)
        blend = Blend(0, 0, round(max(0, min(100, opacity)) * 255 / 100), 1)
        if not self.user.UpdateLayeredWindow(self.hwnd, None, C.byref(destination),
                C.byref(size), self.dc, C.byref(origin), 0, C.byref(blend), 2):
            raise C.WinError(C.get_last_error())
        self.last_bounds = (x, y, x + image.width, y + image.height)
        self.last_image = image
        self.upload_count += 1

    def show(self):
        # HWND_TOPMOST + SWP_NOACTIVATE + SWP_SHOWWINDOW, no focus theft.
        if not self.user.SetWindowPos(self.hwnd, W.HWND(-1), 0, 0, 0, 0, 0x0053):
            raise C.WinError(C.get_last_error())
        self.visible = True

    def hide(self):
        self.user.ShowWindow(self.hwnd, 0)
        self.visible = False

    def close(self):
        if self.bitmap:
            self.gdi.SelectObject(self.dc, self.previous_object)
            self.gdi.DeleteObject(self.bitmap)
            self.bitmap = None
        if self.dc:
            self.gdi.DeleteDC(self.dc)
            self.dc = None
        if self.hwnd:
            self.user.DestroyWindow(self.hwnd)
            self.hwnd = None
