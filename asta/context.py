import subprocess
from functools import lru_cache

from django.conf import settings


@lru_cache(maxsize=1)
def versione_programma() -> str:
    try:
        r = subprocess.run(["git", "log", "-1", "--format=%h del %cd", "--date=format:%d/%m/%Y"],
                           cwd=settings.BASE_DIR, capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def casa_asta(request):
    f = settings.DATI_DIR / "ultimo_backup.txt"
    try:
        ultimo = f.read_text(encoding="utf-8").strip()
    except OSError:
        ultimo = "mai eseguito"
    return {"casa_asta_nome": settings.CASA_ASTA_NOME, "ultimo_backup": ultimo, "versione": versione_programma()}
