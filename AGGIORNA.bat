@echo off
title Aggiornamento Gestionale Asta
cd /d "%~dp0"
echo Controllo se c'e una versione nuova...
venv\Scripts\python aggiorna.py
echo.
echo Se il programma era aperto, chiudi la finestra nera e riaprilo con AVVIA-GESTIONALE.
pause
