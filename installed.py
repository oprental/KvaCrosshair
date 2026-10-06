"""Local crosshair collection, available without a catalogue account."""
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk


class InstalledPanel:
    def __init__(self, app, parent):
        from main import BG, MUTED
        self.app = app
        self.photos = []
        self.frame = ttk.Frame(parent)
        self.frame.pack(fill='both', expand=True)
        ttk.Label(self.frame, text='Установленные прицелы', font=('Segoe UI', 18, 'bold')).pack(anchor='w')
        ttk.Label(self.frame, text='Твои прицелы из каталога, конструктора и импортированных файлов.', foreground=MUTED, wraplength=730).pack(anchor='w', pady=(4, 12))
        self.search = tk.StringVar()
        ttk.Entry(self.frame, textvariable=self.search).pack(fill='x', pady=(0, 10))
        self.summary = ttk.Label(self.frame, foreground=MUTED)
        self.summary.pack(anchor='w', pady=(0, 8))
        area = ttk.Frame(self.frame)
        area.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(area, bg=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(area, orient='vertical', style='Purple.Vertical.TScrollbar', command=self.canvas.yview)
        scroll.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.cards = ttk.Frame(self.canvas)
        window = self.canvas.create_window((0, 0), window=self.cards, anchor='nw')
        self.cards.bind('<Configure>', lambda _: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda event: self.canvas.itemconfigure(window, width=event.width))
        self.search.trace_add('write', lambda *_: self.draw_cards())
        app.root.bind('<MouseWheel>', self.scroll, add='+')

    def scroll(self, event):
        if self.frame.winfo_ismapped():
            self.canvas.yview_scroll(-int(event.delta / 120), 'units')

    def draw_cards(self):
        from main import render, MUTED, ACCENT
        for child in self.cards.winfo_children():
            child.destroy()
        self.photos.clear()
        query = self.search.get().strip().casefold()
        entries = [(i, p) for i, p in enumerate(self.app.profiles) if query in p['name'].casefold()]
        self.summary.configure(text=f'Сохранено: {len(self.app.profiles)} · Найдено: {len(entries)}')
        if not entries:
            ttk.Label(self.cards, text='Ничего не найдено.', foreground=MUTED).pack(pady=40)
        for column in range(3):
            self.cards.columnconfigure(column, weight=1, uniform='cards')
        for position, (index, settings) in enumerate(entries):
            card = ttk.Frame(self.cards, style='Card.TFrame', padding=14)
            card.grid(row=position // 3, column=position % 3, padx=5, pady=5, sticky='nsew')
            missing = False
            try:
                bitmap = render(settings, 256)
            except (OSError, ValueError):
                bitmap = Image.new('RGBA', (256, 256))
                missing = True
            bitmap.putalpha(bitmap.getchannel('A').point(lambda value: round(value * settings['opacity'] / 100)))
            photo = ImageTk.PhotoImage(bitmap.resize((140, 140), Image.Resampling.LANCZOS))
            self.photos.append(photo)
            ttk.Label(card, image=photo, style='Card.TLabel').pack()
            ttk.Label(card, text=settings['name'] or 'Без названия', style='Card.TLabel', font=('Segoe UI', 11, 'bold'), wraplength=185).pack(anchor='w', pady=(6, 3))
            selected = index == self.app.active
            ttk.Label(card, text='Изображение недоступно' if missing else 'Выбран' if selected else settings['style'], style='Card.TLabel', foreground=ACCENT if selected else MUTED).pack(anchor='w')
            ttk.Button(card, text='Включить', style='Accent.TButton', state='disabled' if missing else 'normal', command=lambda i=index: self.select(i)).pack(fill='x', pady=(10, 4))
            ttk.Button(card, text='Настроить', command=lambda i=index: self.select(i, edit=True)).pack(fill='x')
            ttk.Button(card, text='Удалить', state='disabled' if len(self.app.profiles) == 1 else 'normal', command=lambda i=index: self.remove(i)).pack(fill='x', pady=(4, 0))

    def select(self, index, edit=False):
        self.app.update()
        self.app.active = index
        self.app.load_profile()
        if edit:
            self.app.show_settings()
        else:
            if not self.app.visible:
                self.app.toggle()
            self.draw_cards()

    def remove(self, index):
        if len(self.app.profiles) <= 1:
            return
        name = self.app.profiles[index]['name'] or 'Без названия'
        if not messagebox.askyesno('Удаление прицела', f'Удалить «{name}» из установленных? Публикация в общем каталоге останется.', parent=self.app.root):
            return
        self.app.update()
        self.app.profiles.pop(index)
        if index < self.app.active:
            self.app.active -= 1
        elif index == self.app.active:
            self.app.active = min(index, len(self.app.profiles) - 1)
        self.app.load_profile()
        self.draw_cards()
