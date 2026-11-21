"""Backup completo: Excel protetto + copia del database, poi copia su altri dischi.

Le destinazioni extra si indicano in impostazioni.ini:
  ASTA_BACKUP_COPIE = E:\\BackupAsta ; \\\\NAS\\backup\\asta ; C:\\Users\\...\\OneDrive\\BackupAsta
"""
import os
import shutil
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Backup Excel + database, con copie sulle destinazioni configurate"

    def add_arguments(self, parser):
        parser.add_argument("--tieni", type=int, default=120)

    def handle(self, *args, **o):
        base = settings.DATI_DIR / "backup"
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M")

        # 1) Excel leggibile da chiunque abbia la password
        call_command("backup_excel", dir=str(base / "excel"), tieni=o["tieni"], stdout=self.stdout)

        # 2) Copia coerente del database (anche mentre il programma è in uso)
        if connection.vendor == "sqlite":
            dbdir = base / "database"
            dbdir.mkdir(parents=True, exist_ok=True)
            tmp = dbdir / f"gestionale_{stamp}.sqlite3"
            src = sqlite3.connect(settings.DATABASES["default"]["NAME"])
            dst = sqlite3.connect(tmp)
            src.backup(dst)
            dst.close(); src.close()
            with zipfile.ZipFile(tmp.with_suffix(".zip"), "w", zipfile.ZIP_DEFLATED) as z:
                z.write(tmp, tmp.name)
            tmp.unlink()
            for f in sorted(dbdir.glob("gestionale_*.zip"))[:-o["tieni"]]:
                f.unlink()
            self.stdout.write(f"Database copiato: {tmp.with_suffix('.zip').name}")

        # 3) Copie su disco esterno / NAS / cartella cloud
        ok = True
        for dest in filter(None, (d.strip() for d in os.environ.get("ASTA_BACKUP_COPIE", "").split(";"))):
            d = Path(dest)
            try:
                if not d.exists():
                    raise FileNotFoundError("cartella non raggiungibile (disco scollegato?)")
                shutil.copytree(base, d / "gestionale-asta", dirs_exist_ok=True)
                self.stdout.write(f"Copiato su {d}")
            except Exception as e:  # un disco mancante non deve bloccare gli altri
                ok = False
                self.stderr.write(f"ATTENZIONE – copia su {d} non riuscita: {e}")

        (settings.DATI_DIR / "ultimo_backup.txt").write_text(
            f"{datetime.now():%d/%m/%Y %H:%M} – {'OK' if ok else 'con avvisi'}\n", encoding="utf-8")
        self.stdout.write(self.style.SUCCESS("Backup completato." if ok else "Backup completato con avvisi."))
