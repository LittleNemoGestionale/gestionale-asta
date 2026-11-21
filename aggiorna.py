"""Aggiorna il gestionale all'ultima versione pubblicata (senza toccare la cartella «dati»).

Uso:  doppio clic su AGGIORNA.bat (Windows) / AGGIORNA.command (Mac),
      oppure in automatico all'avvio (avvia.py), se è attivo ASTA_AGGIORNA_ALL_AVVIO.

Passi:
  1. controlla se c'è una versione nuova (archivio git del programma);
  2. se sì: backup completo (Excel + database), scarica la nuova versione, installa le librerie,
     aggiorna il database alla nuova struttura;
  3. se qualcosa va storto torna alla versione di prima e lo scrive in dati/aggiornamenti.log.
Senza internet non fa niente e il programma parte con la versione che c'è.
"""
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATI = Path(os.environ.get("ASTA_DATI_DIR", BASE / "dati"))
LOG = DATI / "aggiornamenti.log"


def scrivi(msg: str):
    riga = f"{datetime.now():%d/%m/%Y %H:%M} {msg}"
    print(riga)
    DATI.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(riga + "\n")


def git(*args, timeout=120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=BASE, capture_output=True, text=True, timeout=timeout)


def versione_attuale() -> str:
    r = git("log", "-1", "--format=%h del %cd", "--date=format:%d/%m/%Y")
    return r.stdout.strip() if r.returncode == 0 else "sconosciuta"


def django_comando(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "manage.py", *args], cwd=BASE, capture_output=True, text=True,
                          timeout=900)


def aggiorna(silenzioso=False) -> bool:
    """Restituisce True se è stata installata una versione nuova."""
    if not (BASE / ".git").exists():
        scrivi("Aggiornamento automatico non disponibile: il programma non è stato installato dall'archivio.")
        return False
    ramo = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "main"
    if git("fetch", "--quiet", "origin", ramo).returncode != 0:
        if not silenzioso:
            scrivi("Nessun collegamento all'archivio degli aggiornamenti (internet assente?): resta la versione attuale.")
        return False
    prima = git("rev-parse", "HEAD").stdout.strip()
    nuova = git("rev-parse", f"origin/{ramo}").stdout.strip()
    if prima == nuova:
        if not silenzioso:
            scrivi(f"Il programma è già aggiornato ({versione_attuale()}).")
        return False
    if os.name == "nt":
        git("config", "core.fileMode", "false")  # Windows non ha i permessi «eseguibile» dei file
    modificati = git("status", "--porcelain", "--untracked-files=no").stdout.strip()
    if modificati:
        # file del programma cambiati sul posto (fine riga, copie da zip…): li salvo e rimetto quelli originali.
        # La cartella «dati» non è mai toccata.
        copia = DATI / f"modifiche-locali-{datetime.now():%Y%m%d-%H%M}.patch"
        copia.write_text(git("diff").stdout, encoding="utf-8")
        git("reset", "--hard", "HEAD")
        scrivi(f"Alcuni file del programma erano diversi dall'originale: ripristinati (copia in {copia.name}).")

    scrivi(f"Trovata una versione nuova: aggiorno da {prima[:7]} a {nuova[:7]}.")
    b = django_comando("backup_completo")
    if b.returncode != 0:
        scrivi("Aggiornamento annullato: il backup di sicurezza non è riuscito.\n" + b.stderr[-800:])
        return False
    try:
        r = git("merge", "--ff-only", f"origin/{ramo}")
        if r.returncode != 0:
            raise RuntimeError("scaricamento della nuova versione non riuscito: " + r.stderr[-400:])
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", "requirements.txt"], cwd=BASE,
                           capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            raise RuntimeError("installazione delle librerie non riuscita: " + r.stderr[-400:])
        for cmd in (("migrate", "--noinput"), ("collectstatic", "--noinput"), ("crea_ruoli",)):
            r = django_comando(*cmd)
            if r.returncode != 0:
                raise RuntimeError(f"passo «{cmd[0]}» non riuscito: " + r.stderr[-600:])
    except Exception as e:  # torna alla versione di prima
        git("reset", "--hard", prima)
        scrivi(f"ERRORE durante l'aggiornamento, ripristinata la versione precedente. Dettaglio: {e}")
        scrivi("I dati sono al sicuro; se il database era già stato modificato, l'ultimo backup è in dati/backup.")
        return False
    scrivi(f"Aggiornamento completato: ora la versione è {versione_attuale()}.")
    return True


if __name__ == "__main__":
    aggiorna()
    if os.name == "nt" and sys.stdin.isatty():
        input("Premi Invio per chiudere…")
