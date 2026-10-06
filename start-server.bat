@echo off
cd /d "%~dp0"
python catalog_server.py
if errorlevel 1 pause
