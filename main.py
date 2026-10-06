"""Crosshair KVA — configurable Windows crosshair overlay."""
import ctypes
import json
import os
import shutil
import sys
from contextlib import nullcontext
from pathlib import Path
import tkinter as tk
from tkinter import ttk, colorchooser, filedialog, messagebox
from PIL import Image, ImageDraw, ImageTk

DATA = Path(os.getenv('LOCALAPPDATA', str(Path.home()))) / 'CrosshairKVA'
DEFAULT = dict(name='Мой прицел', style='Крест', color='#65f7a5', size=24,
               gap=5, thickness=2, opacity=100, outline=True, dot=True,
               offset_x=0, offset_y=0, image='')
STYLES = ['Крест', 'Точка', 'Круг', 'Т-образный', 'Изображение']
BG = '#131019'
PANEL = '#1b1624'
FIELD = '#251e32'
ACCENT = '#9670ee'
TEXT = '#eee9f6'
MUTED = '#9c90ad'


def render(settings, max_dimension=None, frame=None):
    scale = 4
    size = max(2, min(4096, int(settings['size'])))
    if max_dimension:
        size = min(size, max_dimension // 2)
    side = max(256, size * 2 + int(settings['gap']) * 2 + 32)
    if side > 512:
        scale = 1
    if settings['style'] == 'Изображение' and settings.get('image'):
        with (Image.open(settings['image']) if frame is None else nullcontext(frame)) as source:
            source = source.convert('RGBA')
            ratio = size * 2 * scale / max(source.size)
            source = source.resize((max(1, round(source.width * ratio)), max(1, round(source.height * ratio))), Image.Resampling.LANCZOS)
            dimensions = (source.width + 16, source.height + 16) if side > 512 else (side * scale, side * scale)
            image = Image.new('RGBA', dimensions)
            image.alpha_composite(source, ((image.width-source.width)//2, (image.height-source.height)//2))
    else:
        image = Image.new('RGBA', (side * scale, side * scale))
        draw = ImageDraw.Draw(image)
        center = side * scale // 2
        gap = int(settings['gap']) * scale
        length = size * scale
        width = int(settings['thickness']) * scale
        color = settings['color']
        lines = []
        if settings['style'] in ('Крест', 'Т-образный'):
            lines = [(center-gap-length, center, center-gap, center),
                     (center+gap, center, center+gap+length, center),
                     (center, center+gap, center, center+gap+length)]
            if settings['style'] == 'Крест':
                lines.append((center, center-gap-length, center, center-gap))
        for line in lines:
            if settings['outline']:
                draw.line(line, fill='black', width=width+2*scale)
            draw.line(line, fill=color, width=width)
        if settings['style'] == 'Круг':
            box = (center-length, center-length, center+length, center+length)
            if settings['outline']:
                draw.ellipse(box, outline='black', width=width+2*scale)
            inset = scale if settings['outline'] else 0
            draw.ellipse((box[0]+inset, box[1]+inset, box[2]-inset, box[3]-inset), outline=color, width=width)
        if settings['dot'] or settings['style'] == 'Точка':
            radius = max(width / 2, scale)
            if settings['outline']:
                draw.ellipse((center-radius-scale, center-radius-scale, center+radius+scale, center+radius+scale), fill='black')
            draw.ellipse((center-radius, center-radius, center+radius, center+radius), fill=color)
    if scale > 1:
        image = image.resize((image.width // scale, image.height // scale), Image.Resampling.LANCZOS)
    return image


class Application:
    def __init__(self):
        DATA.mkdir(parents=True, exist_ok=True)
        self.data = DATA
        self.catalog_panel = None
        self.constructor_panel = None
        self.installed_panel = None
        self.animation_timer = None
        self.animation_reader = None
        self.profiles = [DEFAULT.copy()]
        self.active = 0
        try:
            saved = json.loads((DATA / 'settings.json').read_text(encoding='utf-8'))
            if saved['profiles']:
                self.profiles = [dict(DEFAULT, **p) for p in saved['profiles']]
                self.active = min(max(0, saved.get('active', 0)), len(self.profiles)-1)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self.root = tk.Tk()
        def log_ui_error(kind, value, traceback):
            import traceback as traceback_module
            try:
                with (self.data / 'errors.log').open('a', encoding='utf-8') as output:
                    output.write(''.join(traceback_module.format_exception(kind, value, traceback)))
            except OSError:
                pass
        self.root.report_callback_exception = log_ui_error
        self.root.title('Crosshair KVA')
        icon_path = Path(__file__).resolve().parent / 'assets' / 'icon.ico'
        if icon_path.exists():
            self.root.iconbitmap(default=str(icon_path))
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('CrosshairKVA.Desktop')
        except (AttributeError, OSError):
            pass
        self.root.geometry('860x820')
        self.root.minsize(820, 800)
        self.root.configure(bg=BG)
        self.visible = False
        self.pending = None
        self.vars = {}
        from native_overlay import NativeOverlay
        self.overlay = NativeOverlay()
        self.last_rendered_settings = None
        theme = ttk.Style()
        theme.theme_use('clam')
        theme.configure('.', background=BG, foreground=TEXT, font=('Segoe UI', 10))
        theme.configure('TButton', padding=(14, 9), background=FIELD, borderwidth=0, focusthickness=0)
        theme.map('TButton', background=[('active', '#352945'), ('pressed', '#443357')])
        theme.configure('Accent.TButton', background=ACCENT, foreground='#ffffff', font=('Segoe UI', 11, 'bold'), padding=(18, 13))
        theme.map('Accent.TButton', background=[('active', '#a583f5'), ('pressed', '#8059d5')])
        theme.configure('TEntry', fieldbackground=FIELD, foreground=TEXT, insertcolor=TEXT, bordercolor=FIELD, lightcolor=FIELD, darkcolor=FIELD, padding=8)
        theme.configure('TCombobox', fieldbackground=FIELD, background=FIELD, foreground=TEXT, arrowcolor=MUTED, bordercolor=FIELD, lightcolor=FIELD, darkcolor=FIELD, padding=7)
        theme.map('TCombobox', fieldbackground=[('readonly', FIELD)], foreground=[('readonly', TEXT)], selectbackground=[('readonly', FIELD)], selectforeground=[('readonly', TEXT)])
        theme.configure('TCheckbutton', background=BG, indicatorbackground=FIELD, indicatorforeground=ACCENT)
        theme.map('TCheckbutton', background=[('active', BG)], indicatorbackground=[('selected', ACCENT), ('active', '#352945')])
        theme.configure('Card.TFrame', background=PANEL)
        theme.configure('Card.TLabel', background=PANEL)
        theme.configure('Muted.TLabel', foreground=MUTED)
        theme.configure('Value.TLabel', foreground='#bea3fb')
        theme.configure('TScale', background=BG, troughcolor=FIELD, borderwidth=0)
        theme.configure('Purple.Vertical.TScrollbar', background='#59416e', troughcolor=PANEL,
                        bordercolor=PANEL, lightcolor='#59416e', darkcolor='#59416e',
                        arrowcolor='#bea3fb', borderwidth=0, relief='flat', width=12,
                        arrowsize=12)
        theme.map('Purple.Vertical.TScrollbar', background=[('pressed', ACCENT), ('active', '#805caf')],
                  lightcolor=[('pressed', ACCENT), ('active', '#805caf')],
                  darkcolor=[('pressed', ACCENT), ('active', '#805caf')])
        theme.configure('ActiveTab.TButton', foreground='#bea3fb', background='#352945')
        self.root.option_add('*TCombobox*Listbox.background', FIELD)
        self.root.option_add('*TCombobox*Listbox.foreground', TEXT)
        self.root.option_add('*TCombobox*Listbox.selectBackground', '#61448f')
        self.build_ui()
        self.load_profile()
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.user32 = ctypes.windll.user32
        from ctypes import wintypes
        self.user32.GetParent.argtypes = [wintypes.HWND]
        self.user32.GetParent.restype = wintypes.HWND
        self.user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        self.user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self.user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user32.SendMessageW.restype = wintypes.LPARAM
        self.user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.IsZoomed.argtypes = [wintypes.HWND]
        self.set_window_long_ptr = getattr(self.user32, 'SetWindowLongPtrW', self.user32.SetWindowLongW)
        self.set_window_long_ptr.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        self.set_window_long_ptr.restype = ctypes.c_ssize_t
        self.user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.user32.GetAsyncKeyState.restype = ctypes.c_short
        self.key_down = {key: bool(self.user32.GetAsyncKeyState(key) & 0x8000)
                         for key in (0x77, 0x78)}
        self.hotkey_label.configure(text='F8 — прицел • F9 — окно настроек')
        self.root.update_idletasks()
        self.apply_window_frame()
        self.root.bind('<Map>', self.on_window_map, add='+')
        self.poll_hotkeys()
        from updater import UpdateController
        self.updater = UpdateController(self, self.update_label, self.update_button)

    def build_ui(self):
        titlebar = tk.Frame(self.root, bg=BG, height=34)
        titlebar.pack(fill='x')
        titlebar.pack_propagate(False)
        self.titlebar = titlebar
        title = tk.Label(titlebar, text='Crosshair KVA', bg=BG, fg=MUTED, font=('Segoe UI', 9), padx=16)
        title.pack(side='left', fill='y')
        for widget in (titlebar, title):
            widget.bind('<Button-1>', self.start_drag)
            widget.bind('<B1-Motion>', self.drag_window)
            widget.bind('<ButtonRelease-1>', self.end_drag)
            widget.bind('<Double-Button-1>', self.maximize_window)
        for text, command, hover in [('×', self.close, '#b34769'), ('□', self.maximize_window, FIELD), ('−', self.minimize_window, FIELD)]:
            button = tk.Button(titlebar, text=text, command=command, bg=BG, fg=MUTED,
                               activebackground=hover, activeforeground=TEXT, relief='flat',
                               borderwidth=0, highlightthickness=0, width=4, font=('Segoe UI', 12), takefocus=True)
            button.pack(side='right', fill='y')
            button.bind('<Enter>', lambda _, b=button, c=hover: b.configure(bg=c, fg=TEXT))
            button.bind('<Leave>', lambda _, b=button: b.configure(bg=BG, fg=MUTED))
        grip = tk.Label(self.root, text='◢', bg=BG, fg='#51415f', cursor='size_nw_se', font=('Segoe UI', 10))
        grip.place(relx=1, rely=1, anchor='se')
        grip.bind('<ButtonPress-1>', self.start_resize)
        grip.bind('<B1-Motion>', self.resize_window)
        self.resize_grip = grip
        outer = ttk.Frame(self.root, padding=28)
        outer.pack(fill='both', expand=True)
        header = ttk.Frame(outer)
        header.pack(fill='x')
        ttk.Label(header, text='crosshair', font=('Segoe UI', 26, 'bold')).pack(side='left')
        ttk.Label(header, text='KVA', foreground=ACCENT, font=('Segoe UI', 12, 'bold')).pack(side='left', padx=(10, 0), pady=(10, 0))
        ttk.Label(outer, text='Настрой свой фокус.', style='Muted.TLabel').pack(anchor='w', pady=(2, 16))
        navigation = ttk.Frame(header)
        navigation.pack(side='right')
        self.catalog_tab = ttk.Button(navigation, text='Каталог', command=self.open_catalog)
        self.catalog_tab.pack(side='right', pady=(6, 0))
        self.constructor_tab = ttk.Button(navigation, text='Конструктор', command=self.open_constructor)
        self.constructor_tab.pack(side='right', padx=(0, 8), pady=(6, 0))
        self.installed_tab = ttk.Button(navigation, text='Установленные', command=self.open_installed)
        self.installed_tab.pack(side='right', padx=(0, 8), pady=(6, 0))
        self.settings_tab = ttk.Button(navigation, text='Настройки', style='ActiveTab.TButton', command=self.show_settings)
        self.settings_tab.pack(side='right', padx=(0, 8), pady=(6, 0))
        footer = ttk.Frame(outer)
        footer.pack(side='bottom', fill='x', pady=(12, 0))
        self.toggle_button = ttk.Button(footer, text='Включить прицел', style='Accent.TButton', command=self.toggle)
        self.toggle_button.pack(fill='x')
        self.hotkey_label = ttk.Label(footer, foreground=MUTED)
        self.hotkey_label.pack(pady=(8, 0))
        update_row = ttk.Frame(footer)
        update_row.pack(fill='x', pady=(6, 0))
        from updater import VERSION
        self.update_label = ttk.Label(update_row, text='Версия ' + VERSION, foreground=MUTED, font=('Segoe UI', 9))
        self.update_label.pack(side='left')
        self.update_button = ttk.Button(update_row, text='Проверить обновления')
        self.update_button.pack(side='right')
        self.editor_page = ttk.Frame(outer)
        self.catalog_page = ttk.Frame(outer)
        self.constructor_page = ttk.Frame(outer)
        self.installed_page = ttk.Frame(outer)
        self.editor_page.pack(fill='both', expand=True)
        outer = self.editor_page
        top = ttk.Frame(outer)
        top.pack(fill='x')
        self.profile_combo = ttk.Combobox(top, state='readonly', width=27)
        self.profile_combo.pack(side='left', fill='x', expand=True)
        self.profile_combo.bind('<<ComboboxSelected>>', self.select_profile)
        ttk.Button(top, text='+ Новый', command=self.new_profile).pack(side='left', padx=8)
        ttk.Button(top, text='Удалить', command=self.delete_profile).pack(side='left')
        body = ttk.Frame(outer)
        body.pack(fill='both', expand=True, pady=14)
        controls = ttk.Frame(body)
        controls.pack(side='left', fill='both', expand=True, padx=(0, 28))
        for key, default in DEFAULT.items():
            kind = tk.BooleanVar if isinstance(default, bool) else tk.IntVar if isinstance(default, int) else tk.StringVar
            self.vars[key] = kind(value=default)
        ttk.Label(controls, text='Название профиля').pack(anchor='w')
        ttk.Entry(controls, textvariable=self.vars['name']).pack(fill='x', pady=(3, 10))
        ttk.Label(controls, text='Форма').pack(anchor='w')
        ttk.Combobox(controls, textvariable=self.vars['style'], values=STYLES, state='readonly').pack(fill='x', pady=(3, 10))
        row = ttk.Frame(controls)
        row.pack(fill='x', pady=(0, 8))
        ttk.Button(row, text='Выбрать цвет', command=self.choose_color).pack(side='left')
        ttk.Button(row, text='Импорт прицела', command=self.import_image).pack(side='right')
        for key, title, start, end in [('size', 'Размер', 2, 4096), ('gap', 'Отступ от центра', 0, 30), ('thickness', 'Толщина', 1, 12), ('opacity', 'Непрозрачность', 10, 100), ('offset_x', 'Смещение X', -500, 500), ('offset_y', 'Смещение Y', -500, 500)]:
            row = ttk.Frame(controls)
            row.pack(fill='x', pady=1)
            ttk.Label(row, text=title).pack(side='left')
            if key == 'size':
                ttk.Spinbox(row, from_=start, to=end, textvariable=self.vars[key], width=6).pack(side='right')
            else:
                ttk.Label(row, textvariable=self.vars[key], width=5, anchor='e', style='Value.TLabel').pack(side='right')
            tk.Scale(controls, from_=start, to=end, orient='horizontal', variable=self.vars[key], showvalue=False, bg=BG, troughcolor=FIELD, highlightthickness=0, bd=0, sliderrelief='flat', sliderlength=18, width=8, activebackground=ACCENT).pack(fill='x', pady=(0, 2))
        row = ttk.Frame(controls)
        row.pack(fill='x', pady=8)
        ttk.Checkbutton(row, text='Обводка', variable=self.vars['outline']).pack(side='left')
        ttk.Checkbutton(row, text='Точка в центре', variable=self.vars['dot']).pack(side='right')
        right = ttk.Frame(body, style='Card.TFrame', padding=18)
        right.pack(side='right', fill='y')
        ttk.Label(right, text='Предпросмотр', style='Card.TLabel', foreground=MUTED).pack(anchor='w', pady=(0, 16))
        self.preview = tk.Canvas(right, width=280, height=280, bg=PANEL, highlightthickness=0)
        self.preview.pack()
        ttk.Button(right, text='Размер экрана', command=self.screen_size).pack(fill='x', pady=(10, 0))
        ttk.Label(right, text='Импортируй PNG или анимированный GIF.\nВ игре: оконный режим без рамки.', style='Card.TLabel', foreground=MUTED, wraplength=275).pack(anchor='w', pady=16)
        self.status = ttk.Label(right, text='Прицел выключен', style='Card.TLabel', foreground='#bea3fb')
        self.status.pack(anchor='w', pady=8)
        for var in self.vars.values():
            var.trace_add('write', self.schedule_update)
        self.resize_grip.lift()

    def settings(self):
        return {key: var.get() for key, var in self.vars.items()}

    def screen_size(self):
        self.vars['size'].set(min(4096, max(self.root.winfo_screenwidth(), self.root.winfo_screenheight()) // 2))

    def window_handle(self):
        return self.user32.GetParent(self.root.winfo_id())

    def apply_window_frame(self):
        # Keep the normal taskbar window and Windows minimize/restore behavior.
        hwnd = self.window_handle()
        flags = self.user32.GetWindowLongW(hwnd, -16)
        self.user32.SetWindowLongW(hwnd, -16, flags & ~0x00C40000)
        self.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0037)

    def on_window_map(self, event):
        if event.widget == self.root:
            self.root.after_idle(self.apply_window_frame)

    def start_drag(self, event):
        self.drag_origin = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())

    def end_drag(self, _=None):
        self.drag_origin = None

    def drag_window(self, event):
        origin = getattr(self, 'drag_origin', None)
        if origin is None:
            return
        mouse_x, mouse_y, window_x, window_y = origin
        dx, dy = event.x_root - mouse_x, event.y_root - mouse_y
        if abs(dx) + abs(dy) < 3:
            return
        hwnd = self.window_handle()
        if self.user32.IsZoomed(hwnd):
            fraction = min(1, max(0, (mouse_x - window_x) / self.root.winfo_width()))
            self.user32.ShowWindow(hwnd, 9)
            self.root.update_idletasks()
            window_x = event.x_root - round(self.root.winfo_width() * fraction)
            window_y = event.y_root - min(30, max(0, mouse_y - window_y))
            self.drag_origin = (event.x_root, event.y_root, window_x, window_y)
            dx = dy = 0
        # Do not enter the Windows modal move loop from a Tk callback. Keeping
        # movement in Tk's normal event loop avoids nested timer/UI callbacks.
        self.user32.SetWindowPos(hwnd, None, window_x + dx, window_y + dy, 0, 0, 0x0015)

    def minimize_window(self):
        self.root.iconify()

    def maximize_window(self, _=None):
        self.end_drag()
        hwnd = self.window_handle()
        self.user32.ShowWindow(hwnd, 9 if self.user32.IsZoomed(hwnd) else 3)

    def start_resize(self, event):
        self.resize_origin = (event.x_root, event.y_root, self.root.winfo_width(), self.root.winfo_height())

    def resize_window(self, event):
        if self.user32.IsZoomed(self.window_handle()):
            return
        x, y, width, height = self.resize_origin
        minimum_width, minimum_height = self.root.minsize()
        self.root.geometry(f'{max(minimum_width, width+event.x_root-x)}x{max(minimum_height, height+event.y_root-y)}')

    def open_catalog(self):
        from catalog import CatalogPanel
        self.editor_page.pack_forget()
        self.constructor_page.pack_forget()
        self.installed_page.pack_forget()
        self.installed_tab.configure(style='TButton')
        self.catalog_page.pack(fill='both', expand=True)
        if self.catalog_panel is None:
            self.catalog_panel = CatalogPanel(self, self.catalog_page)
        self.catalog_tab.configure(style='ActiveTab.TButton')
        self.settings_tab.configure(style='TButton')
        self.constructor_tab.configure(style='TButton')

    def open_constructor(self):
        from constructor import ConstructorPanel
        self.editor_page.pack_forget()
        self.catalog_page.pack_forget()
        self.installed_page.pack_forget()
        self.installed_tab.configure(style='TButton')
        self.constructor_page.pack(fill='both', expand=True)
        if self.constructor_panel is None:
            self.constructor_panel = ConstructorPanel(self, self.constructor_page)
        self.constructor_tab.configure(style='ActiveTab.TButton')
        self.catalog_tab.configure(style='TButton')
        self.settings_tab.configure(style='TButton')

    def show_settings(self):
        self.installed_page.pack_forget()
        self.installed_tab.configure(style='TButton')
        self.catalog_page.pack_forget()
        self.constructor_page.pack_forget()
        self.editor_page.pack(fill='both', expand=True)
        self.catalog_tab.configure(style='TButton')
        self.settings_tab.configure(style='ActiveTab.TButton')
        self.constructor_tab.configure(style='TButton')

    def open_installed(self):
        from installed import InstalledPanel
        self.update()
        for page in (self.editor_page, self.catalog_page, self.constructor_page):
            page.pack_forget()
        for tab in (self.settings_tab, self.catalog_tab, self.constructor_tab):
            tab.configure(style='TButton')
        self.installed_tab.configure(style='ActiveTab.TButton')
        self.installed_page.pack(fill='both', expand=True)
        if self.installed_panel is None:
            self.installed_panel = InstalledPanel(self, self.installed_page)
        self.installed_panel.draw_cards()

    def schedule_update(self, *_):
        if self.pending:
            self.root.after_cancel(self.pending)
        self.pending = self.root.after(40, self.update)

    def update(self):
        self.pending = None
        self.stop_animation()
        settings = self.settings()
        self.profiles[self.active] = settings
        self.profile_combo.configure(values=[p['name'] or 'Без названия' for p in self.profiles])
        self.profile_combo.current(self.active)
        try:
            bitmap = render(settings, max(self.root.winfo_screenwidth(), self.root.winfo_screenheight()))
        except (OSError, ValueError):
            self.status.configure(text='Изображение недоступно')
            bitmap = Image.new('RGBA', (256, 256))
        self.display_bitmap(bitmap, settings)
        self.last_rendered_settings = settings.copy()
        self.save()
        self.start_animation(settings)

    def display_bitmap(self, bitmap, settings):
        if self.editor_page.winfo_ismapped() or not hasattr(self, 'preview_photo'):
            preview_bitmap = bitmap.copy()
            preview_bitmap.thumbnail((256, 256), Image.Resampling.LANCZOS)
            preview_bitmap.putalpha(preview_bitmap.getchannel('A').point(lambda a: round(a*settings['opacity']/100)))
            self.preview_photo = ImageTk.PhotoImage(preview_bitmap)
            self.preview.delete('all')
            self.preview.create_oval(40, 40, 240, 240, outline='#30263e')
            self.preview.create_line(140, 20, 140, 260, fill='#272030')
            self.preview.create_line(20, 140, 260, 140, fill='#272030')
            self.preview.create_image(140, 140, image=self.preview_photo)
        x = self.root.winfo_screenwidth()//2-bitmap.width//2+settings['offset_x']
        y = self.root.winfo_screenheight()//2-bitmap.height//2+settings['offset_y']
        self.overlay.display(bitmap, x, y, settings['opacity'])

    def stop_animation(self):
        if self.animation_timer is not None:
            self.root.after_cancel(self.animation_timer)
            self.animation_timer = None
        if self.animation_reader is not None:
            self.animation_reader.close()
            self.animation_reader = None

    def start_animation(self, settings):
        if settings['style'] != 'Изображение' or not settings.get('image'):
            return
        try:
            source = Image.open(settings['image'])
            if source.format != 'GIF' or source.n_frames < 2:
                source.close()
                return
            from gif_support import MAX_FRAMES, MAX_PIXELS
            if max(source.size) > 1024 or source.n_frames > MAX_FRAMES or source.n_frames * source.width * source.height > MAX_PIXELS:
                source.close()
                self.status.configure(text='GIF слишком большой или длинный')
                return
            self.animation_reader = source
            self.animation_settings = settings.copy()
            self.animation_index = 0
            self.animation_loops = 0
            self.animation_repeat = source.info.get('loop', 0)
            self.animation_timer = self.root.after(max(20, min(10000, int(source.info.get('duration', 100)))), self.animate)
        except (OSError, ValueError):
            self.stop_animation()

    def animate(self):
        self.animation_timer = None
        source = self.animation_reader
        if source is None:
            return
        if not self.visible and not self.editor_page.winfo_ismapped():
            self.animation_timer = self.root.after(100, self.animate)
            return
        try:
            self.animation_index += 1
            if self.animation_index >= source.n_frames:
                self.animation_loops += 1
                if self.animation_repeat and self.animation_loops > self.animation_repeat:
                    self.stop_animation()
                    return
                self.animation_index = 0
            source.seek(self.animation_index)
            bitmap = render(self.animation_settings, max(self.root.winfo_screenwidth(), self.root.winfo_screenheight()), source.convert('RGBA'))
            self.display_bitmap(bitmap, self.animation_settings)
            self.animation_timer = self.root.after(max(20, min(10000, int(source.info.get('duration', 100)))), self.animate)
        except (OSError, ValueError, EOFError):
            self.stop_animation()

    def save(self):
        temp = DATA / 'settings.tmp'
        temp.write_text(json.dumps(dict(profiles=self.profiles, active=self.active), ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(DATA / 'settings.json')

    def load_profile(self):
        for key, var in self.vars.items():
            var.set(self.profiles[self.active][key])
        self.update()

    def select_profile(self, _):
        self.active = self.profile_combo.current()
        self.load_profile()

    def new_profile(self):
        self.profiles.append(dict(DEFAULT, name=f'Прицел {len(self.profiles)+1}'))
        self.active = len(self.profiles)-1
        self.load_profile()

    def delete_profile(self):
        if len(self.profiles) == 1:
            return
        self.profiles.pop(self.active)
        self.active = max(0, self.active-1)
        self.load_profile()

    def choose_color(self):
        color = colorchooser.askcolor(self.vars['color'].get(), parent=self.root)[1]
        if color:
            self.vars['color'].set(color)

    def import_image(self, filename=None):
        if filename is None:
            filename = filedialog.askopenfilename(filetypes=[('Изображения и анимации', '*.png *.gif *.jpg *.jpeg *.webp *.bmp')])
        if not filename:
            return
        try:
            with Image.open(filename) as image:
                is_gif = image.format == 'GIF'
                image.verify()
            import uuid
            target = DATA / 'images' / (uuid.uuid4().hex + ('.gif' if is_gif else Path(filename).suffix))
            target.parent.mkdir(exist_ok=True)
            if is_gif:
                from gif_support import clean_gif
                with open(filename, 'rb') as stream:
                    target.write_bytes(clean_gif(stream.read(4_500_001)))
            else:
                shutil.copy2(filename, target)
            self.vars['image'].set(str(target))
            self.vars['style'].set('Изображение')
        except (OSError, ValueError) as error:
            messagebox.showerror('Не удалось загрузить изображение', str(error))

    def toggle(self):
        self.visible = not self.visible
        if self.visible:
            if self.settings() != self.last_rendered_settings:
                self.update()
            self.overlay.show()
        else:
            self.overlay.hide()
        self.toggle_button.configure(text='Выключить прицел' if self.visible else 'Включить прицел')
        self.status.configure(text='Прицел включен' if self.visible else 'Прицел выключен')

    def poll_hotkeys(self):
        # Tk consumes thread messages before an after() callback can read
        # WM_HOTKEY. Poll the physical key state and act on press edges instead.
        for key in self.key_down:
            pressed = bool(self.user32.GetAsyncKeyState(key) & 0x8000)
            rising = pressed and not self.key_down[key]
            self.key_down[key] = pressed
            if not rising:
                continue
            if key == 0x77:
                self.toggle()
            elif key == 0x78:
                if self.root.state() == 'withdrawn':
                    self.root.deiconify()
                    self.root.lift()
                else:
                    self.root.withdraw()
        self.root.after(30, self.poll_hotkeys)

    def close(self):
        self.stop_animation()
        self.overlay.close()
        if self.constructor_panel is not None:
            self.constructor_panel.release()
        self.profiles[self.active] = self.settings()
        self.save()
        self.root.destroy()


if __name__ == '__main__':
    if sys.platform != 'win32':
        raise SystemExit('Crosshair KVA requires Windows.')
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    Application().root.mainloop()
