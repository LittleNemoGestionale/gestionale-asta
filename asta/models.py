from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from .crypto import EncryptedTextField, blind_index

CENT = Decimal("0.01")
# Limite all'uso del contante (D.Lgs. 231/2007): vietati trasferimenti >= 5.000 €.
# Da verificare periodicamente con il commercialista.
LIMITE_CONTANTI = Decimal("5000")


def money(x) -> Decimal:
    return Decimal(x).quantize(CENT, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------- anagrafica


class Interesse(models.Model):
    """Categorie di interesse dei clienti (fumetti, manifesti, …): servono per inviti e cataloghi mirati."""
    nome = models.CharField(max_length=60, unique=True)

    class Meta:
        verbose_name_plural = "interessi"
        ordering = ["nome"]

    def __str__(self):
        return self.nome


class Soggetto(models.Model):
    """Persona o società: può essere mandante, acquirente o entrambi.

    I campi sensibili sono cifrati; le tabelle operative (palette,
    aggiudicazioni) usano solo l'ID, quindi non toccano mai i dati personali.
    """

    codice = models.CharField(max_length=20, unique=True, blank=True,
                              help_text="numero progressivo, assegnato in automatico")
    tipo = models.CharField(max_length=10, choices=[("privato", "Persona"), ("azienda", "Ditta / società")],
                            default="privato")
    nome_proprio = EncryptedTextField("nome", blank=True)
    cognome = EncryptedTextField(blank=True)
    ragione_sociale = EncryptedTextField(blank=True)
    nome = EncryptedTextField("nome completo", blank=True, help_text="calcolato da nome e cognome o ragione sociale")
    is_cliente = models.BooleanField("cliente (compra)", default=False)
    is_mandante = models.BooleanField("mandante (affida lotti)", default=False)
    interessi = models.ManyToManyField(Interesse, blank=True)
    codice_fiscale = EncryptedTextField("CF / P.IVA", blank=True)
    email = EncryptedTextField(blank=True)
    telefono = EncryptedTextField("cellulare", blank=True)
    documento = EncryptedTextField("documento d'identità", blank=True, help_text="tipo, numero, scadenza")
    iban = EncryptedTextField("IBAN", blank=True)
    note = models.TextField(blank=True)
    # indici di ricerca (impronte HMAC, non reversibili)
    cf_index = models.CharField(max_length=64, blank=True, db_index=True, editable=False)
    email_index = models.CharField(max_length=64, blank=True, db_index=True, editable=False)
    creato = models.DateTimeField(auto_now_add=True)
    modificato = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "anagrafica"
        verbose_name_plural = "anagrafiche"
        permissions = [("view_dati_personali", "Può vedere i dati personali")]

    def save(self, *args, **kwargs):
        if self.tipo == "azienda" and self.ragione_sociale:
            self.nome = self.ragione_sociale
        elif self.cognome or self.nome_proprio:
            self.nome = f"{self.cognome} {self.nome_proprio}".strip()
        if not self.codice:
            self.codice = Soggetto.prossimo_codice()
        self.cf_index = blind_index(self.codice_fiscale)
        self.email_index = blind_index(self.email)
        super().save(*args, **kwargs)

    @classmethod
    def prossimo_codice(cls) -> str:
        """Codici numerici progressivi (1, 2, 3…), come i numeri dei mandanti nei fogli."""
        numeri = [int(c) for c in cls.objects.values_list("codice", flat=True) if str(c).isdigit()]
        return str(max(numeri, default=0) + 1)

    @property
    def domicilio(self):
        return self.indirizzi.filter(tipo="domicilio").first()

    @property
    def indirizzo_spedizione(self):
        """Indirizzo dove spedire: quello di spedizione se c'è, altrimenti il domicilio."""
        return self.indirizzi.filter(tipo="spedizione").first() or self.domicilio

    @classmethod
    def cerca_per_cf(cls, cf):
        return cls.objects.filter(cf_index=blind_index(cf))

    def __str__(self):
        return f"{self.codice} – {self.nome}"


class Indirizzo(models.Model):
    soggetto = models.ForeignKey(Soggetto, on_delete=models.CASCADE, related_name="indirizzi")
    tipo = models.CharField(max_length=12, default="domicilio",
                            choices=[("domicilio", "Domicilio"), ("spedizione", "Spedizione")])
    etichetta = models.CharField(max_length=50, default="principale")
    destinatario = EncryptedTextField(blank=True, help_text="se diverso dall'intestatario")
    via = EncryptedTextField()
    cap = models.CharField(max_length=10)
    citta = models.CharField(max_length=80)
    provincia = models.CharField(max_length=4, blank=True)
    nazione = models.CharField(max_length=40, default="Italia")
    telefono_consegna = EncryptedTextField(blank=True)
    note_consegna = models.TextField(blank=True)

    class Meta:
        verbose_name_plural = "indirizzi"

    def righe(self):
        r = [self.destinatario or self.soggetto.nome, self.via,
             f"{self.cap} {self.citta} {('(' + self.provincia + ')') if self.provincia else ''}".strip(),
             self.nazione]
        if self.telefono_consegna:
            r.append(f"Tel. {self.telefono_consegna}")
        return r

    def __str__(self):
        return f"{self.get_tipo_display()} · {self.citta}"


# ---------------------------------------------------------------- mandati


class Mandato(models.Model):
    """Accordo con il mandante: qui si fissa la commissione concordata."""

    class Arrivo(models.TextChoices):
        SPEDITI = "spediti", "In arrivo (spediti)"
        NON_SPEDITI = "non_spediti", "In arrivo (non spediti)"
        DA_RITIRARE = "da_ritirare", "Da ritirare"
        IN_SEDE = "in_sede", "In sede"

    class Destinazione(models.TextChoices):
        CONTOVENDITA = "contovendita", "Contovendita"
        PROSSIMA_ASTA = "prossima_asta", "Prossima asta"
        ASTA_FUTURA = "asta_futura", "Asta futura"
        CATAWIKI = "catawiki", "Catawiki"
        CONSEGNE = "consegne", "Consegne"

    mandante = models.ForeignKey(Soggetto, on_delete=models.PROTECT, related_name="mandati")
    numero = models.CharField(max_length=20, unique=True)
    asta = models.ForeignKey("Asta", on_delete=models.SET_NULL, null=True, blank=True, related_name="mandati_ricevuti",
                             help_text="l'asta per cui è stato firmato il mandato («Mandato d'Asta n.»)")
    data = models.DateField()
    commissione_pct = models.DecimalField("commissione mandante %", max_digits=5, decimal_places=2)
    iva_su_commissione = models.BooleanField(default=False, help_text="aggiunge l'IVA sulla commissione (di solito no)")
    spesa_inserimento = models.DecimalField(
        "spese inserimento per lotto €", max_digits=8, decimal_places=2, default=Decimal("10.00"),
        help_text="addebitate per ogni lotto messo in catalogo, venduto o no")
    note_accordo = models.TextField(blank=True)
    stato_arrivo = models.CharField("stato del materiale", max_length=15, choices=Arrivo.choices, blank=True)
    destinazione = models.CharField(max_length=15, choices=Destinazione.choices, blank=True,
                                    help_text="vale per tutti gli oggetti, salvo quelli con una destinazione propria")

    class Meta:
        verbose_name_plural = "mandati"

    def __str__(self):
        return f"Mandato {self.numero} ({self.mandante.codice}, {self.commissione_pct}%)"


class Allegato(models.Model):
    """Foto o PDF del mandato firmato (con i dati del mandante): salvato cifrato in dati/allegati."""

    mandato = models.ForeignKey(Mandato, on_delete=models.CASCADE, related_name="allegati")
    nome = models.CharField(max_length=200)
    tipo = models.CharField(max_length=100)
    file = models.CharField(max_length=100, editable=False, help_text="nome del file cifrato in dati/allegati")
    dimensione = models.PositiveIntegerField(default=0)
    caricato_il = models.DateTimeField(auto_now_add=True)
    caricato_da = models.CharField(max_length=150, blank=True)

    class Meta:
        verbose_name_plural = "allegati"
        ordering = ["caricato_il"]

    @staticmethod
    def cartella():
        from django.conf import settings
        d = settings.DATI_DIR / "allegati"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @classmethod
    def salva(cls, mandato, caricato, utente=""):
        import uuid

        from .crypto import cifra_bytes
        contenuto = caricato.read()
        nome_file = f"{uuid.uuid4().hex}.bin"
        (cls.cartella() / nome_file).write_bytes(cifra_bytes(contenuto))
        return cls.objects.create(mandato=mandato, nome=caricato.name[:200],
                                  tipo=(caricato.content_type or "application/octet-stream")[:100],
                                  file=nome_file, dimensione=len(contenuto), caricato_da=str(utente)[:150])

    def leggi(self) -> bytes:
        from .crypto import decifra_bytes
        return decifra_bytes((self.cartella() / self.file).read_bytes())

    def elimina(self):
        (self.cartella() / self.file).unlink(missing_ok=True)
        self.delete()

    @property
    def immagine(self) -> bool:
        return self.tipo.startswith("image/") and "heic" not in self.tipo and "heif" not in self.tipo

    def __str__(self):
        return self.nome


class Oggetto(models.Model):
    """Pezzo ricevuto con un mandato, prima e dopo il passaggio in asta (il magazzino)."""

    class Stato(models.TextChoices):
        MAGAZZINO = "magazzino", "In magazzino"
        ASSEGNATO = "assegnato", "Assegnato a un'asta"
        VENDUTO = "venduto", "Venduto"
        INVENDUTO = "invenduto", "Invenduto"
        RESTITUITO = "restituito", "Restituito"

    mandato = models.ForeignKey(Mandato, on_delete=models.PROTECT, related_name="oggetti")
    progressivo = models.PositiveIntegerField(editable=False)
    descrizione = models.TextField()
    categoria = models.CharField(max_length=80, blank=True)
    stima_min = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    stima_max = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    riserva = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    collocazione = models.CharField(max_length=100, blank=True, help_text="dove si trova, es. Caveau, Scatole bianche")
    destinazione = models.CharField(max_length=15, choices=Mandato.Destinazione.choices, blank=True,
                                    help_text="vuoto = quella del mandato")
    consegnato_il = models.DateField(null=True, blank=True)
    restituito_il = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=250, blank=True)
    creato = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "oggetti"
        ordering = ["mandato", "progressivo"]
        constraints = [models.UniqueConstraint(fields=["mandato", "progressivo"], name="oggetto_unico_per_mandato")]

    def save(self, *args, **kwargs):
        if not self.progressivo:
            ultimo = Oggetto.objects.filter(mandato=self.mandato).aggregate(m=models.Max("progressivo"))["m"]
            self.progressivo = (ultimo or 0) + 1
        super().save(*args, **kwargs)

    @property
    def codice(self) -> str:
        return f"{self.mandato.numero}/{self.progressivo:02d}"

    @property
    def destinazione_effettiva(self) -> str:
        return self.destinazione or self.mandato.destinazione

    def get_destinazione_effettiva_display(self) -> str:
        return dict(Mandato.Destinazione.choices).get(self.destinazione_effettiva, "")

    @property
    def ultimo_lotto(self):
        lotti = getattr(self, "_lotti", None)
        if lotti is None:
            lotti = list(self.lotti.select_related("asta", "aggiudicazione").order_by("asta__data", "pk"))
        return lotti[-1] if lotti else None

    @property
    def stato(self) -> str:
        if self.restituito_il:
            return self.Stato.RESTITUITO
        l = self.ultimo_lotto
        if l is None:
            return self.Stato.MAGAZZINO
        if hasattr(l, "aggiudicazione"):
            return self.Stato.VENDUTO
        if l.asta.chiusa or l.stato in (Lotto.Stato.INVENDUTO, Lotto.Stato.DA_RENDERE, Lotto.Stato.RIPROPOSTO):
            return self.Stato.INVENDUTO
        return self.Stato.ASSEGNATO

    def get_stato_display(self) -> str:
        return dict(self.Stato.choices)[self.stato]

    @property
    def disponibile(self) -> bool:
        return self.stato in (self.Stato.MAGAZZINO, self.Stato.INVENDUTO)

    def __str__(self):
        return f"{self.codice} {self.descrizione[:40]}"


# ---------------------------------------------------------------- asta


class Asta(models.Model):
    numero = models.PositiveIntegerField("numero asta", null=True, blank=True)
    nome = models.CharField("tema", max_length=120, help_text="es. Fumetto e illustrazione")
    data = models.DateField("inizio")
    ora_inizio = models.TimeField("ora di inizio", null=True, blank=True)
    data_fine = models.DateField("fine", null=True, blank=True, help_text="vuoto = stesso giorno")
    ora_fine = models.TimeField("ora di fine", null=True, blank=True)
    numero_tornate = models.PositiveSmallIntegerField("numero di tornate", default=1)
    giorni_post_asta = models.PositiveSmallIntegerField(
        "giorni di post asta", default=30, help_text="per quanti giorni dopo la fine si possono vendere gli invenduti")
    diritti_pct = models.DecimalField("diritti d'asta acquirente %", max_digits=5, decimal_places=2,
                                      default=Decimal("18.00"))
    diritti_iva_inclusa = models.BooleanField(
        "diritti IVA inclusa", default=True,
        help_text="Se attivo, il 18% è già comprensivo di IVA; altrimenti l'IVA si aggiunge.")
    iva_pct = models.DecimalField("aliquota IVA %", max_digits=5, decimal_places=2, default=Decimal("22.00"))
    diritti_online_pct = models.DecimalField(
        "diritti % palette online", max_digits=5, decimal_places=2, default=Decimal("23.00"))
    palette_online_da = models.PositiveIntegerField(
        "palette online dal numero", null=True, blank=True, default=900,
        help_text="Le palette da questo numero in su pagano i diritti online (es. 900 → 9xx). Vuoto = nessuna.")
    luogo = models.CharField(max_length=120, blank=True)
    note = models.TextField(blank=True)
    chiusa = models.BooleanField("conclusa", default=False,
                                 help_text="Asta conclusa: niente più aggiudicazioni, resta consultabile.")

    class Meta:
        verbose_name_plural = "aste"
        ordering = ["-data"]

    def __str__(self):
        return f"{self.nome} ({self.data:%d/%m/%Y})"

    @property
    def stato(self) -> str:
        from django.utils import timezone
        if self.chiusa:
            return "conclusa"
        return "futura" if self.data > timezone.localdate() else "in corso"

    @property
    def fine(self):
        return self.data_fine or self.data

    @property
    def fine_post_asta(self):
        from datetime import timedelta
        return self.fine + timedelta(days=self.giorni_post_asta)

    @property
    def post_asta_aperto(self) -> bool:
        from django.utils import timezone
        oggi = timezone.localdate()
        return not self.chiusa and self.fine <= oggi <= self.fine_post_asta

    def fascia_di(self, numero: int):
        for f in self.fasce.all():
            if f.da <= numero <= f.a:
                return f
        return None

    def crea_fasce(self, modello=None):
        """Fasce delle palette: copiate da un'asta precedente o quelle standard."""
        if self.fasce.exists():
            return
        righe = ([(f.tipo, f.da, f.a, f.diritti_pct) for f in modello.fasce.all()] if modello and modello.fasce.exists()
                 else FasciaPalette.STANDARD)
        for tipo, da, a, pct in righe:
            FasciaPalette.objects.create(asta=self, tipo=tipo, da=da, a=a,
                                         diritti_pct=Decimal(pct) if pct is not None else None)

    @classmethod
    def prossimo_numero(cls) -> int:
        ultimo = cls.objects.exclude(numero=None).order_by("-numero").values_list("numero", flat=True).first()
        return (ultimo or 0) + 1


class ModalitaConsegna(models.Model):
    """Come arriva la merce all'acquirente, con il costo proposto (modificabile per ogni acquirente)."""
    nome = models.CharField(max_length=60, unique=True)
    spedizione = models.BooleanField(default=True, help_text="no = ritiro, non serve l'indirizzo")
    costo = models.DecimalField("costo proposto €", max_digits=8, decimal_places=2, default=0)
    zona = models.CharField(max_length=10, blank=True, choices=[("", "—"), ("it", "Italia"), ("ue", "UE"),
                                                                 ("extra", "Fuori UE")],
                            help_text="per proporla in automatico in base alla nazione dell'indirizzo")
    testo_email = models.TextField(blank=True, help_text="frase per l'email, es. «I lotti saranno spediti con corriere…»")
    ordine = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name = "modalità di consegna"
        verbose_name_plural = "modalità di consegna"
        ordering = ["ordine", "nome"]

    def __str__(self):
        return self.nome


class StatoConsegna(models.TextChoices):
    DA_DEFINIRE = "da_definire", "—"
    RITIRA = "ritira", "Ritira (in sede / fiera)"
    DA_SPEDIRE = "da_spedire", "Da spedire"
    ESPORTAZIONE = "esportazione", "In attesa esportazione"
    SPEDITO = "spedito", "Spedito"
    RITIRATO = "ritirato", "Ritirato / consegnato"


class FasciaPalette(models.Model):
    """Gruppi di numeri di paletta per canale di partecipazione (sala, telefono, LiveAuctioneers…)."""

    class Tipo(models.TextChoices):
        SALA = "sala", "In sala"
        SCRITTE = "scritte", "Offerte scritte"
        TELEFONO = "telefono", "Telefono"
        POST_ASTA = "post_asta", "Post asta"
        LIVE = "live", "LiveAuctioneers"

    STANDARD = [("sala", 1, 199, None), ("scritte", 200, 299, None), ("telefono", 300, 399, None),
                ("sala", 400, 799, None), ("post_asta", 800, 899, None), ("live", 900, 999, "23")]

    asta = models.ForeignKey(Asta, on_delete=models.CASCADE, related_name="fasce")
    tipo = models.CharField(max_length=12, choices=Tipo.choices)
    da = models.PositiveIntegerField()
    a = models.PositiveIntegerField()
    diritti_pct = models.DecimalField("diritti %", max_digits=5, decimal_places=2, null=True, blank=True,
                                      help_text="vuoto = diritti dell'asta")

    class Meta:
        verbose_name = "fascia di palette"
        verbose_name_plural = "fasce di palette"
        ordering = ["asta", "da"]

    def __str__(self):
        return f"{self.da}–{self.a} {self.get_tipo_display()}"


class Tornata(models.Model):
    asta = models.ForeignKey(Asta, on_delete=models.CASCADE, related_name="tornate")
    numero = models.PositiveSmallIntegerField()
    data = models.DateField(null=True, blank=True)
    ora = models.TimeField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "tornate"
        ordering = ["asta", "numero"]
        constraints = [models.UniqueConstraint(fields=["asta", "numero"], name="tornata_unica")]

    ORDINALI = ["PRIMA", "SECONDA", "TERZA", "QUARTA", "QUINTA", "SESTA", "SETTIMA", "OTTAVA", "NONA", "DECIMA"]

    @property
    def ordinale(self):
        return self.ORDINALI[self.numero - 1] if self.numero <= len(self.ORDINALI) else f"{self.numero}ª"

    def __str__(self):
        return f"Tornata {self.numero}"


class Paletta(models.Model):
    asta = models.ForeignKey(Asta, on_delete=models.PROTECT, related_name="palette")
    numero = models.PositiveIntegerField()
    acquirente = models.ForeignKey(Soggetto, on_delete=models.PROTECT, related_name="palette")
    indirizzo_spedizione = models.ForeignKey(Indirizzo, on_delete=models.SET_NULL, null=True, blank=True,
                                             help_text="vuoto = ritiro in sede")
    spese_spedizione = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    diritti_pct = models.DecimalField(
        "diritti % specifici", max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="vuoto = regola dell'asta (18% in sala, 23% palette online 9xx)")
    abbuono = models.DecimalField("abbuono / arrotondamento", max_digits=10, decimal_places=2, default=0)
    tipo_documento = models.CharField(max_length=10, blank=True,
                                      choices=[("sc", "Scontrino"), ("fatt", "Fattura")])
    numero_documento = models.CharField("nr scontrino / fattura", max_length=30, blank=True)
    stato_consegna = models.CharField(max_length=15, choices=StatoConsegna.choices,
                                      default=StatoConsegna.DA_DEFINIRE)
    modalita = models.ForeignKey(ModalitaConsegna, on_delete=models.SET_NULL, null=True, blank=True,
                                 verbose_name="modalità di consegna")
    email_preparata_il = models.DateTimeField(null=True, blank=True)
    note = models.CharField("note (ritiro, spedizione…)", max_length=200, blank=True)
    assegnata_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "palette"
        ordering = ["asta", "numero"]
        constraints = [
            models.UniqueConstraint(fields=["asta", "numero"], name="paletta_unica_per_asta"),
            models.UniqueConstraint(fields=["asta", "acquirente"], name="una_paletta_per_cliente"),
        ]

    def clean(self):
        if self.indirizzo_spedizione_id and self.indirizzo_spedizione.soggetto_id != self.acquirente_id:
            raise ValidationError("L'indirizzo di spedizione non appartiene a questo acquirente.")

    def __str__(self):
        return f"Paletta {self.numero}"

    @property
    def diritti_effettivi(self) -> Decimal:
        if self.diritti_pct is not None:
            return self.diritti_pct
        fascia = self.asta.fascia_di(self.numero)
        if fascia:
            return fascia.diritti_pct if fascia.diritti_pct is not None else self.asta.diritti_pct
        if self.asta.palette_online_da and self.numero >= self.asta.palette_online_da:
            return self.asta.diritti_online_pct
        return self.asta.diritti_pct

    @property
    def canale(self) -> str:
        f = self.asta.fascia_di(self.numero)
        return f.get_tipo_display() if f else ""

    @property
    def documento(self):
        return f"{self.tipo_documento} {self.numero_documento}".strip() if self.numero_documento else ""

    # ---- incassi
    def situazione_incasso(self):
        c = self.conto()
        incassi = list(self.incassi.select_related("metodo").order_by("data", "pk"))
        incassato = money(sum((i.importo for i in incassi), Decimal(0)))
        residuo = money(c["totale"] - incassato)
        if not c["aggiudicazioni"]:
            stato = "nessun lotto"
        elif residuo <= 0:
            stato = "incassato"
        elif incassato > 0:
            stato = "parziale"
        else:
            stato = "da incassare"
        contanti = sum((i.importo for i in incassi if i.metodo.contanti), Decimal(0))
        return {**c, "incassi": incassi, "incassato": incassato, "residuo": residuo, "stato": stato,
                "data_saldo": incassi[-1].data if incassi and residuo <= 0 else None,
                "avviso_contanti": contanti >= LIMITE_CONTANTI}

    # ---- conteggi acquirente
    def conto(self):
        agg = list(self.aggiudicazioni.select_related("lotto"))
        martello = sum((a.prezzo_martello for a in agg), Decimal(0))
        diritti = sum((a.diritti for a in agg), Decimal(0))
        iva_diritti = sum((a.iva_diritti for a in agg), Decimal(0))
        spese = money(self.spese_spedizione)
        return {
            "aggiudicazioni": agg,
            "martello": money(martello),
            "diritti": money(diritti),
            "iva_diritti": money(iva_diritti),
            "spese_spedizione": spese,
            "abbuono": money(self.abbuono),
            "totale": money(martello + diritti + (0 if self.asta.diritti_iva_inclusa else iva_diritti) + spese
                            - self.abbuono),
        }


class Lotto(models.Model):
    class Stato(models.TextChoices):
        IN_ASTA = "in_asta", "In asta"
        AGGIUDICATO = "aggiudicato", "Aggiudicato"
        INVENDUTO = "invenduto", "Invenduto – da decidere"
        CONTOVENDITA = "contovendita", "In contovendita"
        DA_RENDERE = "da_rendere", "Da rendere"
        RESO = "reso", "Reso al mandante"
        RIPROPOSTO = "riproposto", "Riproposto in un'altra asta"

    asta = models.ForeignKey(Asta, on_delete=models.PROTECT, related_name="lotti")
    numero = models.CharField(max_length=10)
    tornata = models.PositiveSmallIntegerField(null=True, blank=True)
    sezione = models.CharField(max_length=150, blank=True, help_text="sezione del catalogo, es. HORROR")
    titolo = models.CharField(max_length=250, blank=True)
    illustratore = models.CharField("illustratore / autore", max_length=200, blank=True)
    mandato = models.ForeignKey(Mandato, on_delete=models.PROTECT, related_name="lotti")
    descrizione = models.TextField(blank=True)
    descrizione_breve = models.CharField(
        max_length=200, blank=True, help_text="nota corta per il foglio del banditore (es. «Lotto di 18 fascicoli»)")
    note_interne = models.CharField(max_length=250, blank=True, help_text="note di lavoro, non vanno in catalogo")
    da_fotografare = models.BooleanField(default=False)
    stima_min = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    stima_max = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    base_asta = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    riserva = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    commissione_pct = models.DecimalField(
        "commissione specifica %", max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="vuoto = usa la commissione del mandato")
    stato = models.CharField(max_length=15, choices=Stato.choices, default=Stato.IN_ASTA)
    prezzo_contovendita = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    lotto_origine = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name="riproposte", help_text="se riproposto da un'asta precedente")
    ordine = models.PositiveIntegerField(default=0, editable=False, db_index=True)
    data_reso = models.DateField(null=True, blank=True)
    oggetto = models.ForeignKey("Oggetto", on_delete=models.SET_NULL, null=True, blank=True, related_name="lotti",
                                help_text="il pezzo del magazzino messo in asta con questo lotto")

    class Meta:
        verbose_name_plural = "lotti"
        ordering = ["asta", "ordine", "numero"]
        constraints = [models.UniqueConstraint(fields=["asta", "numero"], name="lotto_unico_per_asta")]

    def save(self, *args, **kwargs):
        import re
        m = re.match(r"\d+", self.numero or "")
        self.ordine = int(m.group()) if m else 0  # così il lotto 10 viene dopo il 9
        super().save(*args, **kwargs)

    @property
    def mandante(self):
        return self.mandato.mandante

    @property
    def etichetta(self) -> str:
        """Titolo del lotto se c'è (catalogo da Word), altrimenti la descrizione."""
        return self.titolo or self.descrizione

    def registro_banditore(self) -> list[str]:
        """Offerte scritte (dalla più alta) e telefoni, in forma corta: «201: 150», «TEL 301»."""
        offerte = sorted(self.offerte_scritte.all(), key=lambda o: (-o.importo, o.ricevuta_il))
        return ([f"{o.paletta.numero}: {o.importo:,.0f}".replace(",", ".") for o in offerte]
                + [f"TEL {t.paletta.numero}" for t in self.telefonate.all()])

    @property
    def commissione_effettiva(self) -> Decimal:
        return self.commissione_pct if self.commissione_pct is not None else self.mandato.commissione_pct

    def __str__(self):
        return f"Lotto {self.numero}"


class OffertaScritta(models.Model):
    """Offerta massima lasciata prima dell'asta (palette 200–299, ma vale qualsiasi paletta dell'asta)."""

    lotto = models.ForeignKey(Lotto, on_delete=models.CASCADE, related_name="offerte_scritte")
    paletta = models.ForeignKey(Paletta, on_delete=models.CASCADE, related_name="offerte_scritte")
    importo = models.DecimalField("offerta massima €", max_digits=12, decimal_places=2)
    note = models.CharField(max_length=200, blank=True)
    ricevuta_il = models.DateTimeField(default=timezone.now, help_text="a parità di importo vince la prima ricevuta")

    class Meta:
        verbose_name = "offerta scritta"
        verbose_name_plural = "offerte scritte"
        ordering = ["lotto__ordine", "-importo", "ricevuta_il"]
        constraints = [models.UniqueConstraint(fields=["lotto", "paletta"], name="offerta_unica_per_lotto")]

    def __str__(self):
        return f"Lotto {self.lotto.numero}: paletta {self.paletta.numero} fino a {self.importo}"


class Telefonata(models.Model):
    """Partecipante al telefono su un lotto (palette 300–399) e il telefonista che lo chiama."""

    lotto = models.ForeignKey(Lotto, on_delete=models.CASCADE, related_name="telefonate")
    paletta = models.ForeignKey(Paletta, on_delete=models.CASCADE, related_name="telefonate")
    telefonista = models.CharField(max_length=80, blank=True)
    note = models.CharField(max_length=200, blank=True, help_text="es. lingua, orario, offerta di sicurezza")
    creata_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "telefonate"
        ordering = ["lotto__ordine", "paletta__numero"]
        constraints = [models.UniqueConstraint(fields=["lotto", "paletta"], name="telefonata_unica_per_lotto")]

    def __str__(self):
        return f"Lotto {self.lotto.numero}: telefono paletta {self.paletta.numero}"


class Aggiudicazione(models.Model):
    lotto = models.OneToOneField(Lotto, on_delete=models.PROTECT, related_name="aggiudicazione")
    paletta = models.ForeignKey(Paletta, on_delete=models.PROTECT, related_name="aggiudicazioni")
    prezzo_martello = models.DecimalField(max_digits=12, decimal_places=2)
    sotto_riserva = models.BooleanField(default=False, help_text="aggiudicato sotto la riserva (da confermare col mandante)")
    post_asta = models.BooleanField(default=False, help_text="venduto nei giorni dopo l'asta")
    registrata_da = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                      editable=False)
    registrata_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "aggiudicazioni"
        ordering = ["lotto__ordine", "lotto__numero"]

    def clean(self):
        if self.lotto_id and self.paletta_id and self.lotto.asta_id != self.paletta.asta_id:
            raise ValidationError("Lotto e paletta appartengono ad aste diverse.")

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.lotto.stato != Lotto.Stato.AGGIUDICATO:
            self.lotto.stato = Lotto.Stato.AGGIUDICATO
            self.lotto.save(update_fields=["stato"])

    # ---- lato acquirente
    @property
    def diritti(self) -> Decimal:
        return money(self.prezzo_martello * self.paletta.diritti_effettivi / 100)

    @property
    def iva_diritti(self) -> Decimal:
        asta = self.lotto.asta
        if asta.diritti_iva_inclusa:  # IVA scorporata dai diritti
            return money(self.diritti - self.diritti / (1 + asta.iva_pct / 100))
        return money(self.diritti * asta.iva_pct / 100)

    # ---- lato mandante
    @property
    def commissione(self) -> Decimal:
        return money(self.prezzo_martello * self.lotto.commissione_effettiva / 100)

    @property
    def iva_commissione(self) -> Decimal:
        if not self.lotto.mandato.iva_su_commissione:
            return Decimal("0.00")
        return money(self.commissione * self.lotto.asta.iva_pct / 100)

    @property
    def netto_mandante(self) -> Decimal:
        return money(self.prezzo_martello - self.commissione - self.iva_commissione)

    def __str__(self):
        return f"{self.lotto} → {self.paletta} a € {self.prezzo_martello}"


class RendicontoMandante(models.Model):
    """Numero e invio del rendiconto (liquidazione) di un mandante per un'asta."""
    asta = models.ForeignKey(Asta, on_delete=models.PROTECT, related_name="rendiconti")
    mandante = models.ForeignKey(Soggetto, on_delete=models.PROTECT, related_name="rendiconti")
    numero = models.PositiveIntegerField()
    anno = models.PositiveSmallIntegerField()
    data = models.DateField()
    email_preparata_il = models.DateTimeField(null=True, blank=True)
    email_inviata_il = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "rendiconto mandante"
        verbose_name_plural = "rendiconti mandanti"
        constraints = [models.UniqueConstraint(fields=["asta", "mandante"], name="un_rendiconto_per_asta"),
                       models.UniqueConstraint(fields=["anno", "numero"], name="numero_rendiconto_unico")]

    @classmethod
    def per(cls, asta, mandante):
        """Restituisce il rendiconto, assegnando il numero progressivo dell'anno la prima volta."""
        from django.db import transaction
        from django.utils import timezone
        r = cls.objects.filter(asta=asta, mandante=mandante).first()
        if r:
            return r
        oggi = timezone.localdate()
        with transaction.atomic():
            ultimo = cls.objects.filter(anno=oggi.year).aggregate(m=models.Max("numero"))["m"] or 0
            return cls.objects.create(asta=asta, mandante=mandante, numero=ultimo + 1, anno=oggi.year, data=oggi)


class SpesaMandante(models.Model):
    """Spese addebitate al mandante (foto, assicurazione, trasporto, ...)."""

    asta = models.ForeignKey(Asta, on_delete=models.PROTECT)
    mandato = models.ForeignKey(Mandato, on_delete=models.PROTECT, related_name="spese")
    lotto = models.ForeignKey(Lotto, on_delete=models.SET_NULL, null=True, blank=True)
    descrizione = models.CharField(max_length=120)
    importo = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = "spesa mandante"
        verbose_name_plural = "spese mandante"


def rendiconto_mandante(asta: Asta, mandante: Soggetto) -> dict:
    lotti = list(Lotto.objects.filter(asta=asta, mandato__mandante=mandante)
                 .select_related("mandato", "asta").order_by("ordine", "numero"))
    venduti = [l.aggiudicazione for l in lotti if hasattr(l, "aggiudicazione")]
    non_venduti = [l for l in lotti if not hasattr(l, "aggiudicazione")]
    spese = list(SpesaMandante.objects.filter(asta=asta, mandato__mandante=mandante).select_related("lotto"))
    inserimenti = spese_inserimento(asta, lotti)
    tot_spese = money(sum((s.importo for s in spese), Decimal(0)) + sum((i["importo"] for i in inserimenti), Decimal(0)))
    percentuali = sorted({a.lotto.commissione_effettiva for a in venduti})
    tot = {
        "martello": money(sum((a.prezzo_martello for a in venduti), Decimal(0))),
        "commissione": money(sum((a.commissione for a in venduti), Decimal(0))),
        "iva_commissione": money(sum((a.iva_commissione for a in venduti), Decimal(0))),
    }
    tot["spese"] = tot_spese
    tot["netto"] = money(tot["martello"] - tot["commissione"] - tot["iva_commissione"] - tot_spese)
    mandati = []
    for m in sorted({l.mandato for l in lotti}, key=lambda m: m.numero):
        mandati.append({"mandato": m, "venduti": [a for a in venduti if a.lotto.mandato_id == m.pk]})
    return {"venduti": venduti, "non_venduti": non_venduti, "spese": spese, "inserimenti": inserimenti,
            "totali": tot, "mandati": mandati, "lotti": lotti,
            "percentuale": percentuali[0] if len(percentuali) == 1 else None}


def spese_inserimento(asta, lotti) -> list[dict]:
    """Spese di inserimento a catalogo: per ogni mandato, n. lotti dell'asta × costo per lotto."""
    out = []
    for m in sorted({l.mandato for l in lotti if l.stato != Lotto.Stato.RIPROPOSTO or l.asta_id == asta.pk},
                    key=lambda m: m.numero):
        n = sum(1 for l in lotti if l.mandato_id == m.pk)
        if n and m.spesa_inserimento:
            out.append({"mandato": m, "n": n, "unitario": m.spesa_inserimento,
                        "importo": money(n * m.spesa_inserimento)})
    return out


def totale_asta(asta: Asta):
    return Aggiudicazione.objects.filter(lotto__asta=asta).aggregate(t=Sum("prezzo_martello"))["t"] or 0


# ---------------------------------------------------------------- contabilità


class MetodoPagamento(models.Model):
    sigla = models.CharField(max_length=20, unique=True, help_text="es. BB B.S., BMT, PayPal")
    descrizione = models.CharField(max_length=80)
    contanti = models.BooleanField(default=False, help_text="conta per il limite del contante")
    attivo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "metodo di pagamento"
        verbose_name_plural = "metodi di pagamento"
        ordering = ["sigla"]

    def __str__(self):
        return self.sigla


class Incasso(models.Model):
    """Pagamento ricevuto da un acquirente. Una paletta può avere più incassi."""

    paletta = models.ForeignKey(Paletta, on_delete=models.PROTECT, related_name="incassi")
    data = models.DateField()
    metodo = models.ForeignKey(MetodoPagamento, on_delete=models.PROTECT)
    importo = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    commissioni = models.DecimalField("commissioni trattenute", max_digits=8, decimal_places=2, default=0,
                                      help_text="es. spese bonifico o PayPal")
    note = models.CharField(max_length=200, blank=True)
    registrato_da = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                      editable=False)

    class Meta:
        verbose_name_plural = "incassi"
        ordering = ["-data"]

    def __str__(self):
        return f"{self.paletta} · {self.metodo} {self.importo} € il {self.data:%d/%m/%Y}"


class PagamentoMandante(models.Model):
    """Pagamento (anche in acconto) fatto a un mandante per un'asta."""

    asta = models.ForeignKey(Asta, on_delete=models.PROTECT, related_name="pagamenti_mandanti")
    mandato = models.ForeignKey(Mandato, on_delete=models.PROTECT, related_name="pagamenti")
    data = models.DateField()
    metodo = models.ForeignKey(MetodoPagamento, on_delete=models.PROTECT)
    importo = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    numero_documento = models.CharField("nr documento", max_length=30, blank=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "pagamento a mandante"
        verbose_name_plural = "pagamenti a mandanti"
        ordering = ["-data"]


def situazione_mandanti(asta: Asta) -> list[dict]:
    """Una riga per mandato: quanto dobbiamo, quanto è già pagabile, quanto pagato."""
    righe = []
    mandati = (Mandato.objects.filter(lotti__asta=asta).distinct()
               .select_related("mandante").order_by("mandante__codice", "numero"))
    stato_palette = {}
    for m in mandati:
        venduti = list(Aggiudicazione.objects.filter(lotto__asta=asta, lotto__mandato=m)
                       .select_related("lotto__mandato", "lotto__asta", "paletta__asta"))
        spese = money(sum((s.importo for s in SpesaMandante.objects.filter(asta=asta, mandato=m)), Decimal(0))
                      + Lotto.objects.filter(asta=asta, mandato=m).count() * m.spesa_inserimento)
        dovuto = money(sum((a.netto_mandante for a in venduti), Decimal(0)) - spese)
        pagabile = Decimal(0)
        for a in venduti:
            if a.paletta_id not in stato_palette:
                stato_palette[a.paletta_id] = a.paletta.situazione_incasso()["stato"] == "incassato"
            if stato_palette[a.paletta_id]:
                pagabile += a.netto_mandante
        pagamenti = list(PagamentoMandante.objects.filter(asta=asta, mandato=m)
                         .select_related("metodo").order_by("data", "pk"))
        pagato = money(sum((p.importo for p in pagamenti), Decimal(0)))
        residuo = money(dovuto - pagato)
        righe.append({
            "mandato": m, "mandante": m.mandante, "lotti_venduti": len(venduti), "dovuto": dovuto,
            "pagabile": money(max(pagabile - spese - pagato, Decimal(0))),
            "pagamenti": pagamenti, "pagato": pagato, "residuo": residuo,
            "stato": "pagato" if residuo <= 0 else ("acconto" if pagato > 0 else "da pagare"),
            "iban_mancante": residuo > 0 and not m.mandante.iban,
        })
    return righe


def iva_mensile(asta: Asta | None = None) -> list[dict]:
    """Diritti + spese incassati per mese (data del saldo), con IVA scorporata.

    Replica il calcolo che oggi si fa a mano nel foglio incassi (cella E90).
    """
    from collections import defaultdict
    mesi = defaultdict(lambda: {"diritti": Decimal(0), "spese": Decimal(0), "palette": 0})
    palette = Paletta.objects.select_related("asta")
    if asta:
        palette = palette.filter(asta=asta)
    for p in palette:
        s = p.situazione_incasso()
        if s["data_saldo"]:
            k = s["data_saldo"].replace(day=1)
            mesi[k]["diritti"] += s["diritti"]
            mesi[k]["spese"] += s["spese_spedizione"]
            mesi[k]["palette"] += 1
    out = []
    for k in sorted(mesi):
        v = mesi[k]
        lordo = money(v["diritti"] + v["spese"])
        aliquota = (asta.iva_pct if asta else Decimal("22")) / 100
        imponibile = money(lordo / (1 + aliquota))
        out.append({"mese": k, **v, "lordo": lordo, "imponibile": imponibile, "iva": money(lordo - imponibile)})
    return out
