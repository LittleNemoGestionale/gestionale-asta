from datetime import date
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.db import IntegrityError, connection
from django.test import TestCase

from .models import Aggiudicazione, Asta, Lotto, Mandato, Oggetto, Paletta, Soggetto, rendiconto_mandante
from .views_sala import parse_euro


class Base(TestCase):
    def setUp(self):
        self.mand = Soggetto.objects.create(codice="M1", nome="Mandante Uno", codice_fiscale="RSSMRA80A01H501U")
        self.mandato = Mandato.objects.create(mandante=self.mand, numero="1", data=date.today(), commissione_pct=D("15"))
        self.cli = Soggetto.objects.create(codice="C1", nome="Cliente Uno")
        self.asta = Asta.objects.create(nome="Test", data=date.today())
        self.pal = Paletta.objects.create(asta=self.asta, numero=7, acquirente=self.cli, spese_spedizione=D("20"))
        self.lotto = Lotto.objects.create(asta=self.asta, numero="1", mandato=self.mandato, descrizione="x")


class CalcoliTest(Base):
    def test_diritti_18_iva_inclusa(self):
        a = Aggiudicazione.objects.create(lotto=self.lotto, paletta=self.pal, prezzo_martello=D("1000"))
        self.assertEqual(a.diritti, D("180.00"))
        self.assertEqual(a.iva_diritti, D("32.46"))  # 180 - 180/1.22
        c = self.pal.conto()
        self.assertEqual(c["totale"], D("1200.00"))  # 1000 + 180 + 20 spedizione

    def test_diritti_piu_iva(self):
        self.asta.diritti_iva_inclusa = False
        self.asta.save()
        a = Aggiudicazione.objects.create(lotto=self.lotto, paletta=self.pal, prezzo_martello=D("1000"))
        self.assertEqual(a.iva_diritti, D("39.60"))
        self.assertEqual(self.pal.conto()["totale"], D("1239.60"))

    def test_commissione_da_mandato_e_override_lotto(self):
        a = Aggiudicazione.objects.create(lotto=self.lotto, paletta=self.pal, prezzo_martello=D("1000"))
        self.assertEqual(a.commissione, D("150.00"))
        self.assertEqual(a.netto_mandante, D("850.00"))  # 1000 - 150 (nessuna IVA sulla commissione)
        self.lotto.commissione_pct = D("8")
        self.lotto.save()
        a.refresh_from_db()
        self.assertEqual(a.commissione, D("80.00"))

    def test_rendiconto(self):
        Aggiudicazione.objects.create(lotto=self.lotto, paletta=self.pal, prezzo_martello=D("2000"))
        Lotto.objects.create(asta=self.asta, numero="2", mandato=self.mandato, descrizione="y")
        r = rendiconto_mandante(self.asta, self.mand)
        self.assertEqual(len(r["venduti"]), 1)
        self.assertEqual(len(r["non_venduti"]), 1)
        self.assertEqual(r["totali"]["netto"], D("1680.00"))  # 2000 - 300 - 2 lotti x 10 € di inserimento

    def test_lotto_non_aggiudicabile_due_volte(self):
        Aggiudicazione.objects.create(lotto=self.lotto, paletta=self.pal, prezzo_martello=D("100"))
        with self.assertRaises(IntegrityError):
            Aggiudicazione.objects.create(lotto=self.lotto, paletta=self.pal, prezzo_martello=D("200"))

    def test_parse_euro(self):
        self.assertEqual(parse_euro("1.500,50"), D("1500.50"))
        self.assertEqual(parse_euro("1.500"), D("1500"))
        self.assertEqual(parse_euro("1500.5"), D("1500.5"))
        self.assertEqual(parse_euro("€ 800"), D("800"))


class PrivacyTest(Base):
    def test_dati_cifrati_nel_database(self):
        with connection.cursor() as cur:
            cur.execute("SELECT nome, codice_fiscale FROM asta_soggetto WHERE codice='M1'")
            nome, cf = cur.fetchone()
        self.assertNotIn("Mandante", nome)
        self.assertNotIn("RSSMRA", cf)
        self.assertEqual(Soggetto.objects.get(codice="M1").nome, "Mandante Uno")

    def test_ricerca_cf_con_indice_cieco(self):
        self.assertEqual(Soggetto.cerca_per_cf("rssmra80a01h501u").get(), self.mand)

    def test_clerk_non_vede_dati_personali(self):
        call_command("crea_ruoli", stdout=open("/dev/null", "w"))
        u = User.objects.create_user("clerk", password="x", is_staff=True)
        u.groups.add(Group.objects.get(name="Clerk di sala"))
        self.client.force_login(u)
        self.assertEqual(self.client.get(f"/asta/{self.asta.pk}/paletta/7/conto/").status_code, 403)
        r = self.client.get(f"/asta/{self.asta.pk}/sala/")
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "Cliente Uno")
        r = self.client.post(f"/asta/{self.asta.pk}/sala/", {"lotto": "1", "paletta": "7", "prezzo": "1.200"})
        self.assertEqual(Aggiudicazione.objects.get().prezzo_martello, D("1200"))
        self.lotto.refresh_from_db()
        self.assertEqual(self.lotto.stato, Lotto.Stato.AGGIUDICATO)


class BackupExcelTest(Base):
    def test_backup_protetto_e_leggibile(self):
        import io

        import msoffcrypto
        from openpyxl import load_workbook

        from .export_excel import esporta_bytes
        Aggiudicazione.objects.create(lotto=self.lotto, paletta=self.pal, prezzo_martello=D("1000"))
        dati = esporta_bytes("segreta")
        with self.assertRaises(Exception):  # senza password non si apre
            load_workbook(io.BytesIO(dati))
        f = msoffcrypto.OfficeFile(io.BytesIO(dati))
        f.load_key(password="segreta")
        out = io.BytesIO()
        f.decrypt(out)
        wb = load_workbook(out)
        self.assertEqual(wb.sheetnames[:3], ["Leggimi", "Anagrafiche", "Indirizzi"])
        agg = list(wb["Aggiudicazioni"].values)
        intest, riga = agg[0], agg[1]
        self.assertEqual(riga[intest.index("Acquirente")], "Cliente Uno")
        self.assertEqual(riga[intest.index("Diritti")], 180)
        self.assertEqual(riga[intest.index("Netto mandante")], 850)
        nomi = [r[3] for r in list(wb["Anagrafiche"].values)[1:]]
        self.assertIn("Mandante Uno", nomi)


class ContabilitaTest(Base):
    """Casi presi dai fogli reali di incassi e pagamenti mandanti."""

    def setUp(self):
        super().setUp()
        call_command("crea_ruoli", verbosity=0)
        from .models import MetodoPagamento
        self.M = {m.sigla: m for m in MetodoPagamento.objects.all()}

    def _agg(self, paletta, prezzo, n):
        l = Lotto.objects.create(asta=self.asta, numero=str(n), mandato=self.mandato, descrizione="x")
        return Aggiudicazione.objects.create(lotto=l, paletta=paletta, prezzo_martello=D(prezzo))

    def test_paletta_9xx_al_23_per_cento(self):
        # foglio incassi, paletta 904: 1.090 + 250,70 + 25 = 1.365,70
        c = Soggetto.objects.create(codice="C9", nome="Online")
        p = Paletta.objects.create(asta=self.asta, numero=904, acquirente=c, diritti_pct=D("23"),
                                   spese_spedizione=D("25"))
        self._agg(p, "1090", 50)
        s = p.situazione_incasso()
        self.assertEqual(s["diritti"], D("250.70"))
        self.assertEqual(s["totale"], D("1365.70"))

    def test_pagamento_su_piu_metodi_e_abbuono(self):
        from .models import Incasso
        self._agg(self.pal, "675", 2)          # paletta 312 del foglio: 675 + 121,50 + 25 = 821,50 − 0,50
        self.pal.spese_spedizione = D("25"); self.pal.abbuono = D("0.50"); self.pal.save()
        self.assertEqual(self.pal.situazione_incasso()["totale"], D("821.00"))
        Incasso.objects.create(paletta=self.pal, data=date(2026, 9, 25), metodo=self.M["BMT"], importo=D("500"))
        self.assertEqual(self.pal.situazione_incasso()["stato"], "parziale")
        Incasso.objects.create(paletta=self.pal, data=date(2026, 9, 29), metodo=self.M["CCR"], importo=D("321"))
        s = self.pal.situazione_incasso()
        self.assertEqual((s["stato"], s["residuo"], s["data_saldo"]), ("incassato", D("0.00"), date(2026, 9, 29)))

    def test_avviso_contanti(self):
        from .models import Incasso
        self._agg(self.pal, "9000", 2)
        Incasso.objects.create(paletta=self.pal, data=date.today(), metodo=self.M["Contanti"], importo=D("5000"))
        self.assertTrue(self.pal.situazione_incasso()["avviso_contanti"])

    def test_iva_mensile_scorporo(self):
        # foglio incassi: 6.451,20 di diritti+spese a ottobre -> 5.287,87 + 1.163,33
        from .models import Incasso, iva_mensile
        self.pal.spese_spedizione = D("0"); self.pal.save()
        a = self._agg(self.pal, "35840", 2)   # diritti 18% = 6.451,20
        Incasso.objects.create(paletta=self.pal, data=date(2026, 10, 15), metodo=self.M["BB B.S."],
                               importo=self.pal.situazione_incasso()["totale"])
        self.assertEqual(a.diritti, D("6451.20"))
        m = iva_mensile(self.asta)[0]
        self.assertEqual((m["mese"], m["imponibile"], m["iva"]), (date(2026, 10, 1), D("5287.87"), D("1163.33")))

    def test_mandante_acconto_e_pagabile(self):
        from .models import Incasso, PagamentoMandante, situazione_mandanti
        self._agg(self.pal, "1000", 2)      # 1000 - 15% - 2 lotti inseriti x 10 € = 830
        r = situazione_mandanti(self.asta)[0]
        self.assertEqual((r["dovuto"], r["pagabile"], r["stato"]), (D("830.00"), D("0.00"), "da pagare"))
        Incasso.objects.create(paletta=self.pal, data=date.today(), metodo=self.M["BB B.S."],
                               importo=self.pal.situazione_incasso()["totale"])
        PagamentoMandante.objects.create(asta=self.asta, mandato=self.mandato, data=date.today(),
                                         metodo=self.M["BB B.R."], importo=D("317"))
        r = situazione_mandanti(self.asta)[0]
        self.assertEqual((r["stato"], r["residuo"], r["pagabile"]), ("acconto", D("513.00"), D("513.00")))
        self.assertTrue(r["iban_mancante"])

    def test_excel_contabile_formato(self):
        import io

        from openpyxl import load_workbook

        from .export_contabile import excel_contabile
        from .models import Incasso
        self.asta.numero = 93; self.asta.save()
        self._agg(self.pal, "1000", 2)
        Incasso.objects.create(paletta=self.pal, data=date(2026, 9, 29), metodo=self.M["BB B.S."], importo=D("1200"))
        wb = load_workbook(io.BytesIO(excel_contabile(self.asta, "incassi")))
        ws = wb.worksheets[0]
        self.assertIn("ASTA  93", ws["A1"].value)
        self.assertEqual(ws["B2"].value, "Nr  Paletta")
        self.assertEqual((ws["B4"].value, ws["C4"].value, ws["G4"].value), (7, "=D4+E4+F4-K4", "BB B.S."))
        self.assertEqual(ws["C4"].fill.fgColor.rgb[-6:], "CCFFCC")  # verde = incassato
        self.assertEqual(wb.sheetnames[1], "IVA mensile")
        ws = load_workbook(io.BytesIO(excel_contabile(self.asta, "mandanti"))).active
        self.assertEqual(ws["B3"].value, "dobbiamo pagare")
        self.assertEqual((ws["A5"].value, ws["B5"].value), ("M1", 830))  # 850 - 2 inserimenti
        self.assertIsNone(ws["A5"].fill.fgColor.rgb if ws["A5"].fill.fill_type else None)  # non pagato = bianco

    def test_contabile_non_vede_nomi_ma_vede_incassi(self):
        u = User.objects.create_user("cont", password="x", is_staff=True)
        u.groups.add(Group.objects.get(name="Contabilità"))
        self.client.force_login(u)
        self._agg(self.pal, "1000", 2)
        r = self.client.get(f"/asta/{self.asta.pk}/incassi/")
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "Cliente Uno")
        self.assertEqual(self.client.get(f"/asta/{self.asta.pk}/excel/mandanti/").status_code, 200)
        self.assertNotContains(self.client.get(f"/asta/{self.asta.pk}/pagamenti-mandanti/"), "Mandante Uno")


class GestioneAsteTest(Base):
    def setUp(self):
        super().setUp()
        self.asta.numero = 93; self.asta.save()
        self.admin = User.objects.create_superuser("adm", password="x")
        self.client.force_login(self.admin)

    def test_elenco_aste_per_stato(self):
        from datetime import timedelta
        Asta.objects.create(numero=94, nome="Futura", data=date.today() + timedelta(days=10))
        Asta.objects.create(numero=92, nome="Vecchia", data=date(2026, 1, 1), chiusa=True)
        r = self.client.get("/aste/")
        self.assertEqual([a.numero for a in r.context["prossime"]], [94])
        self.assertEqual([a.numero for a in r.context["passate"]], [92])
        self.assertEqual([a.numero for a in r.context["in_corso"]], [93])

    def test_nuova_asta_copia_impostazioni(self):
        self.asta.diritti_pct = D("20"); self.asta.save()
        r = self.client.get("/aste/nuova/")
        self.assertEqual(r.context["form"].initial["numero"], 94)
        self.assertEqual(r.context["form"].initial["diritti_pct"], D("20"))
        r = self.client.post("/aste/nuova/", {"numero": 94, "nome": "Asta 94", "data": "2026-12-04",
                                              "diritti_pct": "18", "diritti_online_pct": "23",
                                              "palette_online_da": "900", "diritti_iva_inclusa": "on",
                                              "iva_pct": "22", "numero_tornate": "2", "data_fine": "2026-12-05", "giorni_post_asta": "30"})
        nuova = Asta.objects.get(numero=94)
        self.assertRedirects(r, f"/asta/{nuova.pk}/lotti/")

    def test_regola_palette_online(self):
        c = Soggetto.objects.create(codice="C9", nome="x")
        self.assertEqual(Paletta.objects.create(asta=self.asta, numero=905, acquirente=c).diritti_effettivi, D("23"))
        self.assertEqual(self.pal.diritti_effettivi, D("18"))  # paletta 7

    def test_aggiungi_lotto_e_ordine(self):
        for n in ["2", "9", "10"]:
            r = self.client.post(f"/asta/{self.asta.pk}/lotti/", {"numero": n, "mandato": "M1", "descrizione": "d",
                                                                 "base_asta": "1.500"})
            self.assertEqual(r.status_code, 302)
        self.assertEqual(list(self.asta.lotti.values_list("numero", flat=True)), ["1", "2", "9", "10"])
        self.assertEqual(Lotto.objects.get(asta=self.asta, numero="10").base_asta, D("1500"))
        r = self.client.post(f"/asta/{self.asta.pk}/lotti/", {"numero": "10", "mandato": "M1", "descrizione": "d"})
        self.assertContains(r, "esiste già")

    def test_import_excel_con_anteprima(self):
        import io

        from django.core.files.uploadedfile import SimpleUploadedFile
        from openpyxl import Workbook
        wb = Workbook(); ws = wb.active
        ws.append(["Lotto", "Mandato", "Descrizione", "Stima min", "Base"])
        ws.append([20, "M1", "Tavola", 300, "1.200,00"])
        ws.append([None, "1", "Senza numero (mandato per numero)", None, 100])
        ws.append([21, "MXXX", "Mandante sbagliato", None, None])
        ws.append([1, "M1", "Numero già esistente", None, None])
        buf = io.BytesIO(); wb.save(buf)
        url = f"/asta/{self.asta.pk}/lotti/importa/"
        r = self.client.post(url, {"file": SimpleUploadedFile("lotti.xlsx", buf.getvalue())})
        # numero doppio: non caricabile; mandante sconosciuto: si carica lo stesso con una scheda da completare
        self.assertEqual((r.context["n_ok"], r.context["n_err"], r.context["n_avvisi"]), (2, 1, 1))
        self.assertEqual(self.asta.lotti.count(), 1)  # niente salvato prima della conferma
        self.client.post(url, {"azione": "conferma"})
        self.assertEqual(sorted(self.asta.lotti.values_list("numero", flat=True), key=int), ["1", "2", "20", "21"])
        nuovo = Lotto.objects.get(asta=self.asta, numero="21").mandato
        self.assertEqual((nuovo.mandante.nome, nuovo.mandante.is_mandante), ("MXXX", True))
        self.assertEqual(Lotto.objects.get(asta=self.asta, numero="20").base_asta, D("1200"))

    def test_riproponi_invenduto(self):
        self.lotto.stato = Lotto.Stato.INVENDUTO; self.lotto.save()
        nuova = Asta.objects.create(numero=94, nome="94", data=date(2026, 12, 4))
        self.client.post(f"/asta/{nuova.pk}/lotti/riproponi/", {"lotti": [self.lotto.pk], f"base_{self.lotto.pk}": "80"})
        n = nuova.lotti.get()
        self.assertEqual((n.numero, n.base_asta, n.lotto_origine), ("1", D("80"), self.lotto))
        self.lotto.refresh_from_db()
        self.assertEqual(self.lotto.stato, Lotto.Stato.RIPROPOSTO)

    def test_asta_conclusa_blocca_sala(self):
        self.client.post(f"/asta/{self.asta.pk}/conclusa/", {"chiusa": "1"})
        self.client.post(f"/asta/{self.asta.pk}/sala/", {"lotto": "1", "paletta": "7", "prezzo": "100"})
        self.assertFalse(Aggiudicazione.objects.exists())


class AnagraficaTest(TestCase):
    def setUp(self):
        call_command("crea_ruoli", verbosity=0)
        self.admin = User.objects.create_superuser("adm", password="x")
        self.client.force_login(self.admin)
        self.asta = Asta.objects.create(numero=94, nome="Futura", data=date(2099, 1, 1))

    def _dati(self, **extra):
        d = {"tipo": "privato", "nome_proprio": "Mario", "cognome": "Rossi", "is_cliente": "on",
             "email": "mario@example.com", "telefono": "+39 333 1234567", "iban": "it60 x054 2811 1010 0000 0123 456",
             "dom-via": "Via Roma 1", "dom-cap": "40121", "dom-citta": "Bologna", "dom-provincia": "BO",
             "dom-nazione": "Italia"}
        d.update(extra)
        return d

    def test_nuova_scheda_con_spedizione_diversa_e_interessi(self):
        from .models import Interesse
        fumetti = Interesse.objects.get(nome="Fumetti")
        r = self.client.post("/anagrafica/nuova/", self._dati(
            interessi=[fumetti.pk], nuovo_interesse="figurine", spedizione_diversa="1",
            **{"sped-via": "Corso Italia 5", "sped-cap": "20121", "sped-citta": "Milano", "sped-nazione": "Italia"}))
        s = Soggetto.objects.get()
        self.assertRedirects(r, f"/anagrafica/{s.pk}/")
        self.assertEqual((s.nome, s.codice, s.iban), ("Rossi Mario", "1", "IT60X0542811101000000123456"))
        self.assertEqual(sorted(i.nome for i in s.interessi.all()), ["Figurine", "Fumetti"])
        self.assertEqual(s.domicilio.citta, "Bologna")
        self.assertEqual(s.indirizzo_spedizione.citta, "Milano")
        # togliendo la spunta si torna a spedire al domicilio
        self.client.post(f"/anagrafica/{s.pk}/", self._dati(interessi=[fumetti.pk]))
        self.assertEqual(Soggetto.objects.get().indirizzo_spedizione.citta, "Bologna")

    def test_ditta_e_controlli(self):
        r = self.client.post("/anagrafica/nuova/", self._dati(tipo="azienda", is_cliente=""))
        self.assertContains(r, "Indica la ragione sociale")
        self.assertContains(r, "cliente, mandante o entrambi")
        self.client.post("/anagrafica/nuova/", self._dati(tipo="azienda", ragione_sociale="Galleria Blu srl",
                                                           is_cliente="", is_mandante="on"))
        s = Soggetto.objects.get()
        self.assertEqual((s.nome, s.is_mandante), ("Galleria Blu srl", True))

    def test_ricerca_e_storico_acquisti(self):
        self.client.post("/anagrafica/nuova/", self._dati())
        s = Soggetto.objects.get()
        r = self.client.get("/anagrafica/?q=bologna")
        self.assertEqual([x.pk for x in r.context["schede"]], [s.pk])
        self.assertEqual(len(self.client.get("/anagrafica/?q=torino").context["schede"]), 0)
        m = Soggetto.objects.create(nome="Mand", is_mandante=True)
        mandato = Mandato.objects.create(mandante=m, numero="X1", data=date.today(), commissione_pct=10)
        p = Paletta.objects.create(asta=self.asta, numero=101, acquirente=s)
        l = Lotto.objects.create(asta=self.asta, numero="1", mandato=mandato, descrizione="Tavola rara")
        Aggiudicazione.objects.create(lotto=l, paletta=p, prezzo_martello=D("500"))
        r = self.client.get(f"/anagrafica/{s.pk}/")
        self.assertContains(r, "Tavola rara")
        self.assertContains(self.client.get(f"/anagrafica/{m.pk}/"), "Lotti affidati")

    def test_assegna_paletta_senza_consegna(self):
        self.client.post("/anagrafica/nuova/", self._dati())
        s = Soggetto.objects.get()
        r = self.client.get(f"/palette/?asta={self.asta.pk}")
        self.assertEqual((r.context["num_sala"], r.context["num_online"]), (101, 900))
        self.assertNotContains(r, "ritiro in sede")
        self.client.post(f"/palette/?asta={self.asta.pk}", {"cliente": s.pk, "numero": "101"})
        p = Paletta.objects.get()
        self.assertEqual((p.numero, p.indirizzo_spedizione, p.modalita, p.stato_consegna), (101, None, None, "da_definire"))
        r = self.client.post(f"/palette/?asta={self.asta.pk}", {"cliente": s.pk, "numero": "102"})
        self.assertContains(r, "già una paletta", status_code=200)

    def test_home(self):
        r = self.client.get("/")
        self.assertContains(r, "Cosa vuoi fare?")
        self.assertEqual([a.numero for a in r.context["prossime"]], [94])


def _catalogo_word() -> bytes:
    """Un piccolo catalogo con la stessa struttura delle «Schede Proprietà»."""
    import io

    import docx
    d = docx.Document()
    d.add_paragraph().add_run("LOCANDINE").bold = True
    d.add_paragraph().add_run("FANTASCIENZA U.S.A. ANNI ‘50").bold = True
    d.add_paragraph("00. Dr. Cyclops")
    d.add_paragraph("USA, regia di E. Schoedsack. Fotobusta in prima edizione italiana.")
    d.add_paragraph("€ 40/80 LIBRERIA ALFA")
    d.add_paragraph("")
    d.add_paragraph("00. L’Astronave Atomica")
    d.add_paragraph("Locandina in prima edizione italiana, maggio 1956.")
    d.add_paragraph("Qualità: A-.")
    d.add_paragraph("€ 1.200/2.500 R 1.300 BRUNI DA FOTOGRAFARE")
    d.add_paragraph().add_run("HORROR").bold = True
    d.add_paragraph("00. Demoni 2")
    d.add_paragraph("Locandina firmata da Lamberto Bava con dedica")
    d.add_paragraph("DA FOTOGRAFARE")
    d.add_paragraph("00. Senza mandante noto")
    d.add_paragraph("Descrizione.")
    d.add_paragraph("€ 50/100 SCONOSCIUTO")
    d.add_paragraph().add_run("PROSSIME ASTE O VENDITA DIRETTA").bold = True
    d.add_paragraph("00. Guerre Stellari")
    d.add_paragraph("Locandina 1980.")
    d.add_paragraph("€ 100/200")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


class CatalogoFaldoneTest(TestCase):
    def setUp(self):
        call_command("crea_ruoli", verbosity=0)
        self.client.force_login(User.objects.create_superuser("adm", password="x"))
        self.asta = Asta.objects.create(numero=91, nome="Locandine", data=date(2099, 1, 1), numero_tornate=2)
        self.bruni = Soggetto.objects.create(nome_proprio="Mario", cognome="Bruni", is_mandante=True)
        self.alfa = Soggetto.objects.create(tipo="azienda", ragione_sociale="Libreria Alfa srl", is_mandante=True)
        self.m_cov = Mandato.objects.create(mandante=self.bruni, numero="4017", data=date.today(), commissione_pct=15)
        self.m_ven = Mandato.objects.create(mandante=self.alfa, numero="4009", data=date.today(), commissione_pct=10)

    def test_lettura_word(self):
        from .import_word import leggi_catalogo_word
        L = leggi_catalogo_word(_catalogo_word(), self.asta, 1)
        self.assertEqual([l["numero"] for l in L], ["1", "2", "3", "4", ""])
        a, b, c, d, e = L
        self.assertEqual((a["titolo"], a["base"], a["stima"], a["sezione"], a["mandato_id"]),
                         ("Dr. Cyclops", "40", "80", "FANTASCIENZA U.S.A. ANNI ‘50", self.m_ven.pk))
        self.assertEqual((b["base"], b["stima"], b["riserva"], b["mandato_id"], b["da_fotografare"]),
                         ("1200", "2500", "1300", self.m_cov.pk, True))
        self.assertIn("Qualità: A-.", b["descrizione"])
        self.assertEqual((c["sezione"], c["da_fotografare"], c["base"]), ("HORROR", True, None))
        self.assertEqual((d["mandante_testo"], d["mandato_id"]), ("SCONOSCIUTO", None))
        self.assertTrue(e["escluso"])

    def test_importazione_con_abbinamento_e_tornate(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        url = f"/asta/{self.asta.pk}/lotti/importa/"
        r = self.client.post(url, {"file": SimpleUploadedFile("Schede.docx", _catalogo_word())})
        self.assertEqual((r.context["n_ok"], r.context["n_avvisi"], r.context["n_err"], r.context["n_esclusi"]),
                         (2, 2, 0, 1))
        testi = [t for _, t, _, _ in r.context["da_abbinare"]]
        self.assertEqual(testi, ["", "SCONOSCIUTO"])
        r = self.client.post(url, {"azione": "conferma", "testo_mandante": testi,
                                   "abbina_0": self.m_cov.pk, "abbina_1": self.m_ven.pk, "tornata_2": "3"})
        lotti = {l.numero: l for l in self.asta.lotti.all()}
        self.assertEqual(sorted(lotti, key=int), ["1", "2", "3", "4"])
        self.assertEqual((lotti["1"].tornata, lotti["3"].tornata, lotti["4"].mandato, lotti["3"].mandato),
                         (1, 2, self.m_ven, self.m_cov))
        self.assertEqual((lotti["2"].riserva, lotti["2"].stima_max, lotti["2"].titolo), (D("1300"), D("2500"),
                                                                                         "L’Astronave Atomica"))

    def test_carica_anche_senza_abbinare(self):
        """Quello che il programma non capisce si carica lo stesso e resta segnato «da verificare»."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        url = f"/asta/{self.asta.pk}/lotti/importa/"
        self.client.post(url, {"file": SimpleUploadedFile("Schede.docx", _catalogo_word())})
        self.client.post(url, {"azione": "conferma"})
        lotti = {l.numero: l for l in self.asta.lotti.select_related("mandato__mandante")}
        self.assertEqual(sorted(lotti, key=int), ["1", "2", "3", "4"])
        self.assertEqual(lotti["4"].mandato.mandante.nome, "SCONOSCIUTO")
        self.assertEqual(lotti["3"].mandato.mandante.nome, "MANDANTE DA ABBINARE")
        self.assertTrue(lotti["3"].note_interne.startswith("DA VERIFICARE"))
        self.assertFalse(lotti["4"].note_interne.startswith("DA VERIFICARE"))
        r = self.client.get(f"/asta/{self.asta.pk}/lotti/?verifica=1")
        self.assertEqual([l.numero for l in r.context["lotti"]], ["3"])

    def test_varianti_catalogo(self):
        """Varianti trovate nelle schede reali: punto attaccato, riserva «R.150», prezzo senza €, a capo, tornate."""
        import io

        import docx

        from .import_word import leggi_catalogo_word
        d = docx.Document()
        d.add_paragraph().add_run("FUMETTI").bold = True
        d.add_paragraph().add_run("3.Pinocchio").bold = True
        d.add_paragraph("Foglio sciolto.\n€175/400 BRUNI R.150")
        d.add_paragraph("FINE PRIMA TORNATA")
        d.add_paragraph().add_run("10. HUGO PRATT").bold = True
        d.add_paragraph().add_run("Corto Maltese").bold = True
        d.add_paragraph("1991. Raccolta di litografie.")
        d.add_paragraph("La coppia € 150/350 R. 250 ALFA MANCA FOTO")
        d.add_paragraph().add_run("00. Senza numero").bold = True
        d.add_paragraph("150/300 BRUNI")
        buf = io.BytesIO(); d.save(buf)
        a, b, c = leggi_catalogo_word(buf.getvalue(), self.asta, 1)
        self.assertEqual((a["numero"], a["titolo"], a["base"], a["stima"], a["riserva"], a["mandato_id"], a["tornata"]),
                         ("3", "Pinocchio", "175", "400", "150", self.m_cov.pk, None))
        self.assertEqual((b["numero"], b["illustratore"], b["titolo"], b["riserva"], b["mandato_id"], b["tornata"]),
                         ("10", "HUGO PRATT", "Corto Maltese", "250", self.m_ven.pk, 2))
        self.assertIn("1991. Raccolta", b["descrizione"])
        self.assertTrue(b["da_fotografare"])
        self.assertEqual((c["numero"], c["base"], c["mandato_id"]), ("11", "150", self.m_cov.pk))

    def test_faldone(self):
        import io
        from datetime import time

        from openpyxl import load_workbook

        from .models import Tornata
        Tornata.objects.create(asta=self.asta, numero=2, ora=time(18, 0))
        Lotto.objects.create(asta=self.asta, numero="1", tornata=1, titolo="Bombolo n. 1", descrizione="Settimanale",
                             mandato=self.m_ven, base_asta=100, stima_max=200, descrizione_breve="42 fascicoli")
        Lotto.objects.create(asta=self.asta, numero="2", tornata=2, titolo="Corto", illustratore="HUGO PRATT",
                             mandato=self.m_cov, base_asta=400, riserva=500, stima_max=900)
        r = self.client.get(f"/asta/{self.asta.pk}/faldone/")
        self.assertEqual(r["Content-Disposition"], 'attachment; filename="FALDONE_AT91.xlsx"')
        wb = load_workbook(io.BytesIO(r.content))
        self.assertEqual(wb.sheetnames, ["Foglio1", "DAPHNE ", "SERGIO", "SECONDA TORNATA", "FAX", "ASSICURAZIONE"])
        f1 = wb["Foglio1"]
        self.assertEqual([c.value for c in f1[1]][:5], ["Tornate", "Lotto", "Bis", "Note", "Titolo"])
        self.assertEqual([f1.cell(2, c).value for c in (1, 2, 5, 11, 12, 13, 14, 16)],
                         [1, 1, "Bombolo n. 1", 100, 100, 200, "LIBRERIA ALFA SRL", 4009])
        self.assertEqual(f1.cell(3, 12).value, 500)            # Prezzo = riserva
        s = wb["SERGIO"]
        self.assertEqual([s.cell(2, c).value for c in (1, 2, 3, 4, 5)], [1, "Bombolo n. 1", "42 fascicoli", 100, None])
        self.assertEqual(s.cell(3, 1).value, "FINE PRIMA TORNATA - INIZIO SECONDA TORNATA ORE 18:00")
        self.assertEqual([s.cell(4, c).value for c in (1, 3, 5)], [2, "HUGO PRATT", 500])
        self.assertEqual(wb["SECONDA TORNATA"].cell(2, 2).value, "Corto")
        self.assertEqual(wb["ASSICURAZIONE"].cell(4, 3).value, "=SUM(C2:C3)")

    def test_orari_tornate_nell_asta(self):
        from .models import Tornata
        self.client.post(f"/asta/{self.asta.pk}/modifica/", {
            "numero": 91, "nome": "Locandine", "data": "2099-01-01", "numero_tornate": "3", "diritti_pct": "18",
            "diritti_online_pct": "23", "palette_online_da": "900", "iva_pct": "22", "tornata_1_ora": "15:00",
            "giorni_post_asta": "30",
            "tornata_2_ora": "18:00", "tornata_3_data": "2099-01-02", "tornata_3_ora": "10:30"})
        t = {x.numero: x for x in Tornata.objects.filter(asta=self.asta)}
        self.assertEqual((str(t[1].ora), str(t[2].ora), str(t[3].data), str(t[3].ora)),
                         ("15:00:00", "18:00:00", "2099-01-02", "10:30:00"))


class SalaTest(Base):
    def setUp(self):
        super().setUp()
        call_command("crea_ruoli", verbosity=0)
        u = User.objects.create_user("clerk", password="x", is_staff=True)
        u.groups.add(Group.objects.get(name="Clerk di sala"))
        self.client.force_login(u)
        self.lotto.riserva = D("500"); self.lotto.base_asta = D("300"); self.lotto.save()
        self.l2 = Lotto.objects.create(asta=self.asta, numero="2", mandato=self.mandato, descrizione="y", tornata=1)
        self.url = f"/asta/{self.asta.pk}/sala/"

    def _post(self, **d):
        import json
        return self.client.post(self.url, json.dumps(d), content_type="application/json").json()

    def test_aggiudica_riga(self):
        r = self._post(lotto=self.l2.pk, paletta="7", prezzo="1.200")
        self.assertTrue(r["ok"])
        self.assertEqual((r["lotto"]["stato"], r["lotto"]["paletta"], r["lotto"]["prezzo"]), ("aggiudicato", 7, "1200.00"))

    def test_sotto_riserva_chiede_conferma(self):
        r = self._post(lotto=self.lotto.pk, paletta="7", prezzo="400")
        self.assertTrue(r["conferma"])
        self.assertIn("sotto la riserva", r["errore"])
        self.assertFalse(Aggiudicazione.objects.exists())
        r = self._post(lotto=self.lotto.pk, paletta="7", prezzo="400", conferma=True)
        self.assertTrue(r["ok"] and r["lotto"]["sotto_riserva"])

    def test_paletta_sconosciuta_invenduto_e_correggi(self):
        r = self._post(lotto=self.l2.pk, paletta="999", prezzo="100")
        self.assertEqual((r["ok"], r["campo"]), (False, "paletta"))
        self.assertTrue(self._post(lotto=self.l2.pk, azione="invenduto")["ok"])
        self.l2.refresh_from_db(); self.assertEqual(self.l2.stato, "invenduto")
        self.assertTrue(self._post(lotto=self.l2.pk, azione="annulla")["ok"])
        self._post(lotto=self.l2.pk, paletta="7", prezzo="100")
        r = self._post(lotto=self.l2.pk, paletta="7", prezzo="150")
        self.assertIn("già aggiudicato", r["errore"])
        self._post(lotto=self.l2.pk, azione="annulla")       # correzione: si toglie e si rifà
        self.assertFalse(Aggiudicazione.objects.exists())
        self.l2.refresh_from_db(); self.assertEqual(self.l2.stato, "in_asta")

    def test_pagina_e_aggiornamento(self):
        r = self.client.get(self.url)
        self.assertContains(r, "Aggiudicazione lotti")
        self.assertNotContains(r, "Cliente Uno")
        self._post(lotto=self.l2.pk, paletta="7", prezzo="100")
        d = self.client.get(self.url + "?formato=json").json()
        self.assertEqual({x["numero"]: x["stato"] for x in d["lotti"]}, {"1": "in_asta", "2": "aggiudicato"})


class FascePostAstaTest(Base):
    def setUp(self):
        super().setUp()
        from datetime import timedelta
        call_command("crea_ruoli", verbosity=0)
        self.client.force_login(User.objects.create_superuser("adm", password="x"))
        self.asta.data = date.today() - timedelta(days=5)
        self.asta.save()
        self.asta.crea_fasce()
        self.cli2 = Soggetto.objects.create(nome="Nuovo", is_cliente=True)

    def test_fasce_e_diritti(self):
        from .views_anagrafica import prossima_per_canale
        tipi = {n: self.asta.fascia_di(n).tipo for n in (1, 250, 350, 450, 850, 950)}
        self.assertEqual(tipi, {1: "sala", 250: "scritte", 350: "telefono", 450: "sala", 850: "post_asta", 950: "live"})
        c = Soggetto.objects.create(nome="Live")
        self.assertEqual(Paletta.objects.create(asta=self.asta, numero=901, acquirente=c).diritti_effettivi, D("23"))
        self.assertEqual(self.pal.diritti_effettivi, D("18"))
        self.assertEqual((prossima_per_canale(self.asta, "telefono"), prossima_per_canale(self.asta, "sala")), (300, 1))
        r = self.client.post(f"/palette/?asta={self.asta.pk}", {"cliente": self.cli2.pk, "numero": "1000"})
        self.assertContains(r, "non rientra in nessuna fascia")

    def test_vendita_post_asta(self):
        self.lotto.stato = Lotto.Stato.INVENDUTO; self.lotto.riserva = D("300"); self.lotto.save()
        url = f"/asta/{self.asta.pk}/post-asta/"
        self.assertTrue(self.asta.post_asta_aperto)
        self.client.post(url, {"lotto": self.lotto.pk, "cliente": self.cli2.pk, "prezzo": "250"})
        self.assertFalse(Aggiudicazione.objects.exists())                 # sotto riserva senza conferma
        self.client.post(url, {"lotto": self.lotto.pk, "cliente": self.cli2.pk, "prezzo": "350"})
        a = Aggiudicazione.objects.get()
        self.assertEqual((a.post_asta, a.paletta.numero, a.paletta.acquirente), (True, 800, self.cli2))
        self.lotto.refresh_from_db(); self.assertEqual(self.lotto.stato, "aggiudicato")
        # chi ha già una paletta nell'asta usa quella
        l2 = Lotto.objects.create(asta=self.asta, numero="2", mandato=self.mandato, descrizione="y", stato="invenduto")
        self.client.post(url, {"lotto": l2.pk, "cliente": self.cli.pk, "prezzo": "100"})
        self.assertEqual(Aggiudicazione.objects.get(lotto=l2).paletta, self.pal)
        # annulla: il lotto torna invenduto
        self.client.post(url, {"lotto": l2.pk, "azione": "annulla"})
        l2.refresh_from_db(); self.assertEqual(l2.stato, "invenduto")

    def test_post_asta_chiuso(self):
        from datetime import timedelta
        self.asta.data = date.today() - timedelta(days=40); self.asta.save()
        self.lotto.stato = Lotto.Stato.INVENDUTO; self.lotto.save()
        r = self.client.post(f"/asta/{self.asta.pk}/post-asta/", {"lotto": self.lotto.pk, "cliente": self.cli2.pk,
                                                                  "prezzo": "350"}, follow=True)
        self.assertContains(r, "Il post asta è chiuso")
        self.assertFalse(Aggiudicazione.objects.exists())


class AcquirentiEmailTest(Base):
    def setUp(self):
        super().setUp()
        from .models import Indirizzo
        call_command("crea_ruoli", verbosity=0)
        self.client.force_login(User.objects.create_superuser("adm", password="x"))
        self.cli.email = "cliente@example.com"; self.cli.save()
        Indirizzo.objects.create(soggetto=self.cli, tipo="domicilio", via="Rue X 1", cap="1000", citta="Bruxelles",
                                 nazione="Belgio")
        self.asta.numero = 93; self.asta.save()
        self.lotto.titolo = "Tavola di Pratt"; self.lotto.save()
        self.pal.spese_spedizione = 0; self.pal.save()
        Aggiudicazione.objects.create(lotto=self.lotto, paletta=self.pal, prezzo_martello=D("1000"))
        self.url = f"/asta/{self.asta.pk}/acquirenti/"

    def test_proposta_e_scelta_consegna(self):
        from .models import ModalitaConsegna
        r = self.client.get(self.url)
        riga = r.context["righe"][0]
        self.assertEqual(riga["proposta"].nome, "Spedizione UE")       # proposta in base alla nazione
        self.assertIsNone(riga["p"].modalita)                        # ma non ancora salvata
        ue = ModalitaConsegna.objects.get(nome="Spedizione UE")
        self.client.post(self.url, {"paletta": self.pal.pk, "modalita": ue.pk, "spese": "45"})
        self.pal.refresh_from_db()
        self.assertEqual((self.pal.modalita, self.pal.spese_spedizione, self.pal.indirizzo_spedizione.citta,
                          self.pal.stato_consegna), (ue, D("45"), "Bruxelles", "da_spedire"))
        self.assertEqual(self.pal.situazione_incasso()["totale"], D("1225.00"))
        ritiro = ModalitaConsegna.objects.get(nome="Ritiro in sede")
        self.client.post(self.url, {"paletta": self.pal.pk, "modalita": ritiro.pk})
        self.pal.refresh_from_db()
        self.assertEqual((self.pal.spese_spedizione, self.pal.indirizzo_spedizione, self.pal.stato_consegna),
                         (D("0"), None, "ritira"))

    def test_email_per_outlook(self):
        import email
        from .models import ModalitaConsegna
        ue = ModalitaConsegna.objects.get(nome="Spedizione UE")
        self.client.post(self.url, {"paletta": self.pal.pk, "modalita": ue.pk, "spese": "40"})
        with self.settings(CASA_ASTA_IBAN="IT00X0000000000000000000000", CASA_ASTA_EMAIL="asta@example.com"):
            self.assertContains(self.client.get(f"{self.url}7/email/"), "Tavola di Pratt")
            r = self.client.get(f"{self.url}7/email.eml")
        from email import policy
        m = email.message_from_bytes(r.content, policy=policy.default)
        self.assertEqual((m["X-Unsent"], m["To"]), ("1", "Cliente Uno <cliente@example.com>"))
        self.assertIn("paletta 7", m["Subject"])
        testo = m.get_body(("plain",)).get_content()
        for atteso in ["Lotto 1: Tavola di Pratt", "TOTALE: € 1.220,00", "IT00X0000000000000000000000", "Bruxelles"]:
            self.assertIn(atteso, testo)
        self.assertIn("<table", m.get_body(("html",)).get_content())
        self.pal.refresh_from_db(); self.assertIsNotNone(self.pal.email_preparata_il)

    def test_zip_solo_con_consegna_decisa(self):
        import io
        import zipfile

        from .models import ModalitaConsegna
        r = self.client.get(f"{self.url}email.zip")
        self.assertEqual(r.status_code, 302)                         # nessuna consegna decisa: niente da preparare
        self.client.post(self.url, {"paletta": self.pal.pk, "modalita": ModalitaConsegna.objects.first().pk})
        z = zipfile.ZipFile(io.BytesIO(self.client.get(f"{self.url}email.zip").content))
        self.assertEqual(z.namelist(), ["Asta93_paletta7.eml"])


class ConteggioPdfTest(AcquirentiEmailTest):
    def _testo_pdf(self, dati: bytes) -> str:
        import shutil
        import subprocess
        import tempfile
        if not shutil.which("pdftotext"):
            self.skipTest("pdftotext non disponibile")
        with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
            f.write(dati); f.flush()
            return subprocess.run(["pdftotext", "-layout", f.name, "-"], capture_output=True, text=True).stdout

    def test_pdf_paletta(self):
        from .models import ModalitaConsegna
        self.client.post(self.url, {"paletta": self.pal.pk, "modalita": ModalitaConsegna.objects.get(nome="Spedizione UE").pk,
                                    "spese": "20"})
        r = self.client.get(f"{self.url}7/conteggio.pdf")
        self.assertEqual(r["Content-Type"], "application/pdf")
        t = self._testo_pdf(r.content)
        for atteso in ["Aggiudicazioni relative alla Paletta N.", "Cliente Uno", "cliente@example.com",
                       "Tavola di Pratt", "1.000,00", "Totale Aggiudicazioni", "18,00 %", "180,00 €",
                       "Spedizione UE", "20,00 €", "TOTALE GENERALE", "1.200,00 €"]:
            self.assertIn(atteso, t)

    def test_pdf_tutte_e_allegato_email(self):
        import email
        from email import policy

        from .models import ModalitaConsegna
        c2 = Soggetto.objects.create(nome="Secondo", email="due@example.com")
        p2 = Paletta.objects.create(asta=self.asta, numero=8, acquirente=c2)
        l2 = Lotto.objects.create(asta=self.asta, numero="2", mandato=self.mandato, descrizione="Altro lotto")
        Aggiudicazione.objects.create(lotto=l2, paletta=p2, prezzo_martello=D("50"))
        t = self._testo_pdf(self.client.get(f"{self.url}conteggi.pdf").content)
        self.assertEqual(t.count("Aggiudicazioni relative alla Paletta N."), 2)
        self.client.post(self.url, {"paletta": self.pal.pk, "modalita": ModalitaConsegna.objects.first().pk})
        m = email.message_from_bytes(self.client.get(f"{self.url}7/email.eml").content, policy=policy.default)
        allegati = [a.get_filename() for a in m.iter_attachments()]
        self.assertEqual(allegati, ["Aggiudicazioni_Asta93_Paletta7.pdf"])


class RendicontoMandanteTest(TestCase):
    """Riproduce il rendiconto mandante d'esempio: 5 lotti inseriti, 2 venduti a 100 €, 18%, restauro 20 €."""

    def setUp(self):
        from .models import SpesaMandante
        self.asta = Asta.objects.create(numero=93, nome="T", data=date(2026, 9, 25))
        self.m = Soggetto.objects.create(nome_proprio="Mario", cognome="Rossi", is_mandante=True)
        self.mandato = Mandato.objects.create(mandante=self.m, numero="4021", data=date.today(), commissione_pct=D("18"))
        cli = Soggetto.objects.create(nome="Compratore")
        pal = Paletta.objects.create(asta=self.asta, numero=101, acquirente=cli)
        self.lotti = [Lotto.objects.create(asta=self.asta, numero=str(n), mandato=self.mandato, titolo=f"Lotto {n}")
                      for n in (273, 274, 275, 276, 281)]
        for l in (self.lotti[0], self.lotti[4]):
            Aggiudicazione.objects.create(lotto=l, paletta=pal, prezzo_martello=D("100"))
        SpesaMandante.objects.create(asta=self.asta, mandato=self.mandato, descrizione="Spese piccolo restauro",
                                     importo=D("20"))

    def test_conti_come_nel_documento(self):
        from .models import situazione_mandanti
        r = rendiconto_mandante(self.asta, self.m)
        t = r["totali"]
        self.assertEqual((t["martello"], t["commissione"], t["iva_commissione"], t["spese"], t["netto"]),
                         (D("200.00"), D("36.00"), D("0.00"), D("70.00"), D("94.00")))
        self.assertEqual([(i["n"], i["importo"]) for i in r["inserimenti"]], [(5, D("50.00"))])
        self.assertEqual(r["percentuale"], D("18"))
        self.assertEqual(situazione_mandanti(self.asta)[0]["dovuto"], D("94.00"))

    def test_numero_progressivo(self):
        from .models import RendicontoMandante
        a = RendicontoMandante.per(self.asta, self.m)
        self.assertEqual(RendicontoMandante.per(self.asta, self.m).numero, a.numero)   # stesso numero se rifatto
        altro = Soggetto.objects.create(nome="Altro", is_mandante=True)
        self.assertEqual(RendicontoMandante.per(self.asta, altro).numero, a.numero + 1)


class MandantiPaginaTest(RendicontoMandanteTest):
    def setUp(self):
        super().setUp()
        from .models import Indirizzo
        call_command("crea_ruoli", verbosity=0)
        self.client.force_login(User.objects.create_superuser("adm", password="x"))
        self.m.email = "mario@example.com"; self.m.iban = "IT00X0000000000000000000001"; self.m.save()
        Indirizzo.objects.create(soggetto=self.m, tipo="domicilio", via="Via Roma 1", cap="10070", citta="Fiano",
                                 provincia="TO")
        self.url = f"/asta/{self.asta.pk}/mandanti/"

    def test_pagina_e_pdf_come_il_documento(self):
        r = self.client.get(self.url)
        self.assertEqual(r.context["righe"][0]["r"]["totali"]["netto"], D("94.00"))
        pdf = self.client.get(f"{self.url}{self.m.pk}/rendiconto.pdf").content
        t = ConteggioPdfTest._testo_pdf(self, pdf)
        for atteso in ["Spett.le", "ROSSI MARIO", "VIA ROMA 1", "Mandato N. 4021", "Lotto", "273", "281",
                       "SPESE INSERIMENTO LOTTI NR. 5 x 10€ = 50€", "ADDEBITO SPESE PICCOLO RESTAURO = 20€",
                       "200,00", "18,00 % Diritti d'Asta", "36,00", "70,00", "94,00"]:
            self.assertIn(atteso, t)

    def test_aggiungi_e_togli_spesa(self):
        from .models import SpesaMandante
        self.client.post(self.url, {"azione": "spesa", "mandato": self.mandato.pk, "descrizione": "Foto",
                                    "importo": "15"})
        self.assertEqual(rendiconto_mandante(self.asta, self.m)["totali"]["netto"], D("79.00"))
        s = SpesaMandante.objects.get(descrizione="Foto")
        self.client.post(self.url, {"azione": "togli_spesa", "spesa": s.pk})
        self.assertEqual(rendiconto_mandante(self.asta, self.m)["totali"]["netto"], D("94.00"))

    def test_email_outlook_e_invio_diretto(self):
        import email
        from email import policy

        from django.core import mail
        m = email.message_from_bytes(self.client.get(f"{self.url}{self.m.pk}/email.eml").content, policy=policy.default)
        self.assertEqual(m["X-Unsent"], "1")
        self.assertIn("mario@example.com", m["To"])
        self.assertIn("94,00", m.get_body(("plain",)).get_content())
        self.assertEqual([a.get_content_type() for a in m.iter_attachments()], ["application/pdf"])
        # senza casella configurata non parte niente
        self.client.post(self.url, {"azione": "invia", "mandante": self.m.pk})
        self.assertEqual(len(mail.outbox), 0)
        with self.settings(ASTA_SMTP_HOST="smtp.example.com",
                           ASTA_EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            self.client.post(self.url, {"azione": "invia_tutti"})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("rendiconto", mail.outbox[0].subject)
        from .models import RendicontoMandante
        self.assertIsNotNone(RendicontoMandante.objects.get(mandante=self.m).email_inviata_il)


class Microsoft365Test(TestCase):
    """Invio tramite Microsoft Graph, con le chiamate a Microsoft simulate (nessun collegamento reale)."""

    def test_invio_graph(self):
        import json
        from unittest import mock

        from . import email_util
        chiamate = []

        def finto(url, dati, intestazioni):
            chiamate.append((url, dati, intestazioni))
            if "oauth2" in url:
                return 200, {"access_token": "TOKEN", "expires_in": 3600}
            return 202, {}
        email_util._token.update(valore=None, scade=0)
        with self.settings(ASTA_M365_TENANT_ID="t", ASTA_M365_CLIENT_ID="c", ASTA_M365_CLIENT_SECRET="s",
                           ASTA_M365_MITTENTE="aste@example.com"), mock.patch.object(email_util, "_http_json", finto):
            self.assertTrue(email_util.invio_diretto_attivo())
            em = email_util.costruisci("Oggetto", "Mario Rossi", "mario@example.com", "testo", "<p>html</p>",
                                       [("doc.pdf", b"%PDF-1.4", "application/pdf")])
            email_util.invia(em)
        self.assertIn("login.microsoftonline.com/t/", chiamate[0][0])
        url, dati, intest = chiamate[1]
        self.assertEqual(url, "https://graph.microsoft.com/v1.0/users/aste%40example.com/sendMail")
        self.assertEqual(intest["Authorization"], "Bearer TOKEN")
        msg = json.loads(dati)
        self.assertTrue(msg["saveToSentItems"])
        self.assertEqual(msg["message"]["toRecipients"][0]["emailAddress"],
                         {"name": "Mario Rossi", "address": "mario@example.com"})
        self.assertEqual((msg["message"]["body"]["contentType"], msg["message"]["attachments"][0]["name"]),
                         ("HTML", "doc.pdf"))

    def test_spento_se_non_configurato(self):
        from . import email_util
        self.assertFalse(email_util.invio_diretto_attivo())


class AggiornaAdessoTest(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("adm", password="x"))

    def test_pagina_e_riavvio(self):
        from unittest import mock

        from . import views_aggiorna
        with mock.patch.object(views_aggiorna, "stato_versioni",
                               return_value={"attuale": "abc1234", "novita": [["def5678", "21/11 10:00", "Novità"]]}):
            r = self.client.get("/aggiorna/")
        self.assertContains(r, "Aggiorna adesso")
        self.assertContains(r, "Novità")
        with mock.patch.dict("os.environ", {"ASTA_SUPERVISORE": ""}):
            r = self.client.post("/aggiorna/", follow=True)
        self.assertContains(r, "chiudi la finestra nera")
        with mock.patch.dict("os.environ", {"ASTA_SUPERVISORE": "1"}), \
                mock.patch.object(views_aggiorna.threading, "Thread") as t:
            r = self.client.post("/aggiorna/")
        self.assertContains(r, "Aggiornamento in corso")
        t.return_value.start.assert_called_once()

    def test_solo_amministratore(self):
        self.client.force_login(User.objects.create_user("clerk", password="x", is_staff=True))
        self.assertEqual(self.client.get("/aggiorna/").status_code, 302)


class MandatiMagazzinoTest(TestCase):
    def setUp(self):
        call_command("crea_ruoli", verbosity=0)
        self.client.force_login(User.objects.create_superuser("adm", password="x"))
        self.bruni = Soggetto.objects.create(nome_proprio="Mario", cognome="Bruni", is_mandante=True)
        self.asta = Asta.objects.create(numero=94, nome="Fumetti", data=date(2099, 1, 1))

    def test_nuovo_mandato_con_oggetti(self):
        r = self.client.post("/mandati/nuovo/", {
            "mandante": self.bruni.pk, "numero": "2099/001", "data": "2099-01-01", "commissione_pct": "15",
            "spesa_inserimento": "10", "stato_arrivo": "da_ritirare", "destinazione": "prossima_asta",
            "r_descrizione": ["Tavola originale", "", ""], "r_categoria": ["Tavola originale", "", ""],
            "r_stima_min": ["800", "", ""], "r_stima_max": ["1.200", "", ""], "r_riserva": ["", "", ""],
            "r_note": ["", "", ""],
            "incolla": "Manifesto Dr. Cyclops; Manifesto; 90; 200; 100; da fotografare\nTopolino n. 1\t\t50"})
        m = Mandato.objects.get(numero="2099/001")
        self.assertRedirects(r, f"/mandati/{m.pk}/")
        oggetti = list(m.oggetti.all())
        self.assertEqual([(o.codice, o.descrizione, o.stima_max) for o in oggetti],
                         [("2099/001/01", "Tavola originale", D("1200")),
                          ("2099/001/02", "Manifesto Dr. Cyclops", D("200")),
                          ("2099/001/03", "Topolino n. 1", None)])
        self.assertEqual((oggetti[1].riserva, oggetti[1].note, oggetti[2].stima_min), (D("100"), "da fotografare", D("50")))
        self.assertEqual((oggetti[0].get_destinazione_effettiva_display(), oggetti[0].stato), ("Prossima asta", "magazzino"))
        r = self.client.get("/mandati/?arrivo=da_ritirare")
        self.assertContains(r, "2099/001")

    def test_nuovo_mandato_da_excel(self):
        import io

        from django.core.files.uploadedfile import SimpleUploadedFile
        from openpyxl import Workbook
        wb = Workbook(); ws = wb.active
        ws.append(["Materiale consegnato"])
        ws.append(["Oggetto", "Stima", "Note"])
        ws.append(["Tex n. 1 originale", 500, "copertina staccata"])
        ws.append(["Zagor n. 1", "80", ""])
        buf = io.BytesIO(); wb.save(buf)
        self.client.post("/mandati/nuovo/", {
            "mandante": self.bruni.pk, "numero": "2099/050", "data": "2099-01-01", "commissione_pct": "15",
            "spesa_inserimento": "10", "file": SimpleUploadedFile("mandato.xlsx", buf.getvalue())})
        m = Mandato.objects.get(numero="2099/050")
        self.assertEqual([(o.descrizione, o.stima_min, o.note) for o in m.oggetti.all()],
                         [("Tex n. 1 originale", D("500"), "copertina staccata"), ("Zagor n. 1", D("80"), "")])

    def test_destinazione_consegne_e_asta(self):
        m = Mandato.objects.create(mandante=self.bruni, numero="2099/002", data=date.today(), commissione_pct=15,
                                   destinazione="asta_futura")
        a = Oggetto.objects.create(mandato=m, descrizione="Albo A")
        b = Oggetto.objects.create(mandato=m, descrizione="Albo B", stima_min=100, riserva=80)
        url = f"/mandati/{m.pk}/"
        self.client.post(url, {"azione": "applica", "sel": [a.pk], "b_destinazione": "consegne", "b_categoria": "Fumetto"})
        a.refresh_from_db()
        self.assertEqual((a.destinazione, a.collocazione, a.categoria), ("consegne", "Caveau", "Fumetto"))
        r = self.client.get("/consegne/")
        self.assertEqual([o.pk for o in r.context["da_fare"]], [a.pk])
        self.client.post("/consegne/", {"azione": "consegnato", "sel": [a.pk]})
        r = self.client.get("/consegne/")
        self.assertEqual(([o.pk for o in r.context["da_fare"]], [o.pk for o in r.context["fatti"]]), ([], [a.pk]))
        # assegnato all'asta diventa un lotto collegato all'oggetto
        self.client.post("/magazzino/", {"azione": "assegna", "sel": [b.pk], "asta": self.asta.pk})
        lotto = self.asta.lotti.get()
        self.assertEqual((lotto.numero, lotto.oggetto, lotto.riserva, lotto.mandato), ("1", b, D("80"), m))
        b = Oggetto.objects.get(pk=b.pk)
        self.assertEqual(b.stato, "assegnato")
        r = self.client.get("/magazzino/")  # di default solo i disponibili
        self.assertNotIn(b.pk, [o.pk for o in r.context["oggetti"]])
        self.asta.chiusa = True
        self.asta.save()
        self.assertEqual(Oggetto.objects.get(pk=b.pk).stato, "invenduto")

    def test_import_excel_mandati(self):
        import io

        from django.core.files.uploadedfile import SimpleUploadedFile
        from openpyxl import Workbook
        wb = Workbook(); ws = wb.active
        ws.append(["Elenco materiale ricevuto"])
        ws.append(["Mandante", "N° mandato", "Descrizione", "Categoria", "Stima min", "Riserva"])
        ws.append(["Bruni", "2099/010", "Tavola 1", "Tavola originale", 300, None])
        ws.append(["Bruni", "2099/010", "Tavola 2", "Tavola originale", "1.500,00", 1000])
        ws.append(["Nuovo Collezionista", "", "Manifesto", "Manifesto", 50, None])
        buf = io.BytesIO(); wb.save(buf)
        self.client.post("/mandati/importa/", {"file": SimpleUploadedFile("m.xlsx", buf.getvalue())})
        m = Mandato.objects.get(numero="2099/010")
        self.assertEqual((m.mandante, m.oggetti.count()), (self.bruni, 2))
        self.assertEqual(m.oggetti.get(descrizione="Tavola 2").stima_min, D("1500"))
        nuovo = next(x for x in Soggetto.objects.filter(is_mandante=True) if x.nome == "Nuovo Collezionista")
        self.assertEqual(nuovo.mandati.get().oggetti.get().descrizione, "Manifesto")


class CollegaOggettiTest(TestCase):
    def setUp(self):
        call_command("crea_ruoli", verbosity=0)
        self.client.force_login(User.objects.create_superuser("adm", password="x"))
        self.asta = Asta.objects.create(numero=91, nome="Locandine", data=date(2099, 1, 1))
        bruni = Soggetto.objects.create(nome_proprio="Mario", cognome="Bruni", is_mandante=True)
        alfa = Soggetto.objects.create(tipo="azienda", ragione_sociale="Libreria Alfa srl", is_mandante=True)
        self.m_b = Mandato.objects.create(mandante=bruni, numero="4017", data=date.today(), commissione_pct=15)
        self.m_a = Mandato.objects.create(mandante=alfa, numero="4009", data=date.today(), commissione_pct=10)
        self.cyclops = Oggetto.objects.create(mandato=self.m_a, descrizione="Dr. Cyclops, fotobusta USA")
        self.astronave = Oggetto.objects.create(mandato=self.m_b, descrizione="L'Astronave Atomica del Dr. Quatermass, manifesto")
        self.altro = Oggetto.objects.create(mandato=self.m_b, descrizione="Topolino libretto n. 1")

    def test_word_collega_lotti_e_oggetti(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        url = f"/asta/{self.asta.pk}/lotti/importa/"
        r = self.client.post(url, {"file": SimpleUploadedFile("Schede.docx", _catalogo_word())})
        righe = {x["numero"]: x for x in r.context["righe"]}
        self.assertEqual((righe["1"]["oggetto_id"], righe["2"]["oggetto_id"]), (self.cyclops.pk, self.astronave.pk))
        self.client.post(url, {"azione": "conferma"})
        lotti = {l.numero: l for l in self.asta.lotti.all()}
        self.assertEqual((lotti["1"].oggetto, lotti["2"].oggetto), (self.cyclops, self.astronave))
        self.assertEqual(Oggetto.objects.get(pk=self.cyclops.pk).stato, "assegnato")
        self.assertEqual(Oggetto.objects.get(pk=self.altro.pk).stato, "magazzino")

    def test_scelta_a_mano(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        url = f"/asta/{self.asta.pk}/lotti/importa/"
        r = self.client.post(url, {"file": SimpleUploadedFile("Schede.docx", _catalogo_word())})
        i = next(n for n, x in enumerate(r.context["righe"]) if x["numero"] == "2")
        self.client.post(url, {"azione": "conferma", f"oggetto_{i}": ""})  # «nessuno»
        self.assertIsNone(self.asta.lotti.get(numero="2").oggetto)


class AllegatiMandatoTest(TestCase):
    def setUp(self):
        call_command("crea_ruoli", verbosity=0)
        self.client.force_login(User.objects.create_superuser("adm", password="x"))
        s = Soggetto.objects.create(nome_proprio="Mario", cognome="Bruni", is_mandante=True)
        self.m = Mandato.objects.create(mandante=s, numero="2099/007", data=date.today(), commissione_pct=15)

    def test_foto_cifrata_e_visibile(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from .models import Allegato
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
        self.client.post(f"/mandati/{self.m.pk}/", {"azione": "allega",
                                                   "foto": SimpleUploadedFile("modulo.png", png, "image/png")})
        a = Allegato.objects.get()
        self.assertNotIn(b"PNG", (Allegato.cartella() / a.file).read_bytes())  # sul disco è cifrata
        r = self.client.get(f"/allegati/{a.pk}/")
        self.assertEqual((r.content, r["Content-Type"]), (png, "image/png"))
        r = self.client.get(f"/mandati/{self.m.pk}/")
        self.assertContains(r, 'id="visore"')
        self.client.post(f"/mandati/{self.m.pk}/", {"azione": "allega",
                                                   "foto": SimpleUploadedFile("x.exe", b"MZ", "application/x-msdownload")})
        self.assertEqual(Allegato.objects.count(), 1)
        self.client.post(f"/mandati/{self.m.pk}/", {"azione": "togli_allegato", "allegato": a.pk})
        self.assertFalse(Allegato.objects.exists())
        self.assertFalse((Allegato.cartella() / a.file).exists())


class OfferteTelefoniTest(TestCase):
    def setUp(self):
        call_command("crea_ruoli", verbosity=0)
        self.client.force_login(User.objects.create_superuser("adm", password="x"))
        self.asta = Asta.objects.create(numero=95, nome="Fumetti", data=date(2099, 1, 1))
        mand = Soggetto.objects.create(nome_proprio="Mario", cognome="Bruni", is_mandante=True)
        m = Mandato.objects.create(mandante=mand, numero="2099/001", data=date.today(), commissione_pct=15)
        for n, base in (("12", 100), ("13", 50), ("40", 200), ("41", 200), ("42", 200)):
            Lotto.objects.create(asta=self.asta, numero=n, mandato=m, titolo=f"Lotto {n}", base_asta=base)
        a = Soggetto.objects.create(nome_proprio="Anna", cognome="Verdi", is_cliente=True, telefono="333 0000000")
        b = Soggetto.objects.create(nome_proprio="Luca", cognome="Neri", is_cliente=True)
        self.p201 = Paletta.objects.create(asta=self.asta, numero=201, acquirente=a)
        self.p202 = Paletta.objects.create(asta=self.asta, numero=202, acquirente=b)
        c = Soggetto.objects.create(nome_proprio="Sara", cognome="Gialli", is_cliente=True)
        self.p301 = Paletta.objects.create(asta=self.asta, numero=301, acquirente=c)
        self.url = f"/asta/{self.asta.pk}/offerte/"

    def test_registro(self):
        from .models import OffertaScritta, Telefonata
        self.client.post(self.url, {"azione": "offerte", "paletta": "201", "testo": "12 150\n13: 40\n99 10\n40 1.500,00 €"})
        self.client.post(self.url, {"azione": "offerte", "paletta": "202", "testo": "12 150"})
        self.assertEqual(OffertaScritta.objects.count(), 4)
        self.assertEqual(OffertaScritta.objects.get(paletta=self.p201, lotto__numero="40").importo, D("1500"))
        self.client.post(self.url, {"azione": "offerte", "paletta": "201", "testo": "12 180"})  # correzione
        self.assertEqual(OffertaScritta.objects.get(paletta=self.p201, lotto__numero="12").importo, D("180"))
        self.client.post(self.url, {"azione": "telefoni", "paletta": "301", "lotti": "40-42, 13", "telefonista": "Giulia"})
        self.assertEqual(sorted(Telefonata.objects.values_list("lotto__numero", flat=True)), ["13", "40", "41", "42"])
        r = self.client.post(self.url, {"azione": "offerte", "paletta": "250", "testo": "12 100"}, follow=True)
        self.assertContains(r, "non è assegnata")
        r = self.client.get(self.url)
        riga = next(x for x in r.context["righe"] if x["lotto"].numero == "12")
        self.assertEqual([o.paletta.numero for o in riga["offerte"]], [201, 202])
        r = self.client.get(self.url + "?vista=telefonisti")
        self.assertContains(r, "Giulia")
        lotto = Lotto.objects.get(asta=self.asta, numero="13")
        self.assertEqual(lotto.registro_banditore(), ["201: 40", "TEL 301"])
        r = self.client.get(f"/asta/{self.asta.pk}/sala/")
        self.assertContains(r, "201: 180 · 202: 150")

    def test_faldone_colonne_off(self):
        import io

        from openpyxl import load_workbook
        self.client.post(self.url, {"azione": "offerte", "paletta": "201", "testo": "12 150"})
        self.client.post(self.url, {"azione": "telefoni", "paletta": "301", "lotti": "12"})
        r = self.client.get(f"/asta/{self.asta.pk}/faldone/")
        ws = load_workbook(io.BytesIO(r.content))["SERGIO"]
        riga = next(row for row in ws.iter_rows(values_only=True) if row[0] == 12)
        self.assertEqual(riga[5:8], ("201: 150", "TEL 301", None))


class IncollaDaExcelTest(TestCase):
    def test_incolla_con_titoli_ed_euro(self):
        call_command("crea_ruoli", verbosity=0)
        self.client.force_login(User.objects.create_superuser("adm", password="x"))
        s = Soggetto.objects.create(nome_proprio="Mario", cognome="Bruni", is_mandante=True)
        testo = ("Descrizione\tCategoria\tStima min\tStima max\tRiserva\tNote\n"
                 "A. Pazienza – Ritratti\t\t\t\t70 €\tq.tà 1\n"
                 "A. Pazienza – Il cavaliere; serigrafia\tGrafica e stampe\t\t\t1.500 €\tq.tà 1\n")
        self.client.post("/mandati/nuovo/", {"mandante": s.pk, "numero": "2099/060", "data": "2099-01-01",
                                             "commissione_pct": "15", "spesa_inserimento": "10", "incolla": testo})
        m = Mandato.objects.get(numero="2099/060")
        self.assertEqual([(o.descrizione, o.categoria, o.riserva, o.note) for o in m.oggetti.all()],
                         [("A. Pazienza – Ritratti", "", D("70"), "q.tà 1"),
                          ("A. Pazienza – Il cavaliere; serigrafia", "Grafica e stampe", D("1500"), "q.tà 1")])
