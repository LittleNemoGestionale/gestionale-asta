"""Backup completo in Excel, leggibile senza il gestionale.

Un file .xlsx con un foglio per tabella, con i dati in chiaro e i conteggi già
calcolati (diritti, commissioni, netti), così da poter lavorare anche se il
programma smettesse di funzionare. Il file è protetto da password (cifratura
nativa di Excel: si apre con Excel/Numbers/LibreOffice inserendo la password).
"""
import io
from datetime import datetime

from msoffcrypto.format.ooxml import OOXMLFile
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .models import (Aggiudicazione, Asta, Indirizzo, Lotto, Mandato, Paletta, Soggetto,
                     SpesaMandante)

EURO = '#,##0.00 "€"'
HEAD_FILL = PatternFill("solid", fgColor="7A1F2B")
HEAD_FONT = Font(bold=True, color="FFFFFF")


def _foglio(wb, titolo, intestazioni, righe, colonne_euro=()):
    ws = wb.create_sheet(titolo)
    ws.append(intestazioni)
    for c in ws[1]:
        c.fill, c.font = HEAD_FILL, HEAD_FONT
        c.alignment = Alignment(vertical="center")
    n = 0
    for r in righe:
        ws.append(list(r))
        n += 1
    for idx, h in enumerate(intestazioni, start=1):
        col = get_column_letter(idx)
        if h in colonne_euro:
            for cell in ws[col][1:]:
                cell.number_format = EURO
        larghezza = max([len(str(h))] + [len(str(c.value or "")) for c in ws[col][1:200]])
        ws.column_dimensions[col].width = min(max(10, larghezza + 2), 60)
    ws.freeze_panes = "A2"
    if n:
        ws.auto_filter.ref = ws.dimensions
    return n


def crea_workbook() -> Workbook:
    wb = Workbook()
    info = wb.active
    info.title = "Leggimi"
    conteggi = {}

    conteggi["Anagrafiche"] = _foglio(wb, "Anagrafiche",
        ["ID", "Codice", "Tipo", "Nome / Ragione sociale", "CF / P.IVA", "Email", "Telefono",
         "Documento", "IBAN", "Note"],
        ((s.pk, s.codice, s.tipo, s.nome, s.codice_fiscale, s.email, s.telefono, s.documento, s.iban, s.note)
         for s in Soggetto.objects.order_by("codice")))

    conteggi["Indirizzi"] = _foglio(wb, "Indirizzi",
        ["ID", "Codice cliente", "Etichetta", "Destinatario", "Via", "CAP", "Città", "Prov.", "Nazione",
         "Tel. consegna", "Note consegna"],
        ((i.pk, i.soggetto.codice, i.etichetta, i.destinatario, i.via, i.cap, i.citta, i.provincia,
          i.nazione, i.telefono_consegna, i.note_consegna)
         for i in Indirizzo.objects.select_related("soggetto").order_by("soggetto__codice")))

    conteggi["Mandati"] = _foglio(wb, "Mandati",
        ["Numero", "Data", "Codice mandante", "Mandante", "Commissione %", "IVA su commissione", "Accordo"],
        ((m.numero, m.data, m.mandante.codice, m.mandante.nome, float(m.commissione_pct),
          "sì" if m.iva_su_commissione else "no", m.note_accordo)
         for m in Mandato.objects.select_related("mandante").order_by("numero")))

    conteggi["Aste"] = _foglio(wb, "Aste",
        ["ID", "Nome", "Data", "Diritti %", "Diritti IVA inclusa", "IVA %", "Chiusa"],
        ((a.pk, a.nome, a.data, float(a.diritti_pct), "sì" if a.diritti_iva_inclusa else "no",
          float(a.iva_pct), "sì" if a.chiusa else "no") for a in Asta.objects.order_by("data")))

    conteggi["Palette"] = _foglio(wb, "Palette",
        ["Asta", "Paletta", "Codice cliente", "Cliente", "Spedire a", "Spese spedizione"],
        ((p.asta.nome, p.numero, p.acquirente.codice, p.acquirente.nome,
          " · ".join(p.indirizzo_spedizione.righe()) if p.indirizzo_spedizione else "ritiro in sede",
          p.spese_spedizione)
         for p in Paletta.objects.select_related("asta", "acquirente", "indirizzo_spedizione__soggetto")
         .order_by("asta__data", "numero")), colonne_euro={"Spese spedizione"})

    conteggi["Lotti"] = _foglio(wb, "Lotti",
        ["Asta", "Lotto", "Mandato", "Codice mandante", "Descrizione", "Stima min", "Stima max", "Base",
         "Riserva", "Commissione %", "Stato", "Prezzo contovendita", "Data reso"],
        ((l.asta.nome, l.numero, l.mandato.numero, l.mandato.mandante.codice, l.descrizione, l.stima_min,
          l.stima_max, l.base_asta, l.riserva, float(l.commissione_effettiva), l.get_stato_display(),
          l.prezzo_contovendita, l.data_reso)
         for l in Lotto.objects.select_related("asta", "mandato__mandante").order_by("asta__data", "ordine", "numero")),
        colonne_euro={"Stima min", "Stima max", "Base", "Riserva", "Prezzo contovendita"})

    conteggi["Aggiudicazioni"] = _foglio(wb, "Aggiudicazioni",
        ["Asta", "Lotto", "Descrizione", "Paletta", "Codice acquirente", "Acquirente", "Martello",
         "Diritti", "di cui / + IVA diritti", "Codice mandante", "Commissione %", "Commissione",
         "IVA commissione", "Netto mandante", "Registrata il", "Operatore"],
        ((a.lotto.asta.nome, a.lotto.numero, a.lotto.descrizione, a.paletta.numero,
          a.paletta.acquirente.codice, a.paletta.acquirente.nome, a.prezzo_martello, a.diritti,
          a.iva_diritti, a.lotto.mandato.mandante.codice, float(a.lotto.commissione_effettiva),
          a.commissione, a.iva_commissione, a.netto_mandante,
          a.registrata_il.replace(tzinfo=None), str(a.registrata_da or ""))
         for a in Aggiudicazione.objects.select_related(
             "lotto__asta", "lotto__mandato__mandante", "paletta__acquirente", "registrata_da")
         .order_by("lotto__asta__data", "lotto__ordine", "lotto__numero")),
        colonne_euro={"Martello", "Diritti", "di cui / + IVA diritti", "Commissione", "IVA commissione",
                      "Netto mandante"})

    conteggi["Spese mandanti"] = _foglio(wb, "Spese mandanti",
        ["Asta", "Mandato", "Codice mandante", "Lotto", "Descrizione", "Importo"],
        ((s.asta.nome, s.mandato.numero, s.mandato.mandante.codice, s.lotto.numero if s.lotto else "",
          s.descrizione, s.importo)
         for s in SpesaMandante.objects.select_related("asta", "mandato__mandante", "lotto")),
        colonne_euro={"Importo"})

    info["A1"] = "Backup gestionale asta"
    info["A1"].font = Font(bold=True, size=16)
    info["A2"] = f"Creato il {datetime.now():%d/%m/%Y alle %H:%M}"
    info["A4"] = "Contiene dati personali: non inoltrare, non salvare senza password."
    info["A4"].font = Font(bold=True, color="B00020")
    info["A6"] = "Foglio"; info["B6"] = "Righe"
    info["A6"].font = info["B6"].font = Font(bold=True)
    for i, (k, v) in enumerate(conteggi.items(), start=7):
        info[f"A{i}"] = k
        info[f"B{i}"] = v
    info.column_dimensions["A"].width = 60
    return wb


def esporta_bytes(password: str | None) -> bytes:
    buf = io.BytesIO()
    crea_workbook().save(buf)
    if not password:
        return buf.getvalue()
    buf.seek(0)
    out = io.BytesIO()
    f = OOXMLFile(buf)
    f.encrypt(password, out)
    return out.getvalue()
