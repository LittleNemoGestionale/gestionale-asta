# Installare il Gestionale Asta su Windows

Da fare **una volta sola**, sul computer dell'ufficio che resta acceso in orario di lavoro.
Tempo: circa 30 minuti. Serve internet solo per l'installazione e per gli aggiornamenti.

## 1. Programmi da installare

1. **Python** (versione 3.12 o più recente) da <https://www.python.org/downloads/windows/>
   Nella prima schermata dell'installazione spunta **«Add python.exe to PATH»**, poi «Install Now».
2. **Git per Windows** da <https://git-scm.com/download/win> — installazione con le opzioni proposte.
   Serve per scaricare il programma e i suoi aggiornamenti.

## 2. Scaricare il programma

1. Apri il menu Start, scrivi **cmd** e apri «Prompt dei comandi».
2. Scrivi (sostituendo il nome dell'archivio che vi abbiamo indicato):

   ```
   cd C:\
   git clone https://github.com/NOME-ACCOUNT/gestionale-asta.git GestionaleAsta
   ```

3. La prima volta si apre una finestra per **accedere a GitHub**: entra con l'account dell'azienda.
   Windows ricorda l'accesso, così gli aggiornamenti successivi arrivano da soli.

## 3. Primo avvio

1. Apri la cartella `C:\GestionaleAsta` e fai doppio clic su **AVVIA-GESTIONALE.bat**.
   La prima volta prepara il programma (qualche minuto).
2. Chiede il **nome della casa d'aste** e di creare l'**utente amministratore** (nome e password robusta).
3. **Stampa il file `dati\DA-STAMPARE-E-CANCELLARE.txt`**, mettilo in cassaforte e poi **cancellalo**.
   Contiene le chiavi che proteggono i dati personali e la password dei backup: senza, non si recuperano.
4. Se Windows chiede il permesso del **firewall**, consenti solo le **reti private**.
5. La finestra nera mostra l'indirizzo per gli altri computer, per esempio `http://192.168.1.20:8000`.
   **Non chiudere la finestra nera** finché il programma deve restare acceso.

## 4. Impostazioni (file `C:\GestionaleAsta\dati\impostazioni.ini`)

Apri il file con Blocco note e completa:

```
CASA_ASTA_INTESTAZIONE = Via …, CAP Città | Tel. … | C.F. e P.IVA … | www… – email…
CASA_ASTA_EMAIL = indirizzo da cui scrivete ai clienti
CASA_ASTA_IBAN = IBAN per i pagamenti degli acquirenti
CASA_ASTA_FIRMA = firma delle email
ASTA_BACKUP_COPIE = E:\BackupAsta ; C:\Users\…\OneDrive\BackupAsta
```

Il **logo** per i documenti PDF va messo in `C:\GestionaleAsta\dati\logo.png`.
Dopo ogni modifica chiudi la finestra nera e riapri AVVIA-GESTIONALE.

## 5. Avvio e backup automatici (Utilità di pianificazione di Windows)

1. Menu Start → «Utilità di pianificazione» → **Crea attività di base**:
   - «Gestionale Asta – avvio»: quando? **All'accesso dell'utente** → avvia `C:\GestionaleAsta\AVVIA-GESTIONALE.bat`
   - «Gestionale Asta – backup»: **ogni giorno alle 13:30**, poi una seconda attività alle **23:30** → avvia
     `C:\GestionaleAsta\BACKUP.bat`
2. Nelle impostazioni di alimentazione di Windows disattiva la **sospensione** in orario di lavoro.
3. Dal router dai al computer un **indirizzo IP fisso** (così l'indirizzo per i colleghi non cambia).
4. Se avete Windows Pro, attiva **BitLocker** (cifratura del disco).

## 6. Gli altri computer

Non installano niente: aprono il browser all'indirizzo mostrato nella finestra nera
(conviene salvarlo nei Preferiti) ed entrano con il proprio utente.
Gli utenti si creano da **Archivio tecnico → Utenti**, assegnando il ruolo:
Clerk di sala, Accettazione, Spedizioni, Contabilità, Amministrazione.

## Aggiornamenti

**Automatici a ogni avvio**: se è uscita una versione nuova il programma fa un backup, la installa
e riparte. Se qualcosa va storto torna da solo alla versione precedente.
Per aggiornare a metà giornata: chiudi la finestra nera e fai doppio clic su **AGGIORNA.bat**,
poi riapri AVVIA-GESTIONALE. La versione installata è scritta in fondo a ogni pagina.
Lo storico degli aggiornamenti è in `dati\aggiornamenti.log`.
