@echo off
cd /d "%~dp0"
python -m PyInstaller --onefile --windowed --name CrosshairKVA --icon assets\icon.ico --add-data "assets;assets" --distpath dist\release main.py
if errorlevel 1 pause
