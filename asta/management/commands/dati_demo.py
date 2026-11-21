"""Carica un'asta di esempio con dati INVENTATI (stesso andamento dei fogli reali), per provare il gestionale."""
import random
from datetime import date, datetime, timedelta

from django.utils import timezone
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.core.management.base import BaseCommand

from datetime import time

from asta.models import (Aggiudicazione, Asta, Incasso, Indirizzo, Interesse, Lotto, Mandato, MetodoPagamento,
                         PagamentoMandante, Paletta, Soggetto, SpesaMandante, StatoConsegna)

NOMI = ["Luca Neri", "Anna Russo", "Marco Galli", "Sara Conti", "Paolo Fabbri", "Elena Riva", "Davide Sala",
        "Chiara Leone", "Fabio Greco", "Irene Costa", "Matteo Villa", "Laura Fontana", "Stefano Rizzo",
        "Giorgia Ferri", "Andrea Moretti", "Silvia Bruno", "Nicola Pellegrini", "Marta Caruso",
        "Roberto Gatti", "Valentina Serra", "Alessio Marini", "Federica Longo", "Simone Testa",
        "Martina Fiore", "Enrico Rinaldi", "Paola Barbieri"]
CITTA = [("00186", "Roma", "RM"), ("20121", "Milano", "MI"), ("40126", "Bologna", "BO"), ("50122", "Firenze", "FI"),
         ("10121", "Torino", "TO"), ("80132", "Napoli", "NA"), ("1050", "Bruxelles", ""), ("75003", "Paris", "")]
OGGETTI = ["Tavola originale a china", "Acquerello su carta", "Bozzetto a matita", "Serigrafia numerata",
           "Copertina originale", "Prima edizione, albo", "Studio preparatorio", "Tempera su cartoncino",
           "Striscia originale", "Illustrazione a pennarello", "Litografia firmata", "Disegno a matita blu"]
PREZZI = [50, 70, 100, 125, 150, 170, 175, 200, 230, 250, 275, 300, 375, 425, 450, 485, 500, 525, 550, 600,
          650, 675, 725, 750, 825, 950, 1000, 1090, 1100, 1150, 1200, 1325, 1420, 1600, 1800, 2000, 2205,
          2750, 2900, 3275, 4300, 6030]


class Command(BaseCommand):
    help = "Crea dati di prova inventati"

    def handle(self, *a, **o):
        rnd = random.Random(93)
        call_command("crea_ruoli", verbosity=0)
        utenti = [("admin", None, True), ("clerk", "Clerk di sala", False), ("contabile", "Contabilità", False),
                  ("accettazione", "Accettazione", False)]
        for nome, gruppo, su in utenti:
            u, _ = User.objects.get_or_create(username=nome, defaults={"is_staff": True, "is_superuser": su})
            u.set_password(nome); u.save()
            if gruppo:
                u.groups.add(Group.objects.get(name=gruppo))
        admin = User.objects.get(username="admin")
        M = {m.sigla: m for m in MetodoPagamento.objects.all()}

        # ---- mandanti e mandati
        interessi = list(Interesse.objects.all())
        COGNOMI = ["Bianchi", "Esposito", "Romano", "Colombo", "Ricci", "Marino", "Greco", "Lombardi", "Moretti",
                   "Barbieri", "Fontana", "Santoro"]
        NOMI_M = ["Giovanni", "Maria", "Franco", "Lucia", "Carlo", "Teresa", "Alberto", "Rita", "Sergio",
                  "Anna", "Bruno", "Grazia"]

        def domicilio(sog):
            cap, citta, prov = rnd.choice(CITTA)
            Indirizzo.objects.create(soggetto=sog, tipo="domicilio", via=f"Via {rnd.choice(COGNOMI)} {rnd.randint(1, 99)}",
                                     cap=cap, citta=citta, provincia=prov, nazione="Italia" if prov else
                                     ("Belgio" if citta == "Bruxelles" else "Francia"))

        mandati = []
        for i in range(1, 13):
            if i % 4 == 0:
                s = Soggetto.objects.create(tipo="azienda", ragione_sociale=f"Galleria {COGNOMI[i - 1]} s.r.l.",
                                            is_mandante=True, codice_fiscale=f"IT0{i:02d}45678901")
            else:
                s = Soggetto.objects.create(nome_proprio=NOMI_M[i - 1], cognome=COGNOMI[i - 1], is_mandante=True,
                                            telefono=f"+39 34{i} 555 01{i:02d}", email=f"mandante{i}@example.com")
            if i not in (7, 11):
                s.iban = f"IT60X05428111010000001234{i:02d}"
                s.save()
            domicilio(s)
            mandati.append(Mandato.objects.create(mandante=s, numero=f"2026/{100 + i}", data=date(2026, 8, i + 1),
                                                  commissione_pct=D(rnd.choice(["10", "12", "15", "15", "20"]))))
        # un mandante con due mandati (come il n. 15 del foglio)
        mandati.append(Mandato.objects.create(mandante=mandati[2].mandante, numero="2026/150",
                                              data=date(2026, 9, 1), commissione_pct=D("8"),
                                              note_accordo="Accordo speciale per la collezione completa"))

        asta = Asta.objects.create(numero=93, nome="Fumetto e illustrazione", data=date(2026, 9, 25), data_fine=date(2026, 9, 26),
                                   numero_tornate=2, luogo="Sede", giorni_post_asta=60)
        asta.crea_fasce()
        D_ASTA = asta.data

        # ---- acquirenti e palette: 2xx-8xx in sala (18%), 9xx online (23%)
        numeri = [201, 203, 205, 212, 216, 300, 302, 305, 309, 403, 447, 455, 463, 471, 12, 104, 507, 612,
                  901, 904, 905, 906, 909, 912, 917, 924]
        palette = []
        for n, num in enumerate(numeri):
            nome_p, cogn = NOMI[n].split(" ", 1)
            c = Soggetto.objects.create(nome_proprio=nome_p, cognome=cogn, is_cliente=True,
                                        email=f"cliente{n}@example.com", telefono=f"+39 33{n % 10} 555 {1000 + n}")
            c.interessi.set(rnd.sample(interessi, rnd.choice([1, 1, 2, 3])))
            domicilio(c)
            if n % 4 == 1:  # spedizione a un indirizzo diverso dal domicilio
                cap, citta, prov = rnd.choice(CITTA)
                Indirizzo.objects.create(soggetto=c, tipo="spedizione", destinatario=f"{NOMI[n]} c/o studio",
                                         via=f"Corso {rnd.choice(COGNOMI)} {rnd.randint(1, 120)}", cap=cap, citta=citta,
                                         provincia=prov, nazione="Italia" if prov else "Belgio",
                                         note_consegna="Consegna solo in orario d'ufficio")
            ind = c.indirizzo_spedizione
            spedisce = rnd.random() < .7
            palette.append(Paletta.objects.create(
                asta=asta, numero=num, acquirente=c, indirizzo_spedizione=ind if spedisce else None,
                spese_spedizione=D(rnd.choice([25, 25, 25, 35, 40, 60])) if spedisce else D(0)))  # 9xx al 23% per regola dell'asta

        # ---- lotti e aggiudicazioni
        k = 1
        for p in palette:
            for _ in range(rnd.choice([1, 1, 1, 2, 3])):
                l = Lotto.objects.create(asta=asta, numero=str(k), tornata=1 if k <= 22 else 2,
                                         mandato=rnd.choice(mandati),
                                         descrizione=f"{rnd.choice(OGGETTI)}, {rnd.randint(1960, 1999)}",
                                         base_asta=D(50))
                Aggiudicazione.objects.create(lotto=l, paletta=p, prezzo_martello=D(rnd.choice(PREZZI)),
                                              registrata_da=admin)
                k += 1
        for _ in range(6):  # invenduti
            Lotto.objects.create(asta=asta, numero=str(k), mandato=rnd.choice(mandati),
                                 descrizione=f"{rnd.choice(OGGETTI)}, {rnd.randint(1960, 1999)}",
                                 riserva=D(rnd.choice([200, 300, 500])),
                                 stato=rnd.choice([Lotto.Stato.INVENDUTO, Lotto.Stato.CONTOVENDITA,
                                                   Lotto.Stato.DA_RENDERE]))
            k += 1

        # ---- incassi (situazioni tipiche dei fogli reali)
        sc = 1
        for i, p in enumerate(palette):
            tot = p.conto()["totale"]
            giorno = D_ASTA + timedelta(days=rnd.choice([0, 3, 4, 5, 6, 7, 12, 26, 41]))
            if i == 9:  # pagamento diviso su più metodi
                bmt, cont = (tot * D("0.4")).quantize(D(1)), (tot * D("0.3")).quantize(D(1))
                Incasso.objects.create(paletta=p, data=D_ASTA, metodo=M["BMT"], importo=bmt)
                Incasso.objects.create(paletta=p, data=D_ASTA, metodo=M["Contanti"], importo=cont)
                Incasso.objects.create(paletta=p, data=D_ASTA, metodo=M["CCR"], importo=tot - bmt - cont)
            elif i == 4:  # abbuono di 50 centesimi
                p.abbuono = D("0.50"); p.save()
                Incasso.objects.create(paletta=p, data=giorno, metodo=M["BB B.R."], importo=tot - D("0.50"))
            elif i == 11:  # buono + saldo
                Incasso.objects.create(paletta=p, data=D_ASTA, metodo=M["Buono"], importo=D(95))
                Incasso.objects.create(paletta=p, data=D_ASTA, metodo=M["Contanti"], importo=tot - 95)
            elif i == 20:  # PayPal con commissioni
                Incasso.objects.create(paletta=p, data=giorno, metodo=M["PayPal"], importo=tot,
                                       commissioni=D("8.50"))
            elif i in (6, 18):  # acconto, resta da incassare
                Incasso.objects.create(paletta=p, data=giorno, metodo=M["BB B.S."], importo=(tot / 2).quantize(D(1)))
            elif i in (15, 23):  # non ancora pagato
                continue
            else:
                Incasso.objects.create(paletta=p, data=giorno,
                                       metodo=M[rnd.choice(["BB B.S.", "BB B.S.", "BB B.R.", "Contanti", "BMT"])],
                                       importo=tot)
            fatt = p.numero >= 900 and rnd.random() < .4
            p.tipo_documento = "fatt" if fatt else "sc"
            p.numero_documento = f"{105 + i}/E" if fatt else f"2554-{sc:04d}"
            sc += 0 if fatt else 1
            if p.indirizzo_spedizione:
                p.stato_consegna = rnd.choice([StatoConsegna.SPEDITO, StatoConsegna.SPEDITO,
                                               StatoConsegna.DA_SPEDIRE, StatoConsegna.ESPORTAZIONE])
            else:
                p.stato_consegna = rnd.choice([StatoConsegna.RITIRATO, StatoConsegna.RITIRA])
                if p.stato_consegna == StatoConsegna.RITIRA:
                    p.note = "Ritira in fiera a Bologna, chiamarlo"
            p.save()

        # ---- spese e pagamenti ai mandanti
        SpesaMandante.objects.create(asta=asta, mandato=mandati[0], descrizione="Spese piccolo restauro", importo=D(20))
        SpesaMandante.objects.create(asta=asta, mandato=mandati[4], descrizione="Spese di cornice", importo=D(35))
        from asta.models import situazione_mandanti
        for j, r in enumerate(situazione_mandanti(asta)):
            if r["dovuto"] <= 0 or r["iban_mancante"]:
                continue
            if j % 3 == 0:  # pagato tutto
                PagamentoMandante.objects.create(asta=asta, mandato=r["mandato"], data=date(2026, 11, 3),
                                                 metodo=M["BB B.R."], importo=r["dovuto"],
                                                 numero_documento=f"2587-{j + 1:04d}")
            elif j % 3 == 1 and r["dovuto"] > 1000:  # acconto
                PagamentoMandante.objects.create(asta=asta, mandato=r["mandato"], data=date(2026, 11, 9),
                                                 metodo=M["BB B.S."], importo=(r["dovuto"] / 2).quantize(D(1)),
                                                 numero_documento=f"2593-{j + 1:04d}")
        # ---- asta passata (conclusa) e asta futura
        passata = Asta.objects.create(numero=92, nome="Illustrazione e grafica", data=date(2026, 6, 13), chiusa=True)
        cl = Soggetto.objects.get(pk=palette[0].acquirente_id)  # un cliente abituale
        pp = Paletta.objects.create(asta=passata, numero=210, acquirente=cl)
        for n in range(1, 9):
            l = Lotto.objects.create(asta=passata, numero=str(n), mandato=rnd.choice(mandati),
                                     descrizione=f"{rnd.choice(OGGETTI)}, {rnd.randint(1960, 1999)}",
                                     base_asta=D(rnd.choice([100, 150, 200])), riserva=D(rnd.choice([150, 250])))
            if n <= 5:
                Aggiudicazione.objects.create(lotto=l, paletta=pp, prezzo_martello=D(rnd.choice(PREZZI)))
                Incasso.objects.create(paletta=pp, data=date(2026, 6, 20), metodo=M["BB B.S."], importo=D(1))
            else:
                l.stato = Lotto.Stato.INVENDUTO if n < 8 else Lotto.Stato.CONTOVENDITA
                l.save()
        Incasso.objects.filter(paletta=pp).delete()
        Incasso.objects.create(paletta=pp, data=date(2026, 6, 20), metodo=M["BB B.S."], importo=pp.conto()["totale"])

        futura = Asta.objects.create(numero=94, nome="Fumetto d'autore e manifesti", data=date(2026, 12, 4), data_fine=date(2026, 12, 5),
                                     numero_tornate=2, luogo="Sede")
        for n in range(1, 13):
            Lotto.objects.create(asta=futura, numero=str(n), tornata=1 if n <= 7 else 2, mandato=rnd.choice(mandati),
                                 descrizione=f"{rnd.choice(OGGETTI)}, {rnd.randint(1960, 1999)}",
                                 stima_min=D(rnd.choice([200, 300, 500])), stima_max=D(rnd.choice([600, 800, 1200])),
                                 base_asta=D(rnd.choice([150, 200, 300])))
        for k, l in enumerate(Lotto.objects.filter(asta=passata, stato=Lotto.Stato.INVENDUTO)[:1], start=13):
            Lotto.objects.create(asta=futura, numero=str(k), tornata=2, mandato=l.mandato, descrizione=l.descrizione,
                                 riserva=l.riserva, base_asta=D(80), lotto_origine=l)
            l.stato = Lotto.Stato.RIPROPOSTO
            l.save()

        # ---- titoli, illustratori, sezioni e orari delle tornate (come nel catalogo Word / faldone)
        from asta.models import Tornata
        ILLUSTRATORI = ["HUGO PRATT", "DINO BATTAGLIA", "ANNA BRANDOLI", "GIOVE TOPPI", "SERGIO TOPPI", "MILO MANARA",
                        "GUIDO CREPAX", ""]
        SEZIONI = {1: "TAVOLE ORIGINALI", 2: "MANIFESTI E LOCANDINE"}
        for l in Lotto.objects.all():
            l.titolo = l.descrizione.split(",")[0]
            l.illustratore = rnd.choice(ILLUSTRATORI)
            l.descrizione = (f"{l.descrizione}. China su cartoncino, cm {rnd.randint(25, 50)}x{rnd.randint(30, 70)}. "
                             f"Firmata. Ottimo stato di conservazione, lievi segni del tempo ai margini.")
            l.descrizione_breve = rnd.choice(["", "Firmata", "Lotto di 3", f"cm {rnd.randint(25, 50)}x{rnd.randint(30, 70)}"])
            l.sezione = SEZIONI.get(l.tornata or 1, "")
            l.da_fotografare = l.asta_id == futura.pk and rnd.random() < .3
            l.note_interne = "DA FOTOGRAFARE" if l.da_fotografare else ""
            l.save()
        for a, orari in [(asta, [(date(2026, 9, 25), time(15, 0)), (date(2026, 9, 26), time(10, 0))]),
                         (passata, [(date(2026, 6, 13), time(16, 0))]),
                         (futura, [(date(2026, 12, 4), time(15, 0)), (date(2026, 12, 5), time(15, 0))])]:
            for n, (d, o) in enumerate(orari, start=1):
                Tornata.objects.create(asta=a, numero=n, data=d, ora=o)

        passata.crea_fasce()
        futura.crea_fasce()
        # ---- post asta dell'asta 93: tutti i non venduti sono ancora «invenduti»; due venduti dopo l'asta
        Lotto.objects.filter(asta=asta).exclude(stato=Lotto.Stato.AGGIUDICATO).update(stato=Lotto.Stato.INVENDUTO,
                                                                                         prezzo_contovendita=None)
        from asta.models import FasciaPalette  # noqa: F401
        inv = list(Lotto.objects.filter(asta=asta, stato=Lotto.Stato.INVENDUTO).order_by("ordine")[:2])
        nuovo = Soggetto.objects.create(nome_proprio="Ugo", cognome="Ferretti", is_cliente=True)
        domicilio(nuovo)
        p_post = Paletta.objects.create(asta=asta, numero=800, acquirente=nuovo)
        for l, pal, prezzo in zip(inv, [p_post, palette[2]], [D(320), D(180)]):
            a_ = Aggiudicazione.objects.create(lotto=l, paletta=pal, prezzo_martello=prezzo, post_asta=True,
                                               registrata_da=admin)
            Aggiudicazione.objects.filter(pk=a_.pk).update(registrata_il=timezone.make_aware(datetime(2026, 10, 9, 11, 0)))
        # ---- consegna decisa per una parte degli acquirenti dell'asta 93 (le altre ancora da decidere)
        from asta.models import ModalitaConsegna
        from asta.views_acquirenti import zona
        mod = {m.zona or m.nome: m for m in ModalitaConsegna.objects.all()}
        for i, p in enumerate(asta.palette.order_by("numero")):
            if i % 5 == 3:
                p.modalita, p.indirizzo_spedizione, p.spese_spedizione = None, None, 0
                p.stato_consegna = StatoConsegna.DA_DEFINIRE
            elif p.indirizzo_spedizione:
                p.modalita = mod.get(zona(p.indirizzo_spedizione.nazione))
                p.spese_spedizione = p.modalita.costo
            else:
                p.modalita = mod["Ritiro in sede"]
            if p.modalita and i % 3 == 0:
                p.email_preparata_il = timezone.make_aware(datetime(2026, 9, 28, 10, 30))
            p.save()
        # ---- asta 94 «a metà»: palette assegnate e primi lotti già battuti (per provare l'aggiudicazione)
        clienti94 = list(Soggetto.objects.filter(is_cliente=True).order_by("pk")[:10])
        for i, c in enumerate(clienti94):
            Paletta.objects.create(asta=futura, numero=101 + i if i < 8 else 901 + i - 8, acquirente=c)
        l94 = list(Lotto.objects.filter(asta=futura).order_by("ordine"))
        for l, (pal, prezzo) in zip(l94[:4], [(103, 350), (901, 600), (101, 220), (105, 900)]):
            Aggiudicazione.objects.create(lotto=l, paletta=Paletta.objects.get(asta=futura, numero=pal),
                                          prezzo_martello=D(prezzo), registrata_da=admin)
        l94[4].stato = Lotto.Stato.INVENDUTO
        l94[4].save()

        # ---- asta 95: appena aperta, ancora vuota (per provare l'inserimento dei dati da zero)
        nuova = Asta.objects.create(numero=95, nome="Grafica e manifesti d'epoca", data=date(2027, 1, 23), data_fine=date(2027, 1, 24),
                                    numero_tornate=2, luogo="Sede")
        nuova.crea_fasce(futura)
        Tornata.objects.create(asta=nuova, numero=1, data=date(2027, 1, 23), ora=time(15, 0))
        Tornata.objects.create(asta=nuova, numero=2, data=date(2027, 1, 24), ora=time(10, 30))

        self.stdout.write(self.style.SUCCESS(
            f"Asta demo creata (id {asta.pk}). Utenti: admin/admin, clerk/clerk, contabile/contabile"))
