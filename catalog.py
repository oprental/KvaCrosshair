"""Catalogue UI, file sharing and an optional remote catalogue."""
import json
import queue
import threading
import time
import webbrowser
import sys
import urllib.request
from urllib.parse import urlsplit
from urllib.error import HTTPError
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import ImageTk
from sharing import MAX_PACKAGE, pack, validate, install, preview_settings
import session_store


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        # A redirect must never forward account data or a session to another host.
        if request.get_method() != 'GET' or request.has_header('Authorization'):
            raise HTTPError(request.full_url, code, 'Перенаправление запроса с данными аккаунта запрещено', headers, fp)
        original, target = urlsplit(request.full_url), urlsplit(newurl)
        if original.scheme == 'https' and target.scheme != 'https':
            raise HTTPError(request.full_url, code, 'Переход с HTTPS на HTTP запрещён', headers, fp)
        if target.scheme not in ('http', 'https') or target.username or target.password or original.hostname != target.hostname:
            raise HTTPError(request.full_url, code, 'Перенаправление на другой сервер запрещено', headers, fp)
        return super().redirect_request(request, fp, code, message, headers, newurl)


class CatalogPanel:
    def __init__(self, app, parent, initial_source='Онлайн'):
        from main import BG, PANEL, MUTED, TEXT, FIELD, ACCENT
        self.app = app
        self.window = app.root
        self.jobs = queue.Queue()
        self.busy = False
        self.entries = []
        self.photos = []
        self.online_entries = None
        self.token = ''
        self.user_id = None
        self.auth_origin = ''
        self.premium = False
        self.premium_action = None
        self.next_subscription_check = 0
        outer = ttk.Frame(parent, padding=(0, 8, 0, 0))
        self.frame = outer
        outer.pack(fill='both', expand=True)
        heading = ttk.Frame(outer)
        heading.pack(fill='x')
        ttk.Label(heading, text='Каталог прицелов', font=('Segoe UI', 18, 'bold')).pack(side='left')
        self.account_button = ttk.Button(heading, text='Войти / Регистрация', command=self.account_action)
        self.account_button.pack(side='right')
        ttk.Button(heading, text='KVA PRO', command=self.open_subscription).pack(side='right', padx=8)
        ttk.Label(outer, text='Выбирай готовые. Делись своими.', foreground=MUTED).pack(anchor='w', pady=(4, 12))
        self.url = tk.StringVar(value='https://kvacrosshair.online')
        self.author = tk.StringVar()
        try:
            config = json.loads((app.data / 'catalog.json').read_text(encoding='utf-8'))
            self.url.set(config.get('url') or self.url.get())
            self.author.set(config.get('author', ''))
        except (OSError, ValueError):
            pass
        if self.url.get().rstrip('/') in ('http://31.59.58.36', 'https://31.59.58.36:8443'):
            self.url.set('https://kvacrosshair.online')
        self.author.set('')
        self.source = tk.StringVar(value=initial_source)
        row = ttk.Frame(outer)
        row.pack(fill='x')
        source = ttk.Combobox(row, textvariable=self.source, values=['Встроенные', 'Онлайн'], state='readonly', width=15)
        source.pack(side='left')
        source.bind('<<ComboboxSelected>>', lambda _: self.change_source())
        self.search = tk.StringVar()
        ttk.Entry(row, textvariable=self.search).pack(side='left', fill='x', expand=True, padx=8)
        ttk.Button(row, text='Обновить', command=self.refresh).pack(side='left')
        self.search.trace_add('write', lambda *_: self.draw_cards())
        self.account_form = ttk.Frame(outer)
        self.subscription_form = ttk.Frame(outer)
        ttk.Label(self.subscription_form, text='KVA PRO · Цвет ника и обработка персонажей', font=('Segoe UI', 12, 'bold')).pack(anchor='w')
        ttk.Label(self.subscription_form, text='Вырезание персонажа, обводка и нейросетевой аниме-лайн. Оплата через ЮKassa, без автоматических списаний.', foreground=MUTED, wraplength=730).pack(anchor='w', pady=(4, 8))
        self.payment_provider = 'yookassa'
        self.payment_url = ''
        self.receipt_email = tk.StringVar()
        email_row = ttk.Frame(self.subscription_form)
        email_row.pack(fill='x', pady=(0, 8))
        ttk.Label(email_row, text='Email для чека:').pack(side='left', padx=(0, 8))
        ttk.Entry(email_row, textvariable=self.receipt_email).pack(side='left', fill='x', expand=True)
        plans = ttk.Frame(self.subscription_form)
        plans.pack(fill='x')
        self.plan_buttons = {}
        for plan, label in [('month', '30 дней · 100 ₽'), ('quarter', '3 месяца · 250 ₽'), ('half', '6 месяцев · 500 ₽'), ('year', 'Год · 900 ₽')]:
            button = ttk.Button(plans, text=label, command=lambda value=plan: self.buy_subscription(value))
            button.pack(side='left', expand=True, fill='x', padx=(0, 5))
            self.plan_buttons[plan] = button
        self.subscription_status = ttk.Label(self.subscription_form, text='Проверяю доступность оплаты…', foreground=MUTED, wraplength=730)
        self.subscription_status.pack(anchor='w', pady=8)
        self.order_code = tk.StringVar()
        ttk.Entry(self.subscription_form, textvariable=self.order_code, state='readonly').pack(fill='x')
        payment_actions = ttk.Frame(self.subscription_form)
        payment_actions.pack(fill='x', pady=6)
        ttk.Button(payment_actions, text='Скопировать код', command=self.copy_order).pack(side='left')
        ttk.Button(payment_actions, text='Перейти к оплате', command=self.open_payment).pack(side='left', padx=5)
        ttk.Button(payment_actions, text='Проверить подписку', command=self.check_subscription).pack(side='left')
        ttk.Button(payment_actions, text='Закрыть', command=self.subscription_form.pack_forget).pack(side='right')
        color_row = ttk.Frame(self.subscription_form)
        color_row.pack(fill='x', pady=(0, 8))
        ttk.Label(color_row, text='Цвет ника:').pack(side='left', padx=(0, 8))
        for color in ('#ffca72', '#b693ff', '#ff81bd', '#65f7a5', '#ffffff'):
            tk.Button(color_row, bg=color, activebackground=color, width=3, relief='flat', command=lambda value=color: self.set_nick_color(value)).pack(side='left', padx=3)
        self.login_name = tk.StringVar()
        self.login_password = tk.StringVar()
        self.repeat_password = tk.StringVar()
        credentials = ttk.Frame(self.account_form)
        credentials.pack(fill='x', pady=(8, 0))
        for label, variable, secret in [('Логин', self.login_name, False), ('Пароль', self.login_password, True), ('Повтор пароля', self.repeat_password, True)]:
            field = ttk.Frame(credentials)
            field.pack(side='left', fill='x', expand=True, padx=(0, 8))
            ttk.Label(field, text=label, foreground=MUTED).pack(anchor='w')
            ttk.Entry(field, textvariable=variable, show='•' if secret else '').pack(fill='x')
        ttk.Label(self.account_form, text='Логин: 3–24 буквы, цифры или _. Пароль: от 10 символов. Повтор нужен для регистрации.', foreground=MUTED, wraplength=790).pack(anchor='w', pady=(4, 8))
        account_actions = ttk.Frame(self.account_form)
        account_actions.pack(fill='x')
        self.login_button = ttk.Button(account_actions, text='Войти', command=lambda: self.submit_auth('login'))
        self.login_button.pack(side='left')
        self.register_button = ttk.Button(account_actions, text='Создать аккаунт', command=lambda: self.submit_auth('register'))
        self.register_button.pack(side='left', padx=8)
        ttk.Button(account_actions, text='Отмена', command=self.hide_account_form).pack(side='left')
        actions = ttk.Frame(outer)
        self.actions = actions
        actions.pack(fill='x', pady=12)
        self.publish_button = ttk.Button(actions, text='Опубликовать текущий', style='Accent.TButton', command=self.publish)
        self.publish_button.pack(side='left')
        ttk.Button(actions, text='Экспорт файла', command=self.export).pack(side='left', padx=8)
        ttk.Button(actions, text='Импорт файла', command=self.import_file).pack(side='left')
        self.status = ttk.Label(outer, text='Выбирай прицел из коллекции или открой онлайн-каталог.', foreground=MUTED, wraplength=820)
        self.status.pack(anchor='w', pady=(0, 12))
        area = ttk.Frame(outer)
        area.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(area, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(area, orient='vertical', style='Purple.Vertical.TScrollbar', command=self.canvas.yview)
        scrollbar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.cards = ttk.Frame(self.canvas)
        self.cards_id = self.canvas.create_window((0, 0), window=self.cards, anchor='nw')
        self.cards.bind('<Configure>', lambda _: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self.cards_id, width=e.width))
        self.window.bind('<MouseWheel>', self.scroll, add='+')
        origin = self.url.get().strip().rstrip('/')
        self.token = session_store.load(self.app.data, origin)
        if self.token:
            self.auth_origin = origin
            self.request('GET', '/api/auth/me')
        self.change_source()
        self.window.after(100, self.poll)

    def account_action(self):
        if self.busy:
            return
        if self.token:
            try:
                self.request('POST', '/api/auth/logout', {})
            except (OSError, ValueError) as error:
                self.status.configure(text=str(error))
        else:
            self.account_form.pack(fill='x', before=self.actions, pady=8)
            self.status.configure(text='Аккаунт нужен только для публикации. Смотреть и устанавливать можно без входа.')

    def hide_account_form(self):
        self.account_form.pack_forget()
        self.login_password.set('')
        self.repeat_password.set('')

    def submit_auth(self, action):
        if self.busy:
            return
        if action == 'register' and self.login_password.get() != self.repeat_password.get():
            self.status.configure(text='Пароли не совпадают.')
            return
        try:
            self.request('POST', '/api/auth/'+action, dict(username=self.login_name.get().strip(), password=self.login_password.get()))
            self.login_password.set('')
            self.repeat_password.set('')
        except (OSError, ValueError) as error:
            self.status.configure(text=str(error))

    def clear_account(self):
        self.token = ''
        self.user_id = None
        self.premium = False
        self.premium_action = None
        self.auth_origin = ''
        try:
            session_store.clear(self.app.data)
        except OSError:
            pass
        self.author.set('')
        self.account_button.configure(text='Войти / Регистрация', style='TButton')
        self.draw_cards()

    def scroll(self, event):
        if self.frame.winfo_ismapped():
            self.canvas.yview_scroll(-int(event.delta/120), 'units')

    def save_config(self):
        (self.app.data / 'catalog.json').write_text(json.dumps(dict(url=self.url.get().strip(), author=self.author.get().strip()), ensure_ascii=False), encoding='utf-8')

    def builtins(self):
        from main import DEFAULT
        specs = [('Classic', 'Крест', '#b693ff', 16, 4, 2, True),
                 ('Mint dot', 'Точка', '#65f7a5', 4, 0, 3, True),
                 ('Orbit', 'Круг', '#d8c5ff', 14, 0, 2, False),
                 ('Precision', 'Крест', '#ffffff', 9, 3, 1, False),
                 ('Amber T', 'Т-образный', '#ffca72', 16, 5, 2, False),
                 ('Rose', 'Крест', '#ff81bd', 12, 5, 2, True)]
        return [pack(dict(DEFAULT, name=n, style=s, color=c, size=z, gap=g, thickness=t, dot=d), 'KVA')
                for n, s, c, z, g, t, d in specs]

    def change_source(self):
        self.entries = self.builtins() if self.source.get() == 'Встроенные' else (self.online_entries or [])
        self.draw_cards()
        if self.source.get() == 'Онлайн':
            self.refresh()

    def draw_cards(self):
        from main import render, PANEL, MUTED
        for child in self.cards.winfo_children():
            child.destroy()
        self.photos.clear()
        query = self.search.get().strip().casefold()
        entries = [p for p in self.entries if query in (p['settings']['name']+' '+p['author']).casefold()]
        if not entries:
            ttk.Label(self.cards, text='Прицелов пока нет.' if not query else 'Ничего не найдено.', foreground=MUTED).pack(pady=40)
        for column in range(3):
            self.cards.columnconfigure(column, weight=1, uniform='cards')
        for index, package in enumerate(entries):
            card = ttk.Frame(self.cards, style='Card.TFrame', padding=16)
            card.grid(row=index//3, column=index%3, padx=5, pady=5, sticky='nsew')
            photo = ImageTk.PhotoImage(render(preview_settings(package), 256).resize((150, 150)))
            self.photos.append(photo)
            ttk.Label(card, image=photo, style='Card.TLabel').pack()
            ttk.Label(card, text=package['settings']['name'], style='Card.TLabel', font=('Segoe UI', 11, 'bold'), wraplength=190).pack(anchor='w', pady=(8, 3))
            nick_color = package.get('nick_color')
            if not package.get('premium') or nick_color not in ('#ffca72', '#b693ff', '#ff81bd', '#65f7a5', '#ffffff'):
                nick_color = MUTED
            ttk.Label(card, text=(package['author'] or 'Без автора') + (' ★' if package.get('premium') else ''), style='Card.TLabel', foreground=nick_color).pack(anchor='w')
            ttk.Button(card, text='Установить', command=lambda p=package: self.install(p)).pack(fill='x', pady=(12, 0))
            if self.source.get() == 'Онлайн' and self.token and self.user_id is not None and package.get('owner_id') == self.user_id and type(package.get('id')) is int:
                ttk.Button(card, text='Удалить публикацию', command=lambda p=package: self.delete_publication(p)).pack(fill='x', pady=(5, 0))
        self.canvas.yview_moveto(0)

    def install(self, package):
        try:
            settings = install(package, self.app.data)
            self.app.profiles.append(settings)
            self.app.active = len(self.app.profiles)-1
            self.app.load_profile()
            self.status.configure(text=f'«{settings["name"]}» добавлен в твои профили.')
        except (OSError, ValueError) as error:
            messagebox.showerror('Не удалось установить', str(error), parent=self.window)

    def export(self):
        try:
            package = pack(self.app.settings(), self.author.get().strip())
            path = filedialog.asksaveasfilename(parent=self.window, defaultextension='.kvacrosshair', filetypes=[('Прицел KVA', '*.kvacrosshair')], initialfile='crosshair.kvacrosshair')
            if path:
                from pathlib import Path
                Path(path).write_text(json.dumps(package, ensure_ascii=False), encoding='utf-8')
                self.status.configure(text='Файл сохранён. Отправь его другу — он сможет импортировать прицел.')
        except (OSError, ValueError) as error:
            messagebox.showerror('Ошибка экспорта', str(error), parent=self.window)

    def import_file(self):
        path = filedialog.askopenfilename(parent=self.window, filetypes=[('Прицел KVA', '*.kvacrosshair')])
        if not path:
            return
        try:
            from pathlib import Path
            with Path(path).open('rb') as stream:
                raw = stream.read(MAX_PACKAGE+1)
            if len(raw) > MAX_PACKAGE:
                raise ValueError('Файл слишком большой')
            self.install(validate(json.loads(raw)))
        except (OSError, ValueError) as error:
            messagebox.showerror('Ошибка импорта', str(error), parent=self.window)

    def request(self, method, endpoint, payload=None):
        url = self.url.get().strip().rstrip('/')
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Не удалось подключиться к каталогу. Перезапусти приложение.')
        self.save_config()
        if method != 'GET' and parsed.scheme != 'https' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
            raise ValueError('Для входа и публикации нужен сервер с HTTPS.')
        if self.token and self.auth_origin != url:
            self.clear_account()
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else None
        headers = {'Content-Type': 'application/json'}
        if self.token and self.auth_origin == url:
            headers['Authorization'] = 'Bearer '+self.token
        request = urllib.request.Request(url+endpoint, data=data, method=method, headers=headers)
        def work():
            try:
                handlers = [SafeRedirectHandler()]
                if parsed.hostname in ('localhost', '127.0.0.1', '::1'):
                    handlers.append(urllib.request.ProxyHandler({}))
                opener = urllib.request.build_opener(*handlers)
                with opener.open(request, timeout=15) as response:
                    raw = response.read(12_000_001)
                if len(raw) > 12_000_000:
                    raise ValueError('Ответ сервера слишком большой')
                result = json.loads(raw)
                if method == 'GET' and endpoint == '/api/crosshairs':
                    result = [dict(validate(p), id=p.get('id'), owner_id=p.get('owner_id'), premium=p.get('premium', False), nick_color=p.get('nick_color')) for p in result['items']]
                self.jobs.put((method, endpoint, url, result, None, None))
            except HTTPError as error:
                try:
                    message = json.loads(error.read(8192)).get('error', str(error))
                except (ValueError, OSError):
                    message = str(error)
                self.jobs.put((method, endpoint, url, None, message, error.code))
            except Exception as error:
                message = str(error) if isinstance(error, ValueError) else 'Не удалось подключиться. Проверь интернет и попробуй снова.'
                self.jobs.put((method, endpoint, url, None, message, None))
        self.busy = True
        self.publish_button.configure(state='disabled')
        self.login_button.configure(state='disabled')
        self.register_button.configure(state='disabled')
        self.status.configure(text='Подключаюсь к аккаунту…' if endpoint.startswith('/api/auth/') else 'Загружаю каталог…' if method == 'GET' else 'Удаляю публикацию…' if method == 'DELETE' else 'Публикую прицел…')
        threading.Thread(target=work, daemon=True).start()

    def refresh(self):
        if self.source.get() == 'Встроенные':
            self.change_source()
            return
        if self.busy:
            return
        try:
            self.request('GET', '/api/crosshairs')
        except (OSError, ValueError) as error:
            self.status.configure(text=str(error))

    def publish(self):
        if self.busy:
            return
        if not self.token:
            self.account_action()
            return
        try:
            package = pack(self.app.settings(), self.author.get().strip())
            if not messagebox.askyesno('Публикация', 'Прицел и имя автора будут доступны всем пользователям общего каталога. Опубликовать?', parent=self.window):
                return
            self.request('POST', '/api/crosshairs', package)
        except (OSError, ValueError) as error:
            messagebox.showerror('Не удалось опубликовать', str(error), parent=self.window)

    def delete_publication(self, package):
        if self.busy:
            return
        if not self.token or self.user_id is None or package.get('owner_id') != self.user_id:
            return
        if not messagebox.askyesno('Удаление публикации', f'Удалить «{package["settings"]["name"]}» из общего каталога? Сохранённые прицелы у тебя и других пользователей останутся.', parent=self.window):
            return
        try:
            self.request('DELETE', '/api/crosshairs/'+str(package['id']))
        except (OSError, ValueError) as error:
            self.status.configure(text=str(error))

    def poll(self):
        try:
            method, endpoint, origin, result, error, status = self.jobs.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.publish_button.configure(state='normal')
            self.login_button.configure(state='normal')
            self.register_button.configure(state='normal')
            if endpoint == '/api/auth/logout':
                self.clear_account()
            if error:
                self.premium_action = None
                if status == 401 and (endpoint.startswith('/api/crosshairs') or endpoint == '/api/auth/me'):
                    self.clear_account()
                    if endpoint != '/api/auth/me':
                        self.account_action()
                self.status.configure(text=str(error))
                if endpoint.startswith('/api/subscription/'):
                    self.subscription_status.configure(text=str(error))
                if endpoint == '/api/auth/me':
                    self.refresh()
            elif endpoint == '/api/subscription/plans':
                self.payment_provider = result.get('provider','yookassa')
                for button in self.plan_buttons.values():
                    button.configure(state='normal' if result['enabled'] else 'disabled')
                self.subscription_status.configure(text='Укажи email для чека и выбери срок. Затем открой страницу ЮKassa. Подписка активируется после подтверждения оплаты.' if result['enabled'] else 'Оплата временно недоступна. Попробуй позже.')
            elif endpoint == '/api/subscription/order':
                self.order_code.set(result['code'])
                self.payment_url = result.get('url','')
                self.next_subscription_check = time.monotonic() + 30
                self.subscription_status.configure(text=f'{result["amount"]} ₽ за {result["period"]}. Нажми «Перейти к оплате»: сумма и аккаунт уже привязаны к заказу. После оплаты PRO включится автоматически.')
            elif endpoint in ('/api/auth/register', '/api/auth/login', '/api/auth/me', '/api/subscription/color'):
                if origin == self.url.get().strip().rstrip('/'):
                    if endpoint in ('/api/auth/register', '/api/auth/login'):
                        self.token = result['token']
                    self.user_id = result['user']['id']
                    self.auth_origin = origin
                    self.author.set(result['user']['username'])
                    self.premium = bool(result['user'].get('premium'))
                    nick_color = result['user'].get('nick_color', '#ffca72')
                    ttk.Style().configure('Premium.TButton', foreground=nick_color)
                    self.account_button.configure(text=self.author.get()+(' ★' if self.premium else '')+' · Выйти', style='Premium.TButton' if self.premium else 'TButton')
                    self.hide_account_form()
                    self.draw_cards()
                    self.status.configure(text='Ты вошёл как '+self.author.get()+'. Теперь можно публиковать прицелы.')
                    if self.premium:
                        expires = time.strftime('%d.%m.%Y', time.localtime(result['user']['premium_until']))
                        self.subscription_status.configure(text='KVA PRO активна до '+expires+'. Цвет ника и обработка изображений доступны.')
                    elif self.subscription_form.winfo_ismapped() and self.order_code.get():
                        self.subscription_status.configure(text='Платёж пока не подтверждён. Заверши оплату на странице ЮKassa и повтори проверку через несколько секунд.')
                    try:
                        session_store.save(self.app.data, origin, self.token)
                    except OSError:
                        self.status.configure(text='Вход выполнен, но сохранить его для следующего запуска не удалось.')
                    action = self.premium_action
                    self.premium_action = None
                    if action:
                        if self.premium:
                            action()
                        else:
                            self.open_subscription()
                            self.status.configure(text='Обработка персонажей доступна с KVA PRO.')
                    if endpoint == '/api/auth/me':
                        self.refresh()
            elif endpoint == '/api/auth/logout':
                self.status.configure(text='Ты вышел из аккаунта. Каталог остаётся доступным.')
            elif method == 'DELETE':
                self.online_entries = [p for p in (self.online_entries or []) if p.get('id') != result['id']]
                if self.source.get() == 'Онлайн':
                    self.entries = self.online_entries
                    self.draw_cards()
                self.status.configure(text='Публикация удалена из общего каталога. Сохранённые профили остались.')
            elif method == 'GET':
                self.online_entries = result
                if self.source.get() == 'Онлайн':
                    self.entries = result
                    self.draw_cards()
                self.status.configure(text=f'Загружено прицелов: {len(result)}. Показаны последние публикации.')
            else:
                self.status.configure(text='Прицел опубликован. Открой онлайн-каталог и нажми «Обновить».')
        if self.subscription_form.winfo_ismapped() and self.order_code.get() and self.token and not self.busy and time.monotonic() >= self.next_subscription_check:
            self.next_subscription_check = time.monotonic() + 30
            self.request('GET', '/api/auth/me')
        self.window.after(100, self.poll)

    def open_subscription(self):
        self.subscription_form.pack(fill='x', before=self.actions, pady=8)
        if not self.busy:
            self.request('GET', '/api/subscription/plans')

    def buy_subscription(self, plan):
        if self.busy:
            return
        if not self.token:
            self.account_action()
            return
        if self.payment_provider != 'yookassa':
            self.subscription_status.configure(text='Оплата через ЮKassa пока не подключена к серверу.')
            return
        from yookassa_payments import receipt_email
        try:
            email=receipt_email(self.receipt_email.get())
        except ValueError as error:
            self.subscription_status.configure(text=str(error))
            return
        self.request('POST', '/api/subscription/order', {'plan':plan,'email':email,'provider':'yookassa'})

    def copy_order(self):
        if self.order_code.get():
            self.window.clipboard_clear()
            self.window.clipboard_append(self.order_code.get())

    def open_payment(self):
        if self.order_code.get() and self.payment_url:
            from yookassa_payments import confirmation_url
            try:
                webbrowser.open(confirmation_url(self.payment_url))
            except ValueError as error:
                self.subscription_status.configure(text=str(error))

    def check_subscription(self):
        if not self.busy:
            if self.token:
                self.request('GET', '/api/auth/me')
            else:
                self.account_action()

    def set_nick_color(self, color):
        if not self.busy:
            if self.token:
                self.request('POST', '/api/subscription/color', {'color': color})
            else:
                self.account_action()

    def require_premium(self, action):
        from updater import ORIGIN
        if getattr(sys, 'frozen', False) and self.url.get().strip().rstrip('/') != ORIGIN:
            self.clear_account()
            self.url.set(ORIGIN)
            self.status.configure(text='Войди в аккаунт KVA PRO, чтобы обработать персонажа.')
            self.account_action()
            return
        if self.busy:
            self.window.after(150, lambda: self.require_premium(action))
            return
        if not self.token:
            self.account_action()
            self.status.configure(text='Войди в аккаунт с KVA PRO, чтобы обработать персонажа.')
            return
        self.premium_action = action
        self.request('GET', '/api/auth/me')
