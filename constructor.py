"""Embedded transparent-image crosshair editor."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk, colorchooser, filedialog, messagebox
import uuid
import queue
import threading
from PIL import Image, ImageColor, ImageDraw, ImageTk

SIZE = 1024
TOOLS = ['Кисть', 'Ластик', 'Заливка', 'Линия', 'Прямоугольник', 'Круг']


def character(name, color):
    image = Image.new('RGBA', (128, 128))
    draw = ImageDraw.Draw(image)
    outline, ink = '#261c38', '#261c38'
    if name == 'Кот':
        draw.polygon([(25, 48), (25, 17), (49, 35), (80, 35), (104, 17), (104, 48)], fill=color)
        draw.line([(25, 48), (25, 17), (49, 35), (80, 35), (104, 17), (104, 48)], fill=outline, width=4)
        draw.ellipse((20, 33, 108, 104), fill=color, outline=outline, width=4)
        draw.polygon([(31, 26), (31, 44), (44, 36)], fill='#f3b7dc')
        draw.polygon([(98, 26), (98, 44), (85, 36)], fill='#f3b7dc')
        draw.ellipse((42, 58, 49, 69), fill=ink)
        draw.ellipse((79, 58, 86, 69), fill=ink)
        draw.polygon([(59, 72), (69, 72), (64, 78)], fill=ink)
        draw.arc((51, 73, 64, 88), 0, 180, fill=ink, width=2)
        draw.arc((64, 73, 77, 88), 0, 180, fill=ink, width=2)
        for a, b in [((26, 73), (43, 76)), ((24, 83), (43, 81)), ((85, 76), (102, 73)), ((85, 81), (104, 83))]:
            draw.line([a, b], fill=ink, width=2)
    elif name == 'Призрак':
        draw.ellipse((29, 19, 99, 88), fill=color, outline=outline, width=4)
        draw.rectangle((29, 56, 99, 98), fill=color)
        draw.polygon([(29, 93), (40, 108), (52, 97), (64, 108), (76, 97), (88, 108), (99, 93)], fill=color)
        draw.line([(29, 56), (29, 93), (40, 108), (52, 97), (64, 108), (76, 97), (88, 108), (99, 93), (99, 56)], fill=outline, width=4)
        draw.ellipse((44, 48, 53, 64), fill=ink)
        draw.ellipse((75, 48, 84, 64), fill=ink)
        draw.ellipse((59, 72, 69, 83), fill=ink)
    elif name == 'Смайлик':
        draw.ellipse((20, 20, 108, 108), fill=color, outline=outline, width=4)
        draw.ellipse((43, 44, 50, 58), fill=ink)
        draw.ellipse((78, 44, 85, 58), fill=ink)
        draw.arc((38, 48, 90, 91), 10, 170, fill=ink, width=4)
    else:
        raise ValueError('Unknown character')
    return image


class ConstructorPanel:
    def __init__(self, app, parent):
        from main import BG, MUTED
        self.app = app
        self.folder = app.data / 'constructor'
        self.folder.mkdir(exist_ok=True)
        self.image = Image.new('RGBA', (SIZE, SIZE))
        try:
            with Image.open(self.folder / 'draft.png') as saved:
                if max(saved.size) <= 2048:
                    self.image = saved.convert('RGBA')
        except (OSError, ValueError):
            pass
        self.undo_stack, self.redo_stack = [], []
        self.before = None
        self.last = None
        self.processing_busy = False
        self.processing_jobs = queue.Queue()
        self.frame = ttk.Frame(parent)
        self.frame.pack(fill='both', expand=True)
        ttk.Label(self.frame, text='Конструктор прицела', font=('Segoe UI', 18, 'bold')).pack(anchor='w')
        ttk.Label(self.frame, text='Нарисуй персонажа, значок или свой прицел. Шахматный фон — прозрачность.', foreground=MUTED, wraplength=760).pack(anchor='w', pady=(4, 12))
        body = ttk.Frame(self.frame)
        body.pack(fill='both', expand=True)
        left = ttk.Frame(body)
        left.pack(side='left', fill='both', expand=True, padx=(0, 20))
        self.canvas = tk.Canvas(left, background=BG, highlightthickness=0, width=430, height=390, cursor='crosshair')
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda _: self.redraw())
        self.canvas.bind('<Button-1>', self.press)
        self.canvas.bind('<B1-Motion>', self.motion)
        self.canvas.bind('<ButtonRelease-1>', self.release)
        history = ttk.Frame(left)
        history.pack(fill='x', pady=(8, 0))
        self.undo_button = ttk.Button(history, text='↶ Отменить', command=self.undo)
        self.undo_button.pack(side='left')
        self.redo_button = ttk.Button(history, text='↷ Повторить', command=self.redo)
        self.redo_button.pack(side='left', padx=6)
        ttk.Button(history, text='Очистить', command=self.clear).pack(side='right')
        right_host = ttk.Frame(body, width=245)
        right_host.pack(side='right', fill='y')
        right_host.pack_propagate(False)
        self.apply_button = ttk.Button(right_host, text='Использовать прицел', style='Accent.TButton', command=self.apply)
        self.apply_button.pack(side='bottom', fill='x', pady=(8, 0))
        sizing = ttk.Frame(right_host)
        sizing.pack(side='bottom', fill='x', pady=(8, 0))
        self.size = tk.IntVar(value=32)
        self.size_label = ttk.Label(sizing, text='Размер на экране: 64 px', foreground=MUTED)
        self.size_label.pack(anchor='w')
        ttk.Scale(sizing, from_=8, to=4096, variable=self.size, command=lambda _: self.redraw()).pack(fill='x')
        size_actions = ttk.Frame(sizing)
        size_actions.pack(fill='x')
        ttk.Button(size_actions, text='−', width=3, command=lambda: self.resize_overlay(.5)).pack(side='left')
        ttk.Button(size_actions, text='+', width=3, command=lambda: self.resize_overlay(2)).pack(side='left', padx=4)
        ttk.Button(size_actions, text='На весь экран', command=self.screen_size).pack(side='left', fill='x', expand=True)
        self.crop = tk.BooleanVar(value=True)
        ttk.Checkbutton(sizing, text='Обрезать пустые поля', variable=self.crop, command=self.redraw).pack(anchor='w')
        right_canvas = tk.Canvas(right_host, width=225, background=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(right_host, orient='vertical', style='Purple.Vertical.TScrollbar', command=right_canvas.yview)
        scroll.pack(side='right', fill='y')
        right_canvas.pack(side='left', fill='both', expand=True)
        right_canvas.configure(yscrollcommand=scroll.set)
        right = ttk.Frame(right_canvas)
        right_id = right_canvas.create_window((0, 0), window=right, anchor='nw')
        right.bind('<Configure>', lambda _: right_canvas.configure(scrollregion=right_canvas.bbox('all')))
        right_canvas.bind('<Configure>', lambda e: right_canvas.itemconfigure(right_id, width=e.width))
        def scroll_controls(event):
            if self.frame.winfo_ismapped() and (event.widget == right_canvas or str(event.widget).startswith(str(right) + '.')):
                right_canvas.yview_scroll(-int(event.delta / 120), 'units')
                return 'break'
        app.root.bind('<MouseWheel>', scroll_controls, add='+')
        self.name = tk.StringVar(value='Мой персонаж')
        ttk.Label(right, text='Название профиля').pack(anchor='w')
        ttk.Entry(right, textvariable=self.name).pack(fill='x', pady=(3, 10))
        ttk.Label(right, text='Размер холста').pack(anchor='w')
        self.resolution = tk.StringVar(value=f'{self.image.width} × {self.image.height}')
        resolutions = ttk.Combobox(right, textvariable=self.resolution, values=['128 × 128', '512 × 512', '1024 × 1024', '1920 × 1080'], state='readonly')
        resolutions.pack(fill='x', pady=(3, 8))
        resolutions.bind('<<ComboboxSelected>>', self.change_canvas)
        self.tool = tk.StringVar(value='Кисть')
        ttk.Label(right, text='Инструмент').pack(anchor='w')
        ttk.Combobox(right, textvariable=self.tool, values=TOOLS, state='readonly').pack(fill='x', pady=(3, 8))
        self.color = '#b693ff'
        palette = ttk.Frame(right)
        palette.pack(fill='x', pady=(0, 7))
        for color in ('#b693ff', '#65f7a5', '#ffca72', '#ff81bd', '#ffffff', '#261c38'):
            tk.Button(palette, bg=color, activebackground=color, width=2, height=1, relief='flat', command=lambda c=color: self.set_color(c)).pack(side='left', padx=(0, 3))
        self.color_button = ttk.Button(right, text='Другой цвет', command=self.choose_color)
        self.color_button.pack(fill='x')
        self.width = tk.IntVar(value=12)
        widths = ttk.Frame(right)
        widths.pack(fill='x', pady=(8, 8))
        ttk.Label(widths, text='Толщина').pack(side='left')
        ttk.Spinbox(widths, from_=1, to=128, textvariable=self.width, width=4, state='readonly').pack(side='right')
        self.filled = tk.BooleanVar(value=False)
        ttk.Checkbutton(right, text='Закрашивать фигуры', variable=self.filled).pack(anchor='w')
        self.guides = tk.BooleanVar(value=True)
        ttk.Checkbutton(right, text='Показывать центр', variable=self.guides, command=self.redraw).pack(anchor='w')
        ttk.Label(right, text='Начни с персонажа', foreground=MUTED).pack(anchor='w', pady=(10, 4))
        templates = ttk.Frame(right)
        templates.pack(fill='x')
        for name in ('Кот', 'Призрак', 'Смайлик'):
            ttk.Button(templates, text='Смайл' if name == 'Смайлик' else name, padding=(5, 6), command=lambda n=name: self.use_template(n)).pack(side='left', expand=True, fill='x', padx=(0, 3))
        file_row = ttk.Frame(right)
        file_row.pack(fill='x', pady=(8, 0))
        ttk.Button(file_row, text='Импорт', command=self.import_image).pack(side='left', fill='x', expand=True, padx=(0, 4))
        ttk.Button(file_row, text='PNG', command=self.export).pack(side='left', fill='x', expand=True)
        ttk.Button(right, text='Взять текущий прицел', command=self.load_current).pack(fill='x', pady=(5, 8))
        ttk.Label(right, text='Обработка персонажа · PRO', foreground=MUTED).pack(anchor='w', pady=(8, 4))
        self.processing_mode = tk.StringVar(value='Вырезать персонажа')
        ttk.Combobox(right, textvariable=self.processing_mode, values=['Вырезать персонажа', 'Персонаж с обводкой', 'Контурный рисунок'], state='readonly').pack(fill='x')
        self.processing_method = tk.StringVar(value='Нейросеть · аниме')
        ttk.Combobox(right, textvariable=self.processing_method, values=['Нейросеть · аниме', 'Обычный алгоритм'], state='readonly').pack(fill='x', pady=(4, 0))
        self.processing_details = tk.BooleanVar(value=True)
        ttk.Checkbutton(right, text='Внутренние линии лица и тела', variable=self.processing_details).pack(anchor='w', pady=4)
        self.processing_width = tk.IntVar(value=1)
        line_width = ttk.Frame(right)
        line_width.pack(fill='x', pady=4)
        ttk.Label(line_width, text='Толщина линий · пиксели').pack(side='left')
        ttk.Spinbox(line_width, from_=1, to=6, textvariable=self.processing_width, width=3, state='readonly').pack(side='right')
        self.processing_strength = tk.IntVar(value=25)
        ttk.Label(right, text='Очистка фона (больше — сильнее)', foreground=MUTED, wraplength=200).pack(anchor='w')
        ttk.Scale(right, from_=0, to=60, variable=self.processing_strength).pack(fill='x')
        self.processing_threshold = tk.IntVar(value=35)
        ttk.Label(right, text='Порог деталей (меньше — больше линий)', foreground=MUTED, wraplength=200).pack(anchor='w')
        ttk.Scale(right, from_=15, to=100, variable=self.processing_threshold).pack(fill='x')
        ttk.Label(right, text='Нейросеть работает в контурном режиме и сохраняет детали лица. Цвет — в палитре. Модели скачиваются один раз: фон ~168 МБ, линии ~183 МБ. Обработка на твоём ПК.', foreground=MUTED, wraplength=200).pack(anchor='w', pady=4)
        self.processing_button = ttk.Button(right, text='Обработать · PRO', style='Accent.TButton', command=self.request_processing)
        self.processing_button.pack(fill='x', pady=(4, 8))
        self.preview_label = ttk.Label(right, text='Предпросмотр 1:1', foreground=MUTED)
        self.preview_label.pack(anchor='w')
        self.preview = tk.Canvas(right, width=210, height=216, background='#1b1624', highlightthickness=0)
        self.preview.pack(fill='x', pady=(4, 4))
        self.status = ttk.Label(self.frame, text='Ctrl+Z — отменить · Ctrl+Y — повторить · PNG — сохранить арт', foreground=MUTED, wraplength=760)
        self.status.pack(anchor='w', pady=(8, 0))
        app.root.bind('<Control-z>', lambda e: self.shortcut(e, self.undo), add='+')
        app.root.bind('<Control-y>', lambda e: self.shortcut(e, self.redo), add='+')
        self.redraw()
        app.root.after(100, self.poll_processing)

    def shortcut(self, event, action):
        if self.frame.winfo_ismapped() and event.widget.winfo_class() not in ('Entry', 'TEntry', 'TSpinbox'):
            action()
            return 'break'

    def request_processing(self):
        if self.processing_busy:
            return
        if not self.image.getchannel('A').getbbox():
            self.status.configure(text='Сначала импортируй изображение персонажа.')
            return
        self.app.open_catalog()
        self.app.catalog_panel.require_premium(self.start_processing)

    def start_processing(self):
        from image_processing import cutout, contour, neural_lineart
        self.app.open_constructor()
        self.release()
        if getattr(self, 'processing_result', None) is not None:
            result, self.processing_result = self.processing_result, None
            self.replace(self.fit_image(result))
            self.status.configure(text='Результат предыдущей обработки применён. Ctrl+Z отменяет изменение.')
            return
        bounds = self.image.getchannel('A').getbbox()
        if not bounds or self.processing_busy:
            return
        source = self.image.crop(bounds)
        mode = {'Вырезать персонажа': 'cutout', 'Персонаж с обводкой': 'outlined', 'Контурный рисунок': 'lineart'}[self.processing_mode.get()]
        color, width = self.color, self.processing_width.get()
        strength = int(self.processing_strength.get())
        details, threshold = self.processing_details.get(), int(self.processing_threshold.get())
        neural = mode == 'lineart' and self.processing_method.get() == 'Нейросеть · аниме' and details
        self.processing_busy = True
        self.processing_before = self.image.tobytes()
        self.processing_button.configure(state='disabled')
        self.status.configure(text='Подготавливаю обработку персонажа…')
        def work():
            try:
                result = cutout(source, self.app.data, lambda message: self.processing_jobs.put(('progress', message)), strength=strength)
                if neural:
                    result = neural_lineart(result, self.app.data, lambda message: self.processing_jobs.put(('progress', message)), color, width, threshold)
                else:
                    result = contour(result, mode, color, width, details, threshold)
                self.processing_jobs.put(('done', result))
            except Exception:
                self.processing_jobs.put(('error', 'Не удалось обработать персонажа. Проверь интернет для загрузки модели и попробуй другое изображение.'))
        threading.Thread(target=work, name='character-processing', daemon=True).start()

    def poll_processing(self):
        try:
            while True:
                kind, value = self.processing_jobs.get_nowait()
                if kind == 'progress':
                    self.status.configure(text=value)
                    continue
                self.processing_busy = False
                self.processing_button.configure(state='normal')
                if kind == 'error':
                    self.status.configure(text=value)
                elif not value.getchannel('A').getbbox():
                    self.status.configure(text='Персонаж не найден. Попробуй изображение с более чётким фоном.')
                elif self.image.tobytes() != self.processing_before:
                    self.processing_result = value
                    self.status.configure(text='Во время обработки рисунок изменился. Результат сохранён; нажми «Обработать» ещё раз, чтобы применить его.')
                else:
                    self.replace(self.fit_image(value))
                    self.status.configure(text='Готово. Можно исправить детали кистью или ластиком, сохранить PNG и использовать прицел. Ctrl+Z отменяет обработку.')
        except queue.Empty:
            pass
        self.app.root.after(100, self.poll_processing)

    def set_color(self, color):
        self.color = color
        self.color_button.configure(text='Цвет: ' + color.upper())

    def choose_color(self):
        _, color = colorchooser.askcolor(self.color, parent=self.app.root)
        if color:
            self.set_color(color)

    def geometry(self):
        width, height = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        ratio = min(max(1, width - 8) / self.image.width, max(1, height - 8) / self.image.height)
        side = max(1, round(self.image.width * ratio))
        return side, (width - side) // 2, (height - round(self.image.height * ratio)) // 2

    def point(self, event, clamp=False):
        side, left, top = self.geometry()
        x, y = int((event.x - left) * self.image.width / side), int((event.y - top) * self.image.width / side)
        if clamp:
            return max(0, min(self.image.width - 1, x)), max(0, min(self.image.height - 1, y))
        return (x, y) if 0 <= x < self.image.width and 0 <= y < self.image.height else None

    def press(self, event):
        point = self.point(event)
        if point is None:
            return
        self.before = self.image.copy()
        self.start = self.last = point
        self.stroke_tool = self.tool.get()
        self.stroke_width = max(1, min(128, self.width.get()))
        self.stroke_color = ImageColor.getrgb(self.color) + (255,)
        if self.stroke_tool == 'Заливка':
            ImageDraw.floodfill(self.image, point, self.stroke_color)
        elif self.stroke_tool in ('Кисть', 'Ластик'):
            self.brush(point, point)
        self.redraw()

    def brush(self, start, end):
        color = (0, 0, 0, 0) if self.stroke_tool == 'Ластик' else self.stroke_color
        draw = ImageDraw.Draw(self.image)
        draw.line([start, end], fill=color, width=self.stroke_width)
        radius = (self.stroke_width - 1) / 2
        for x, y in (start, end):
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)

    def motion(self, event):
        if self.before is None:
            return
        point = self.point(event, clamp=True)
        tool = self.stroke_tool
        if tool in ('Кисть', 'Ластик'):
            self.brush(self.last, point)
        elif tool in ('Линия', 'Прямоугольник', 'Круг'):
            self.image = self.before.copy()
            draw = ImageDraw.Draw(self.image)
            if tool == 'Линия':
                draw.line([self.start, point], fill=self.stroke_color, width=self.stroke_width)
            else:
                box = (min(self.start[0], point[0]), min(self.start[1], point[1]), max(self.start[0], point[0]), max(self.start[1], point[1]))
                method = draw.rectangle if tool == 'Прямоугольник' else draw.ellipse
                method(box, fill=self.stroke_color if self.filled.get() else None, outline=self.stroke_color, width=self.stroke_width)
        self.last = point
        self.redraw()

    def release(self, event=None):
        if self.before is not None:
            if event is not None and self.stroke_tool != 'Заливка':
                self.motion(event)
            self.commit(self.before)
            self.before = None

    def commit(self, previous):
        if previous.size != self.image.size or previous.tobytes() != self.image.tobytes():
            self.undo_stack.append(previous)
            del self.undo_stack[:-40]
            while len(self.undo_stack) > 1 and sum(p.width * p.height * 4 for p in self.undo_stack) > 64_000_000:
                self.undo_stack.pop(0)
            self.redo_stack.clear()
            self.save_draft()
        self.redraw()

    def replace(self, image):
        self.release()
        previous = self.image
        self.image = image.copy()
        self.commit(previous)

    def undo(self):
        self.release()
        if self.undo_stack:
            self.redo_stack.append(self.image)
            self.image = self.undo_stack.pop()
            self.save_draft()
            self.redraw()

    def redo(self):
        self.release()
        if self.redo_stack:
            self.undo_stack.append(self.image)
            self.image = self.redo_stack.pop()
            self.save_draft()
            self.redraw()

    def clear(self):
        if self.image.getbbox() and not messagebox.askyesno('Очистить холст', 'Удалить рисунок? Его можно будет вернуть кнопкой «Отменить».', parent=self.app.root):
            return
        self.replace(Image.new('RGBA', self.image.size))

    def save_draft(self):
        try:
            temporary = self.folder / 'draft.tmp'
            self.image.save(temporary, format='PNG')
            temporary.replace(self.folder / 'draft.png')
        except OSError:
            self.status.configure(text='Не удалось сохранить черновик. Сохрани рисунок кнопкой PNG.')

    def redraw(self):
        side, left, top = self.geometry()
        self.resolution.set(f'{self.image.width} × {self.image.height}')
        checker = Image.new('RGBA', self.image.size, '#251e32')
        draw = ImageDraw.Draw(checker)
        tile = max(8, self.image.width // 16)
        for y in range(0, self.image.height, tile):
            for x in range(0, self.image.width, tile):
                if (x // tile + y // tile) % 2:
                    draw.rectangle((x, y, x + tile - 1, y + tile - 1), fill='#302639')
        checker.alpha_composite(self.image)
        height = max(1, round(side * self.image.height / self.image.width))
        sampling = Image.Resampling.LANCZOS if side < checker.width else Image.Resampling.NEAREST
        self.photo = ImageTk.PhotoImage(checker.resize((side, height), sampling))
        self.canvas.delete('all')
        self.canvas.create_image(left, top, image=self.photo, anchor='nw')
        if self.guides.get():
            self.canvas.create_line(left + side / 2, top, left + side / 2, top + height, fill='#876ba1', dash=(2, 5))
            self.canvas.create_line(left, top + height / 2, left + side, top + height / 2, fill='#876ba1', dash=(2, 5))
        size = max(8, min(4096, int(self.size.get()))) * 2
        preview_size = min(size, 200)
        source = self.output_image()
        # Match the overlay's supersampling and aspect ratio exactly.
        ratio = preview_size * 4 / max(source.size)
        source = source.resize((max(1, round(source.width * ratio)), max(1, round(source.height * ratio))), Image.Resampling.LANCZOS)
        preview = Image.new('RGBA', (256 * 4, 256 * 4))
        preview.alpha_composite(source, ((preview.width-source.width)//2, (preview.height-source.height)//2))
        preview = preview.resize((256, 256), Image.Resampling.LANCZOS).crop((20, 20, 236, 236))
        self.preview_photo = ImageTk.PhotoImage(preview)
        self.preview.delete('all')
        self.preview.create_image(max(1, self.preview.winfo_width()) / 2, 108, image=self.preview_photo)
        self.size_label.configure(text=f'Размер на экране: {size} px')
        self.preview_label.configure(text='Предпросмотр 1:1' if size <= 200 else f'Предпросмотр уменьшен · {size} px')
        self.undo_button.configure(state='normal' if self.undo_stack else 'disabled')
        self.redo_button.configure(state='normal' if self.redo_stack else 'disabled')

    def use_template(self, name):
        self.replace(self.fit_image(character(name, self.color)))
        self.name.set(name)
        self.status.configure(text='Шаблон готов. Добавляй детали кистью или меняй цвет заливкой.')

    def output_image(self):
        bounds = self.image.getchannel('A').getbbox()
        return self.image.crop(bounds) if self.crop.get() and bounds else self.image.copy()

    def fit_image(self, image):
        result = Image.new('RGBA', self.image.size)
        image = image.convert('RGBA')
        ratio = min(result.width / image.width, result.height / image.height)
        image = image.resize((max(1, round(image.width * ratio)), max(1, round(image.height * ratio))), Image.Resampling.LANCZOS)
        result.alpha_composite(image, ((result.width - image.width) // 2, (result.height - image.height) // 2))
        return result

    def change_canvas(self, _=None):
        self.release()
        width, height = (int(value.strip()) for value in self.resolution.get().split('×'))
        previous = self.image
        self.image = Image.new('RGBA', (width, height))
        self.image = self.fit_image(previous)
        self.commit(previous)

    def resize_overlay(self, factor):
        self.size.set(max(8, min(4096, round(self.size.get() * factor))))
        self.redraw()

    def screen_size(self):
        self.size.set(min(4096, max(self.app.root.winfo_screenwidth(), self.app.root.winfo_screenheight()) // 2))
        self.redraw()

    def import_image(self):
        filename = filedialog.askopenfilename(parent=self.app.root, filetypes=[('Изображения', '*.png *.jpg *.jpeg *.webp *.bmp *.gif')])
        if not filename:
            return
        try:
            with Image.open(filename) as image:
                if image.format == 'GIF':
                    self.app.import_image(filename)
                    self.app.show_settings()
                    return
                if image.width * image.height > 16_000_000:
                    raise ValueError('Изображение слишком большое.')
                self.replace(image.convert('RGBA'))
            self.status.configure(text=f'Импорт без изменения пикселей · {self.image.width} × {self.image.height}. Холст подстроен под изображение.')
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            messagebox.showerror('Импорт изображения', str(error), parent=self.app.root)

    def load_current(self):
        from main import render
        try:
            settings = self.app.settings()
            if settings['style'] == 'Изображение' and settings.get('image'):
                with Image.open(settings['image']) as image:
                    if image.width * image.height > 16_000_000:
                        raise ValueError('Изображение слишком большое.')
                    self.replace(image.convert('RGBA'))
            else:
                self.replace(self.fit_image(render(settings)))
            self.name.set(settings['name'])
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            messagebox.showerror('Редактирование прицела', str(error), parent=self.app.root)

    def export(self):
        if not self.image.getbbox():
            self.status.configure(text='Сначала нарисуй прицел или выбери персонажа.')
            return
        filename = filedialog.asksaveasfilename(parent=self.app.root, defaultextension='.png', filetypes=[('Прозрачное изображение', '*.png')], initialfile='my-crosshair.png')
        if filename:
            try:
                self.output_image().save(filename, format='PNG')
                self.status.configure(text='PNG сохранён с прозрачным фоном.')
            except OSError as error:
                messagebox.showerror('Сохранение PNG', str(error), parent=self.app.root)

    def apply(self):
        self.release()
        if not self.image.getbbox():
            self.status.configure(text='Сначала нарисуй прицел или выбери персонажа.')
            return
        from main import DEFAULT
        try:
            folder = self.app.data / 'images'
            folder.mkdir(exist_ok=True)
            target = folder / (uuid.uuid4().hex + '.png')
            self.output_image().save(target, format='PNG')
            settings = dict(DEFAULT, name=self.name.get().strip()[:80] or 'Мой персонаж', style='Изображение',
                            image=str(target), size=max(8, min(4096, int(self.size.get()))))
            self.app.profiles.append(settings)
            self.app.active = len(self.app.profiles) - 1
            self.app.load_profile()
            self.app.show_settings()
        except (OSError, ValueError) as error:
            messagebox.showerror('Создание прицела', str(error), parent=self.app.root)
