"""Avvia il gestionale sul computer dell'ufficio.

Doppio clic su  AVVIA-GESTIONALE.bat (Windows)  oppure  AVVIA-GESTIONALE.command (Mac).

Al primo avvio:
  - crea la cartella "dati" con il database e il file impostazioni.ini
  - genera le chiavi di cifratura e la password dei backup (DA STAMPARE E CONSERVARE)
  - chiede di creare l'utente amministratore
Poi resta acceso e gli altri computer dell'ufficio si collegano dal browser.
"""
import base64
import os
import secrets
import socket
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATI = Path(os.environ.get("ASTA_DATI_DIR", BASE / "dati"))
INI = DATI / "impostazioni.ini"
PORTA = int(os.environ.get("ASTA_PORTA", "8000"))
RIAVVIA = 3  # codice d'uscita del server quando si chiede «Aggiorna adesso» dal programma


def ip_locale():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def primo_avvio():
    DATI.mkdir(parents=True, exist_ok=True)
    nome = input("Nome della casa d'aste (come apparirà sui documenti): ").strip() or "Casa d'Aste"
    pwd_backup = "-".join(secrets.token_hex(3) for _ in range(4))
    valori = {
        "CASA_ASTA_NOME": nome,
        "DJANGO_SECRET_KEY": secrets.token_urlsafe(40),
        "ASTA_FIELD_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        "ASTA_INDEX_KEY": secrets.token_urlsafe(32),
        "ASTA_BACKUP_PASSWORD": pwd_backup,
        "ASTA_BACKUP_COPIE": "",
        "DJANGO_DEBUG": "0",
        "DJANGO_ALLOWED_HOSTS": "*",
        "ASTA_AGGIORNA_ALL_AVVIO": "1",
    }
    righe = ["# Impostazioni del gestionale. NON condividere questo file.",
             "# ASTA_BACKUP_COPIE: cartelle dove copiare i backup, separate da ;",
             "#   es.  E:\\BackupAsta ; \\\\NAS\\backup ; C:\\Users\\ufficio\\OneDrive\\BackupAsta",
             "[gestionale]"] + [f"{k} = {v}" for k, v in valori.items()]
    INI.write_text("\n".join(righe) + "\n", encoding="utf-8")
    foglio = DATI / "DA-STAMPARE-E-CANCELLARE.txt"
    foglio.write_text(
        "GESTIONALE ASTA – CHIAVI DI EMERGENZA\n"
        "Stampare, conservare in cassaforte, poi CANCELLARE questo file.\n"
        "Senza queste chiavi i backup e i dati personali non si possono recuperare.\n\n"
        f"Password backup Excel:  {pwd_backup}\n"
        f"ASTA_FIELD_KEY:         {valori['ASTA_FIELD_KEY']}\n"
        f"ASTA_INDEX_KEY:         {valori['ASTA_INDEX_KEY']}\n", encoding="utf-8")
    print(f"\n*** Ho creato {foglio}\n*** STAMPALO, mettilo in cassaforte e poi cancellalo. ***\n")
    return True


def aggiornamento_all_avvio():
    """Se c'è una versione nuova del programma la installa prima di partire (backup incluso)."""
    import configparser
    cp = configparser.ConfigParser()
    cp.optionxform = str
    if INI.exists():
        cp.read(INI, encoding="utf-8")
    attivo = os.environ.get("ASTA_AGGIORNA_ALL_AVVIO") or (cp["gestionale"].get("ASTA_AGGIORNA_ALL_AVVIO", "1")
                                                          if cp.has_section("gestionale") else "1")
    if attivo.strip() not in ("0", "no", "false"):
        try:
            import aggiorna
            aggiorna.aggiorna(silenzioso=True)
        except Exception as e:  # un problema di aggiornamento non deve impedire l'avvio
            print(f"(controllo aggiornamenti non riuscito: {e})")


def main():
    """Controlla gli aggiornamenti, poi tiene acceso il server; se dal programma si preme
    «Aggiorna adesso» il server si ferma, si installa la versione nuova e riparte da solo."""
    nuovo = not INI.exists() and primo_avvio()
    if not nuovo:
        aggiornamento_all_avvio()
    env = {**os.environ, "ASTA_SUPERVISORE": "1"}
    while True:
        r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--server"]
                           + (["--nuovo"] if nuovo else []), cwd=BASE, env=env)
        nuovo = False
        if r.returncode != RIAVVIA:
            return r.returncode
        print("\nAggiornamento richiesto dal programma: installo la versione nuova e riparto…\n")
        try:
            import aggiorna
            aggiorna.aggiorna()
        except Exception as e:  # se l'aggiornamento non riesce si riparte con la versione di prima
            print(f"(aggiornamento non riuscito: {e})")


def server(nuovo=False):
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    sys.path.insert(0, str(BASE))
    import django
    django.setup()
    from django.core.management import call_command
    call_command("migrate", verbosity=0)
    call_command("collectstatic", interactive=False, verbosity=0)
    call_command("crea_ruoli", verbosity=0)
    from django.contrib.auth.models import User
    if nuovo or not User.objects.filter(is_superuser=True).exists():
        print("Crea l'utente amministratore:")
        call_command("createsuperuser", interactive="DJANGO_SUPERUSER_PASSWORD" not in os.environ,
                     **({"username": os.environ["DJANGO_SUPERUSER_USERNAME"], "email": ""}
                        if "DJANGO_SUPERUSER_PASSWORD" in os.environ else {}))

    from waitress import serve

    from config.wsgi import application
    ip = ip_locale()
    print("\n" + "=" * 60)
    print("  GESTIONALE ASTA ACCESO – non chiudere questa finestra")
    print(f"  Da questo computer:     http://localhost:{PORTA}")
    print(f"  Dagli altri computer:   http://{ip}:{PORTA}")
    print("=" * 60 + "\n")
    serve(application, host="0.0.0.0", port=PORTA, threads=8)


if __name__ == "__main__":
    if "--server" in sys.argv:
        server(nuovo="--nuovo" in sys.argv)
    else:
        sys.exit(main())
