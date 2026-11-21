"""«Aggiorna adesso»: installa una versione nuova del programma senza chiudere la finestra nera."""
import os
import sys
import threading
import time
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import redirect, render

BASE = Path(settings.BASE_DIR)


def _aggiorna_mod():
    if str(BASE) not in sys.path:
        sys.path.insert(0, str(BASE))
    import aggiorna
    return aggiorna


def stato_versioni() -> dict:
    """Confronta la versione installata con quella pubblicata (serve internet)."""
    ag = _aggiorna_mod()
    if not (BASE / ".git").exists():
        return {"errore": "Il programma non è collegato all'archivio degli aggiornamenti."}
    ramo = ag.git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "main"
    if ag.git("fetch", "--quiet", "origin", ramo, timeout=60).returncode != 0:
        return {"errore": "Non riesco a controllare gli aggiornamenti (internet assente?)."}
    novita = ag.git("log", "--format=%h|%cd|%s", "--date=format:%d/%m %H:%M", f"HEAD..origin/{ramo}").stdout
    return {"attuale": ag.versione_attuale(),
            "novita": [r.split("|", 2) for r in novita.strip().splitlines() if r.count("|") >= 2]}


def _riavvia():
    time.sleep(1.5)  # il tempo di mandare la pagina al browser
    os._exit(3)  # avvia.py vede il codice 3: aggiorna e riparte


@user_passes_test(lambda u: u.is_superuser)
def aggiorna_programma(request):
    supervisore = os.environ.get("ASTA_SUPERVISORE") == "1"
    if request.method == "POST":
        if not supervisore:
            messages.error(request, "Per questa volta chiudi la finestra nera e riapri AVVIA-GESTIONALE: "
                                    "dalla prossima il pulsante funziona.")
            return redirect("aggiorna_programma")
        threading.Thread(target=_riavvia, daemon=True).start()
        return render(request, "asta/aggiorna_in_corso.html")
    return render(request, "asta/aggiorna.html", {**stato_versioni(), "supervisore": supervisore})
