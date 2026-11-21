# Gestionale Asta

Sostituisce il vecchio gestionale e gli Excel. Moduli:

| # | Modulo | Dove |
|---|---|---|
| 1 | Anagrafica unica (mandanti + acquirenti) con più indirizzi di spedizione | Archivio → Anagrafiche |
| 2 | Assegnazione palette (una per cliente per asta) | Archivio → Palette |
| 3 | Aggiudicazioni: schermata di sala + **conto acquirente** + **rendiconto mandante** | `/asta/<id>/sala/` e cruscotto |
| 4 | Invenduti → *contovendita* / *da rendere* / *reso* | Archivio → Lotti (azioni) + lista stampabile |
| 5 | Lista aggiudicazioni con diritti, commissioni e spedizioni | cruscotto → Lista aggiudicazioni |

Documento di spedizione: "lotti X, Y venduti alla paletta N → spedire a …", con checklist d'imballo.

## Regole di calcolo

- **Acquirente**: diritti d'asta = 18% del martello (parametro dell'asta, IVA inclusa o + IVA configurabile).
- **Mandante**: commissione definita nel **mandato** (l'accordo); si può cambiare sul singolo lotto.
  Netto = martello − commissione − IVA sulla commissione − spese mandante.
- Spese di spedizione sulla paletta, spese del mandante (foto, assicurazione…) come voci separate.

> Da verificare col commercialista: regime IVA (margine), diritto di seguito, antiriciclaggio.

## Privacy

- Nome, CF/P.IVA, email, telefono, documento, IBAN e indirizzi sono **cifrati** nel database.
- Ricerca per CF/email tramite *indice cieco* (HMAC), senza decifrare.
- Ruoli (`python manage.py crea_ruoli`): **Clerk di sala** (nessun dato personale), **Accettazione**,
  **Spedizioni**, **Amministrazione**.

## Provarlo

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py dati_demo        # asta di esempio; utenti admin/admin e clerk/clerk
python manage.py runserver
python manage.py test asta        # verifica dei calcoli e della privacy
```

Poi apri http://localhost:8000/asta/1/

## Installazione sul computer dell'ufficio (rete interna)

Un computer dell'ufficio fa da "server"; gli altri si collegano dal browser. Nessun accesso da fuori.

**Una volta sola**
1. Installa Python 3.12+ da python.org (su Windows spunta *"Add Python to PATH"*).
2. Copia la cartella `gestionale-asta` sul computer (es. `C:\GestionaleAsta`).
3. Doppio clic su **`AVVIA-GESTIONALE.bat`** (Mac: `AVVIA-GESTIONALE.command`).
   Al primo avvio chiede il nome della casa d'aste e crea l'utente amministratore.
4. **Stampa `dati\DA-STAMPARE-E-CANCELLARE.txt`**, mettilo in cassaforte e cancella il file.
5. Se Windows chiede il permesso del firewall, consenti solo **reti private**.
6. Apri `dati\impostazioni.ini` e indica in `ASTA_BACKUP_COPIE` dove copiare i backup
   (disco USB, NAS, cartella OneDrive/Google Drive), separati da `;`.
7. Backup automatico: *Utilità di pianificazione* → *Crea attività di base* → ogni giorno alle 13:30
   e alle 23:30 → avvia `BACKUP.bat`.
8. Pianifica l'avvio di `AVVIA-GESTIONALE.bat` all'accesso, così riparte dopo un riavvio.

**Ogni giorno**: la finestra nera deve restare aperta; gli altri aprono l'indirizzo che mostra
(es. `http://192.168.1.20:8000`). Conviene dare al computer un **IP fisso** dal router.

**Impostazioni del computer server**: niente sospensione/ibernazione in orario di lavoro,
BitLocker (Windows Pro) o FileVault attivo, aggiornamenti installati fuori orario, meglio con un UPS.

**Spostarlo in futuro (es. su un Mac mini)**: copia tutta la cartella, inclusa `dati`, e fai doppio clic
sull'avvio. Fine.

## Backup in Excel (piano B)

Ogni giorno, alle 13:30 e alle 23:30, il computer server crea **un file Excel con tutto**: anagrafiche, indirizzi,
mandati, palette, lotti, aggiudicazioni con diritti/commissioni/netti già calcolati, spese.
Il file è **protetto da password** (si apre normalmente con Excel/Numbers inserendola) e viene copiato
su NAS e cloud. Se il programma smettesse di funzionare, si continua a lavorare dagli Excel.

```bash
ASTA_BACKUP_PASSWORD=... python manage.py backup_excel --dir /percorso/backup --tieni 120
```

- Pulsante **"Scarica backup Excel"** nel cruscotto (solo amministrazione).
- Lanciato da `BACKUP.bat` (Windows) o `python backup.py`: Excel + copia del database + copie esterne.
- La data dell'ultimo backup è visibile in alto nel programma (in rosso se qualcosa non va).
- Per il futuro Mac mini: `deploy/` contiene la pianificazione già pronta.
- Esempio con dati finti in `docs/esempio-backup/`.

## Prossimi passi

- Import dagli Excel/vecchio gestionale (`openpyxl`) – servono i file di esempio.
- Registro modifiche (audit) e storico rettifiche delle aggiudicazioni.
- Aggiornamento in tempo reale della sala su più postazioni (oggi si aggiorna ogni 15 s).
- Gateway AI con mascheramento dei dati personali.
