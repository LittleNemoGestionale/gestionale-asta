@echo off
title Gestionale Asta
cd /d "%~dp0"
if not exist venv (
  echo Prima installazione: preparo il programma...
  py -3 -m venv venv || python -m venv venv
  venv\Scripts\pip install -r requirements.txt
)
venv\Scripts\python avvia.py
pause
