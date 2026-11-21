"""Backup completo (Excel protetto + database). Pensato per essere lanciato
dall'Utilità di pianificazione di Windows (o da launchd su Mac) due volte al giorno."""
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()
from django.core.management import call_command  # noqa: E402

call_command("backup_completo")
