"""Signed releases, background download and Windows replacement after exit."""
import base64
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import urllib.request
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

VERSION = '0.10.0'
ORIGIN = 'https://kvacrosshair.online'
PUBLIC_KEY = bytes.fromhex('2730eb5513f11ade7b919c8668fa2c5fe62ff867b8d1a78a423325e55081d691')
MAX_SIZE = 128 * 1024 * 1024


def version_tuple(value):
    parts = value.split('.')
    if len(parts) != 3 or any(not p.isascii() or not p.isdigit() or len(p) > 6 for p in parts):
        raise ValueError('Invalid version')
    return tuple(map(int, parts))


def verify_manifest(raw, public_key=PUBLIC_KEY):
    envelope = json.loads(raw)
    release = envelope['release']
    payload = json.dumps(release, sort_keys=True, separators=(',', ':')).encode('utf-8')
    Ed25519PublicKey.from_public_bytes(public_key).verify(base64.b64decode(envelope['signature'], validate=True), payload)
    version_tuple(release['version'])
    if release['url'] != ORIGIN + '/downloads/CrosshairKVA.exe':
        raise ValueError('Unexpected download address')
    if type(release['size']) is not int or not 2 <= release['size'] <= MAX_SIZE:
        raise ValueError('Invalid release size')
    digest = release['sha256']
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('Invalid checksum')
    return release


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Update redirects are not allowed')


def file_matches(path, release):
    if not path.is_file() or path.stat().st_size != release['size']:
        return False
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() == release['sha256']


def download_release(opener, release, folder, progress=lambda _: None):
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / ('CrosshairKVA-' + release['version'] + '.exe')
    if file_matches(target, release):
        return target
    partial = target.with_suffix('.part')
    received, digest = 0, hashlib.sha256()
    try:
        with opener.open(release['url'], timeout=30) as response, partial.open('wb') as stream:
            while chunk := response.read(256 * 1024):
                received += len(chunk)
                if received > release['size']:
                    raise ValueError('Download exceeds signed size')
                digest.update(chunk)
                stream.write(chunk)
                progress(min(100, received * 100 // release['size']))
        if received != release['size'] or digest.hexdigest() != release['sha256']:
            raise ValueError('Download verification failed')
        with partial.open('rb') as stream:
            if stream.read(2) != b'MZ':
                raise ValueError('Invalid executable')
        partial.replace(target)
        return target
    finally:
        partial.unlink(missing_ok=True)


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def cleanup_updates(folder, current_version, executable):
    """Remove only recognized old downloads and a verified successful install."""
    folder, executable = Path(folder).resolve(), Path(executable).resolve()
    if not folder.is_dir():
        return
    current = version_tuple(current_version)
    def remove_file(path):
        try:
            if path.is_file() and not path.is_symlink():
                path.unlink()
            return not path.exists()
        except OSError:
            return False
    for path in folder.iterdir():
        if not path.name.startswith('CrosshairKVA-') or path.suffix not in ('.exe', '.part'):
            continue
        try:
            downloaded = version_tuple(path.stem[len('CrosshairKVA-'):])
        except ValueError:
            continue
        if downloaded <= current and not path.is_symlink() and path.resolve() != executable:
            remove_file(path)
    receipt_path = folder / 'installed.json'
    try:
        if receipt_path.is_symlink():
            return
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text(encoding='utf-8-sig'))
        else:
            # Earlier installers did not write a receipt. Their literal
            # target and checksum still prove this executable was installed.
            script = (folder / 'install-update.ps1').read_text(encoding='utf-8-sig')
            def literal(name):
                match = re.search(r"(?m)^\$" + name + r" = '((?:[^'\r\n]|'')*)'\r?$", script)
                if not match:
                    raise ValueError('Invalid installer state')
                return match.group(1).replace("''", "'")
            if Path(literal('updateFile')).resolve() != folder / ('CrosshairKVA-' + current_version + '.exe'):
                return
            receipt = dict(version=current_version, executable=literal('appFile'), sha256=literal('expectedHash'))
        if receipt['version'] != current_version or Path(receipt['executable']).resolve() != executable:
            return
        with executable.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != receipt['sha256']:
                return
        if not receipt_path.exists():
            receipt_path.write_text(json.dumps(receipt), encoding='utf-8')
    except (OSError, ValueError, KeyError, TypeError):
        return
    # This runs only after the new application's UI has initialized. Rollback
    # is retained if the new executable failed to launch or validate.
    cleaned = remove_file(Path(str(executable) + '.previous'))
    for name in ('install-update.ps1', 'install-helper.log', 'install-error.txt'):
        cleaned = remove_file(folder / name) and cleaned
    if cleaned:
        remove_file(receipt_path)


def install_after_exit(target, release, current, folder, pid):
    if not file_matches(target, release) or Path(current).resolve() == target.resolve():
        raise ValueError('Invalid update file')
    script = folder / 'install-update.ps1'
    backup = Path(str(current) + '.previous')
    log = folder / 'install-error.txt'
    receipt = folder / 'installed.json'
    # Literal PowerShell arguments keep quotes, spaces and Unicode paths safe.
    script.write_text(f'''$ErrorActionPreference = 'Stop'
$env:PYINSTALLER_RESET_ENVIRONMENT = '1'
$updateFile = {ps_quote(target)}
$appFile = {ps_quote(current)}
$backupFile = {ps_quote(backup)}
$expectedHash = {ps_quote(release['sha256'])}
$replaced = $false
try {{
    $runningApp = Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue
    if ($runningApp -and -not $runningApp.WaitForExit(120000)) {{ throw 'Application did not exit' }}
    if ((Get-FileHash -LiteralPath $updateFile -Algorithm SHA256).Hash.ToLower() -ne $expectedHash) {{ throw 'Checksum mismatch' }}
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    while ($true) {{
        try {{
            if (-not $replaced) {{ Copy-Item -LiteralPath $appFile -Destination $backupFile -Force }}
            $replaced = $true
            Copy-Item -LiteralPath $updateFile -Destination $appFile -Force
            break
        }} catch {{
            if ([DateTime]::UtcNow -ge $deadline) {{ throw }}
            Start-Sleep -Milliseconds 300
        }}
    }}
    if ((Get-FileHash -LiteralPath $appFile -Algorithm SHA256).Hash.ToLower() -ne $expectedHash) {{ throw 'Installed checksum mismatch' }}
    @{{ version = {ps_quote(release['version'])}; sha256 = $expectedHash; executable = $appFile }} | ConvertTo-Json | Set-Content -LiteralPath {ps_quote(receipt)} -Encoding UTF8
    Start-Process -FilePath $appFile
}} catch {{
    if ($replaced -and (Test-Path -LiteralPath $backupFile)) {{ Copy-Item -LiteralPath $backupFile -Destination $appFile -Force }}
    $_.Exception.Message | Set-Content -LiteralPath {ps_quote(log)}
    if ((-not $runningApp -or $runningApp.HasExited) -and (Test-Path -LiteralPath $appFile)) {{ Start-Process -FilePath $appFile }}
}}
''', encoding='utf-8-sig')
    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    environment = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1')
    # PyInstaller changes the Windows DLL search directory. A system helper
    # must not depend on the application's temporary unpacked libraries.
    import ctypes
    kernel32 = ctypes.windll.kernel32
    kernel32.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
    kernel32.SetDllDirectoryW(None)
    try:
        with (folder / 'install-helper.log').open('wb') as output:
            subprocess.Popen([str(powershell), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                             env=environment, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                             creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)
    finally:
        if getattr(sys, '_MEIPASS', None):
            kernel32.SetDllDirectoryW(sys._MEIPASS)


class UpdateController:
    def __init__(self, app, label, button):
        self.app, self.label, self.button = app, label, button
        self.events = queue.Queue()
        self.busy = False
        self.ready = None
        self.button.configure(command=self.action)
        self.app.root.after(200, self.poll)
        if getattr(sys, 'frozen', False):
            def cleanup():
                import time
                for _ in range(10):
                    cleanup_updates(self.app.data / 'updates', VERSION, sys.executable)
                    if not (self.app.data / 'updates/installed.json').exists():
                        break
                    time.sleep(.3)
            threading.Thread(target=cleanup, daemon=True).start()
            self.app.root.after(2000, self.check)

    def action(self):
        if self.ready:
            from tkinter import messagebox
            if not messagebox.askyesno('Обновление готово', 'Перезапустить приложение и установить новую версию? Прицел временно выключится. Настройки сохранятся.', parent=self.app.root):
                return
            target, release = self.ready
            try:
                self.app.update()
                install_after_exit(target, release, Path(sys.executable), self.app.data / 'updates', os.getpid())
            except (OSError, ValueError):
                messagebox.showerror('Обновление', 'Не удалось установить обновление. Скачай новую версию с сайта.', parent=self.app.root)
                return
            self.app.close()
        else:
            self.check()

    def check(self):
        if self.busy:
            return
        if not getattr(sys, 'frozen', False):
            self.label.configure(text='Обновления доступны в EXE-версии')
            return
        self.busy = True
        self.button.configure(state='disabled')
        self.label.configure(text='Проверяю обновления…')
        def work():
            try:
                opener = urllib.request.build_opener(NoRedirect())
                with opener.open(ORIGIN + '/downloads/latest.json', timeout=15) as response:
                    raw = response.read(16385)
                if len(raw) > 16384:
                    raise ValueError('Manifest exceeds limit')
                release = verify_manifest(raw)
                if version_tuple(release['version']) <= version_tuple(VERSION):
                    self.events.put(('current', None))
                    return
                target = download_release(opener, release, self.app.data / 'updates',
                                          lambda percent: self.events.put(('progress', percent)))
                self.events.put(('ready', (target, release)))
            except Exception:
                self.events.put(('error', None))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'progress':
                self.label.configure(text=f'Скачиваю обновление: {value}%')
                continue
            self.busy = False
            self.button.configure(state='normal')
            if kind == 'ready':
                self.ready = value
                self.label.configure(text='Версия ' + value[1]['version'] + ' скачана')
                self.button.configure(text='Перезапустить и обновить')
            elif kind == 'current':
                self.label.configure(text='Установлена последняя версия · ' + VERSION)
            else:
                self.label.configure(text='Обновление недоступно. Попробуй позже.')
        self.app.root.after(200, self.poll)
