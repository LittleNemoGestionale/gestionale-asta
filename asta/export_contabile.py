"""Excel per la contabile, con lo stesso formato dei fogli usati oggi.

- incassi_asta():   "ASTA 93 del 25/09/26" — una riga per paletta, cosa incassare e come è stato incassato
                    + foglio "IVA mensile" (diritti + spese incassati, IVA scorporata)
- pagamenti_asta(): "ASTA 93 il 25/09/2026" — una riga per mandato, verde = pagato

Nessun nome di persona: solo numeri di paletta e codici mandante (come nei fogli originali).
"""
import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .models import Asta, iva_mensile, situazione_mandanti

CONT = '_-* #,##0.00_-;\\-* #,##0.00_-;_-* "-"??_-;_-@_-'
EURO = '#,##0.00\\ "€"'
DATA = "dd/mm/yy;@"
VERDE_INCASSI = PatternFill("solid", fgColor="CCFFCC")
VERDE_HEAD = PatternFill("solid", fgColor="99FFCC")
GIALLO = PatternFill("solid", fgColor="FFF2B3")
VERDE_PAGATI = PatternFill("solid", fgColor="CCFF99")
AZZURRO = PatternFill("solid", fgColor="D9E1F2")
LILLA = PatternFill("solid", fgColor="DDEBF7")
B = Font(bold=True)
SOTTILE = Border(bottom=Side(style="thin", color="999999"))


def _titolo_asta(asta, sep="del", anno="%y"):
    num = f"ASTA  {asta.numero}" if asta.numero else asta.nome
    return f"{num}       {sep}  {asta.data.strftime('%d/%m/' + anno)}"


def _metodi(incassi):
    if len(incassi) == 1:
        return incassi[0].metodo.sigla
    return ", ".join(f"{i.metodo.sigla} {i.importo:,.2f}€".replace(",", "X").replace(".", ",").replace("X", ".")
                     for i in incassi)


def foglio_incassi(ws, asta: Asta):
    ws.title = f"Incassi asta {asta.numero or asta.pk}"[:31]
    ws["A1"] = _titolo_asta(asta)
    ws["A1"].font = Font(bold=True, size=12)
    ws["A1"].fill = VERDE_HEAD
    head = ["ACQUIRENTE", "Nr  Paletta", "TOTALE da Incassare €", "Tot  Aggiudicazione", "Tot \nDiritti",
            "Tot \nSpese", "Metodo   incasso", " Data   incasso", "Nr Scontrino  \nNr Fattura",
            "RITIRATO,  SPEDITO,   NOTE",
            # colonne in più rispetto al foglio originale
            "Abbuono", "Incassato €", "Ancora da incassare €", "Stato", "Diritti %"]
    ws.append(head)
    for c in ws[2]:
        c.fill, c.font = VERDE_HEAD, B
        c.alignment = Alignment(wrap_text=True, vertical="center")
    ws.row_dimensions[2].height = 32
    ws.append([])
    palette = list(asta.palette.filter(aggiudicazioni__isnull=False).distinct().order_by("numero"))
    r = 4
    for n, p in enumerate(palette, start=1):
        s = p.situazione_incasso()
        note = " – ".join(x for x in [p.get_stato_consegna_display() if p.stato_consegna != "da_definire" else "",
                                      p.note] if x)
        if s["avviso_contanti"]:
            note = "⚠ CONTANTI OLTRE IL LIMITE – " + note
        ws.append([
            n, p.numero, f"=D{r}+E{r}+F{r}-K{r}", s["martello"], s["diritti"], s["spese_spedizione"] or None,
            _metodi(s["incassi"]) if s["incassi"] else None,
            s["incassi"][-1].data if s["incassi"] else None,
            p.documento or None, note or None,
            s["abbuono"] or None, s["incassato"] or None, f"=C{r}-L{r}", s["stato"], float(p.diritti_effettivi),
        ])
        fill = {"incassato": VERDE_INCASSI, "parziale": GIALLO}.get(s["stato"])
        for c in ws[r]:
            if fill:
                c.fill = fill
            if c.column in (3, 4, 5, 6, 11, 12, 13):
                c.number_format = CONT
        ws.cell(r, 8).number_format = DATA
        r += 1
    last = r - 1
    r += 1
    ws.cell(r, 1, "Totali").font = B
    for col in (3, 4, 5, 6, 11, 12, 13):
        L = get_column_letter(col)
        c = ws.cell(r, col, f"=SUM({L}4:{L}{last})" if last >= 4 else 0)
        c.number_format, c.font = CONT, B
    for col, t in zip((3, 4, 5, 6), ("TOTALE da incassare ", "Tot Aggiudicazione", "Tot \nDiritti", "Tot \nSpese")):
        ws.cell(r + 1, col, t).alignment = Alignment(wrap_text=True)
    larghezze = [10.5, 9.2, 13, 13, 11, 9.2, 22, 10, 14, 34, 10, 12, 13, 12, 9]
    for i, w in enumerate(larghezze, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "C4"


def foglio_iva(ws, asta: Asta):
    ws.title = "IVA mensile"
    ws["A1"] = f"{_titolo_asta(asta)} – diritti e spese incassati, IVA {asta.iva_pct}% scorporata"
    ws["A1"].font = Font(bold=True, size=12)
    ws.append([])
    ws.append(["Mese (data saldo)", "Palette saldate", "Diritti", "Spese", "Totale (IVA incl.)", "Imponibile", "IVA"])
    for c in ws[3]:
        c.font, c.fill = B, LILLA
    r = 4
    for m in iva_mensile(asta):
        ws.append([m["mese"].strftime("%m/%Y"), m["palette"], m["diritti"], m["spese"], f"=C{r}+D{r}",
                   f"=ROUND(E{r}/(1+{asta.iva_pct}/100),2)", f"=E{r}-F{r}"])
        for col in range(3, 8):
            ws.cell(r, col).number_format = CONT
        r += 1
    for i, w in enumerate([18, 15, 13, 13, 18, 13, 13], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.cell(r + 1, 1, "Mese = data dell'ultimo incasso che salda la paletta. Le palette non ancora saldate "
                      "non sono conteggiate.").font = Font(italic=True, color="666666")


def foglio_pagamenti(ws, asta: Asta):
    ws.title = f"Mandanti asta {asta.numero or asta.pk}"[:31]
    ws["A3"] = "  " + _titolo_asta(asta, sep="il", anno="%Y")
    ws["B3"], ws["C3"], ws["D3"], ws["E3"] = "dobbiamo pagare", "= Pagati", " =ancora  Da pagare", " = Note"
    ws["A3"].fill = ws["F3"].fill = LILLA
    ws["A3"].font = B
    ws["B3"].fill = AZZURRO
    ws["C3"].fill = VERDE_PAGATI
    head = ["Mandante", "Importo €", "Pagato con", "Data", "Nr documento", "Note",
            "Mandato", "Lotti venduti", "Già pagabile €*", "IBAN in archivio"]
    for i, h in enumerate(head, start=1):
        c = ws.cell(4, i, h)
        c.font, c.border = Font(bold=True, size=9, color="555555"), SOTTILE
    r = 5
    for riga in situazione_mandanti(asta):
        m, pag = riga["mandato"], riga["pagamenti"]
        base = [riga["mandante"].codice, riga["dovuto"]]
        extra = [m.numero, riga["lotti_venduti"], riga["pagabile"] if riga["residuo"] > 0 else None,
                 "sì" if riga["mandante"].iban else "NO"]
        note = []
        if riga["stato"] == "acconto":
            note.append("Pagato acconto " + ", ".join(f"{p.importo:,.2f}€ il {p.data:%d/%m}".replace(",", "X")
                        .replace(".", ",").replace("X", ".") for p in pag))
        if riga["iban_mancante"]:
            note.append("chiedere IBAN")
        if pag:
            ultimo = pag[-1]
            metodi = ", ".join(sorted({p.metodo.sigla for p in pag}))
            ws.append(base + [metodi, ultimo.data, ", ".join(p.numero_documento for p in pag if p.numero_documento)
                              or None, " · ".join(note) or None] + extra)
        else:
            ws.append(base + [None, None, None, " · ".join(note) or None] + extra)
        verde = riga["stato"] == "pagato" or riga["dovuto"] <= 0 or riga["stato"] == "acconto"
        for col in range(1, 6):
            if verde:
                ws.cell(r, col).fill = VERDE_PAGATI
        if riga["stato"] == "acconto":
            ws.cell(r, 6).fill = VERDE_PAGATI
        ws.cell(r, 2).number_format = EURO
        ws.cell(r, 4).number_format = DATA
        ws.cell(r, 9).number_format = EURO
        r += 1
        if riga["stato"] == "acconto":  # riga del residuo, non colorata
            ws.append([None, riga["residuo"], None, None, None, " DA PAGARE"])
            ws.cell(r, 2).number_format = EURO
            r += 1
    ws.cell(r, 2, f"=SUMIF(A5:A{r - 1},\"<>\",B5:B{r - 1})" if r > 5 else 0).number_format = EURO
    ws.cell(r, 2).font = B
    ws.cell(r, 1, "Totale da pagare per l'asta").font = B
    ws.cell(r + 2, 1, "* Già pagabile = netto dei lotti il cui acquirente ha già saldato, meno quanto già pagato."
            ).font = Font(italic=True, color="666666")
    for i, w in enumerate([28.2, 13, 14, 11, 13, 40, 11, 8, 14, 10], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def excel_contabile(asta: Asta, quale: str) -> bytes:
    wb = Workbook()
    if quale == "incassi":
        foglio_incassi(wb.active, asta)
        foglio_iva(wb.create_sheet(), asta)
    else:
        foglio_pagamenti(wb.active, asta)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
