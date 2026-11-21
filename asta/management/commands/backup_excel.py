"""Crea un backup Excel protetto da password e tiene solo gli ultimi N.

Uso:  ASTA_BACKUP_PASSWORD=... python manage.py backup_excel --dir /Volumes/Backup/asta --tieni 60
"""
import os
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from asta.export_excel import esporta_bytes


class Command(BaseCommand):
    help = "Esporta tutto il gestionale in un file Excel protetto da password"

    def add_arguments(self, parser):
        parser.add_argument("--dir", default=os.environ.get("ASTA_BACKUP_DIR", "backup"))
        parser.add_argument("--tieni", type=int, default=60, help="quanti backup conservare")
        parser.add_argument("--senza-password", action="store_true", help="solo per prove con dati finti")

    def handle(self, *args, **o):
        password = os.environ.get("ASTA_BACKUP_PASSWORD")
        if not password and not o["senza_password"]:
            raise CommandError("Imposta ASTA_BACKUP_PASSWORD (il file contiene dati personali).")
        cartella = Path(o["dir"])
        cartella.mkdir(parents=True, exist_ok=True)
        dest = cartella / f"gestionale-asta_{datetime.now():%Y-%m-%d_%H%M}.xlsx"
        tmp = dest.with_suffix(".tmp")
        tmp.write_bytes(esporta_bytes(None if o["senza_password"] else password))
        tmp.replace(dest)  # scrittura atomica: mai un file a metà
        vecchi = sorted(cartella.glob("gestionale-asta_*.xlsx"))[:-o["tieni"]]
        for f in vecchi:
            f.unlink()
        self.stdout.write(self.style.SUCCESS(f"Backup creato: {dest} ({dest.stat().st_size // 1024} KB)"))
