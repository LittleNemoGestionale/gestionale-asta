from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

# ruolo -> permessi (codename del modello "asta")
RUOLI = {
    "Clerk di sala": ["view_lotto", "view_paletta", "add_aggiudicazione", "view_aggiudicazione", "change_lotto",
                      "delete_aggiudicazione", "view_asta"],
    "Accettazione": ["view_soggetto", "add_soggetto", "change_soggetto", "view_dati_personali",
                     "add_indirizzo", "change_indirizzo", "view_indirizzo",
                     "view_paletta", "add_paletta", "change_paletta", "view_asta", "view_lotto",
                     "view_interesse", "add_interesse", "view_mandato", "add_mandato", "view_aggiudicazione"],
    "Spedizioni": ["view_paletta", "change_paletta", "view_lotto", "view_aggiudicazione",
                   "view_soggetto", "view_indirizzo", "view_dati_personali"],
    "Contabilità": ["view_asta", "view_lotto", "view_paletta", "change_paletta", "view_aggiudicazione",
                    "view_mandato", "view_soggetto", "view_spesamandante", "add_spesamandante",
                    "view_metodopagamento", "view_incasso", "add_incasso", "change_incasso",
                    "view_pagamentomandante", "add_pagamentomandante", "change_pagamentomandante",
                    "view_modalitaconsegna"],
    "Amministrazione": "*",
}


# metodi di pagamento iniziali (le sigle usate nei fogli Excel); modificabili dall'archivio
METODI = [
    ("Contanti", "Contanti", True),
    ("BB B.S.", "Bonifico bancario – banca S.", False),
    ("BB B.R.", "Bonifico bancario – banca R.", False),
    ("BMT", "Bancomat", False),
    ("CCR", "Carta di credito", False),
    ("PayPal", "PayPal", False),
    ("Buono", "Buono / credito cliente", False),
]


# modalità di consegna iniziali (costi indicativi, modificabili dall'archivio)
MODALITA = [
    ("Ritiro in sede", False, 0, "", "I lotti potranno essere ritirati in sede, previo appuntamento, a pagamento avvenuto."),
    ("Spedizione Italia", True, 25, "it", "I lotti saranno spediti con corriere assicurato all'indirizzo indicato, a pagamento avvenuto."),
    ("Spedizione UE", True, 40, "ue", "I lotti saranno spediti con corriere assicurato all'indirizzo indicato, a pagamento avvenuto."),
    ("Spedizione fuori UE", True, 60, "extra", "I lotti saranno spediti con corriere assicurato dopo le pratiche di esportazione, a pagamento avvenuto."),
    ("Corriere dell'acquirente", False, 0, "", "I lotti saranno consegnati al corriere da Lei incaricato, a pagamento avvenuto."),
]

# categorie di interesse iniziali (si possono aggiungere dalla scheda)
INTERESSI = ["Fumetti", "Tavole originali", "Manifesti", "Illustrazione", "Grafica d'autore", "Libri e riviste",
             "Fotografia", "Giocattoli e modellismo"]


class Command(BaseCommand):
    help = "Crea/aggiorna i gruppi di utenti con i relativi permessi"

    def handle(self, *args, **opts):
        tutti = Permission.objects.filter(content_type__app_label="asta")
        for nome, perms in RUOLI.items():
            g, _ = Group.objects.get_or_create(name=nome)
            g.permissions.set(tutti if perms == "*" else tutti.filter(codename__in=perms))
            if opts["verbosity"]:
                self.stdout.write(f"✓ {nome}: {g.permissions.count()} permessi")
        from asta.models import Interesse, MetodoPagamento, ModalitaConsegna
        if not ModalitaConsegna.objects.exists():
            for i, (nome, sped, costo, zona, testo) in enumerate(MODALITA):
                ModalitaConsegna.objects.create(nome=nome, spedizione=sped, costo=costo, zona=zona,
                                                testo_email=testo, ordine=i)
        for nome in INTERESSI:
            Interesse.objects.get_or_create(nome=nome)
        for sigla, descr, cont in METODI:
            MetodoPagamento.objects.get_or_create(sigla=sigla, defaults={"descrizione": descr, "contanti": cont})
