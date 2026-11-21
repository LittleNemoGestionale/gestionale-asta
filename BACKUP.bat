@echo off
cd /d "%~dp0"
venv\Scripts\python backup.py >> dati\backup.log 2>&1
