#!/bin/bash
cd "$(dirname "$0")"
./venv/bin/python aggiorna.py
echo
echo "Se il programma era aperto, chiudilo e riaprilo con AVVIA-GESTIONALE."
read -p "Premi Invio per chiudere..."
