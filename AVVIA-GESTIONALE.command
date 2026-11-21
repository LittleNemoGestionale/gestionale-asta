#!/bin/bash
cd "$(dirname "$0")"
if [ ! -d venv ]; then
  echo "Prima installazione: preparo il programma..."
  python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
fi
./venv/bin/python avvia.py
