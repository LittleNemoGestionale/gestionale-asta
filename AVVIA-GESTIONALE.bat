@echo off
title Gestionale Asta
cd /d "%~dp0"
where py >nul 2>nul || where python >nul 2>nul || (
  echo Python non e' installato: vedi docs\INSTALLAZIONE-WINDOWS.md, punto 1.
  pause
  exit /b 1
)
if not exist venv (
  echo Prima installazione: preparo il programma, ci vuole qualche minuto...
  py -V:3.13 -m venv venv || py -3 -m venv venv || python -m venv venv
  venv\Scripts\python -m pip install -r requirements.txt
)
venv\Scripts\python avvia.py
pause
