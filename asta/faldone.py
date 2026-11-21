"""Faldone dell'asta: lo stesso Excel che produceva il vecchio gestionale.

Fogli:
  Foglio1          tutti i dati dei lotti (per chi gestisce l'asta)
  DAPHNE           base, riserva, stima e mandante per ogni lotto
  SERGIO           foglio del battitore: breve, con le colonne per le offerte e le righe di cambio tornata
  SECONDA TORNATA… lo stesso foglio diviso per tornata
  FAX              per chi registra le aggiudicazioni durante l'asta
  ASSICURAZIONE    lotto, titolo e valore assicurato (riserva o base)
"""
import io
import re

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .models import Lotto, Tornata

EURO = '#,##0.00'
B = Font(bold=True)
GRIGIO = PatternFill("solid", fgColor="E7E6E6")
GIALLO = PatternFill("solid", fgColor="FFF2CC")
LINEA = Side(style="thin", color="999999")
BORDO = Border(left=LINEA, right=LINEA, top=LINEA, bottom=LINEA)
A_CAPO = Alignment(wrap_text=True, vertical="top")
NOME_FOGLIO_GESTIONE = "DAPHNE "   # come nel faldone originale (con lo spazio finale)
NOME_FOGLIO_BATTITORE = "SERGIO"


def _numero_e_bis(numero: str):
    m = re.match(r"(\d+)\s*(.*)", numero or "")
    return (int(m.group(1)), m.group(2).strip()) if m else (numero, "")


def _prezzo(l):
    return l.riserva if l.riserva is not None else l.base_asta


def _larghezze(ws, larghezze):
    for i, w in enumerate(larghezze, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _intestazione(ws, titoli, fill=None):
    ws.append(titoli)
    for c in ws[ws.max_row]:
        c.font = B
        if fill:
            c.fill = fill
    ws.freeze_panes = "A2"


def _orari(asta):
    return {t.numero: t for t in Tornata.objects.filter(asta=asta)}


def _riga_cambio_tornata(ws, n_col, tornata_nuova: int, orari):
    t = orari.get(tornata_nuova)
    ordinali = Tornata.ORDINALI
    prima = ordinali[tornata_nuova - 2] if tornata_nuova - 2 < len(ordinali) else str(tornata_nuova - 1)
    dopo = ordinali[tornata_nuova - 1] if tornata_nuova - 1 < len(ordinali) else str(tornata_nuova)
    testo = f"FINE {prima} TORNATA - INIZIO {dopo} TORNATA"
    if t and t.ora:
        testo += f" ORE {t.ora:%H:%M}"
    if t and t.data:
        testo += f" ({t.data:%d/%m})"
    ws.append([testo])
    r = ws.max_row
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=n_col)
    c = ws.cell(r, 1)
    c.font, c.fill, c.alignment = Font(bold=True, size=14), GIALLO, Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[r].height = 30


def foglio_battitore(ws, lotti, orari, con_cambi=True):
    titoli = ["Lotto", "TITOLO", "DESCRIZIONE", "BASE", "RISERVA"] + ["OFF"] * 6 + ["PALETTA", "AGG."]
    _intestazione(ws, titoli, GRIGIO)
    for c in ws[1]:
        c.font, c.border, c.alignment = Font(bold=True, size=14), BORDO, Alignment(horizontal="center")
    tornata = None
    for l in lotti:
        if con_cambi and tornata is not None and (l.tornata or 1) != tornata:
            _riga_cambio_tornata(ws, len(titoli), l.tornata or 1, orari)
        tornata = l.tornata or 1
        n, bis = _numero_e_bis(l.numero)
        ws.append([f"{n}{bis}" if bis else n, l.titolo or l.descrizione[:80], l.descrizione_breve or l.illustratore or None,
                   l.base_asta, l.riserva if l.riserva not in (None, l.base_asta) else None] + [None] * 8)
        r = ws.max_row
        for c in ws[r]:
            c.font, c.border, c.alignment = Font(size=14, bold=c.column == 1), BORDO, A_CAPO
        for col in (4, 5):
            ws.cell(r, col).number_format = "#,##0"
        ws.row_dimensions[r].height = 42
    _larghezze(ws, [9, 42, 34, 12, 12] + [10] * 6 + [12, 12])
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToHeight = 0
    ws.print_title_rows = "1:1"


def crea_faldone(asta) -> bytes:
    lotti = list(Lotto.objects.filter(asta=asta).exclude(stato=Lotto.Stato.RIPROPOSTO)
                 .select_related("mandato__mandante", "aggiudicazione__paletta").order_by("ordine", "numero"))
    orari = _orari(asta)
    wb = Workbook()

    # ---- Foglio1: tutti i dati
    ws = wb.active
    ws.title = "Foglio1"
    _intestazione(ws, ["Tornate", "Lotto", "Bis", "Note", "Titolo", "Illustratore", "Autore", "D.tecnica",
                       "D.Contenutistica", "Testo in Inglese", "Base Catalogo", "Prezzo", "Valore C.A.", "Mandanti",
                       "Codice PS", "Mand. N.", "Riga"])
    for i, l in enumerate(lotti, start=1):
        n, bis = _numero_e_bis(l.numero)
        mand = l.mandato.numero
        ws.append([l.tornata or 1, n, bis or None, l.note_interne or None, l.titolo or None, l.illustratore or None,
                   None, None, l.descrizione or None, None, l.base_asta, _prezzo(l), l.stima_max,
                   (l.mandato.mandante.nome or "").upper(), None, int(mand) if str(mand).isdigit() else mand, i])
        for col in (11, 12, 13):
            ws.cell(ws.max_row, col).number_format = EURO
    _larghezze(ws, [10, 8, 6, 20, 32, 20, 10, 10, 60, 15, 12, 12, 12, 30, 10, 9, 6])

    # ---- DAPHNE: gestione dell'asta
    ws = wb.create_sheet(NOME_FOGLIO_GESTIONE)
    _intestazione(ws, ["Lotto", "Titolo", "Illustratore", "D.Contenutistica", "BASE", "RISERVA", "STIMA", "Mandanti"])
    for l in lotti:
        ws.append([l.numero if not l.numero.isdigit() else int(l.numero), l.titolo or None, l.illustratore or None,
                   l.descrizione or None, l.base_asta, _prezzo(l), l.stima_max, (l.mandato.mandante.nome or "").upper()])
        for col in (5, 6, 7):
            ws.cell(ws.max_row, col).number_format = EURO
        ws.cell(ws.max_row, 4).alignment = A_CAPO
    _larghezze(ws, [7, 50, 30, 67, 12, 12, 12, 33])

    # ---- SERGIO: battitore (tutta l'asta, con i cambi di tornata)
    foglio_battitore(wb.create_sheet(NOME_FOGLIO_BATTITORE), lotti, orari)

    # ---- un foglio per ogni tornata successiva alla prima
    numeri_tornate = sorted({l.tornata or 1 for l in lotti})
    for t in numeri_tornate:
        if t == 1:
            continue
        nome = f"{Tornata.ORDINALI[t - 1] if t <= len(Tornata.ORDINALI) else t} TORNATA"
        foglio_battitore(wb.create_sheet(nome[:31]), [l for l in lotti if (l.tornata or 1) == t], orari, con_cambi=False)

    # ---- FAX: registrazione delle aggiudicazioni (compilato se l'asta è già battuta)
    ws = wb.create_sheet("FAX")
    _intestazione(ws, ["Lotto", "PALETTA", "AGG.", "BASE"], GRIGIO)
    for l in lotti:
        agg = getattr(l, "aggiudicazione", None)
        ws.append([l.numero if not l.numero.isdigit() else int(l.numero), agg.paletta.numero if agg else None,
                   agg.prezzo_martello if agg else None, l.base_asta])
        for c in ws[ws.max_row]:
            c.border = BORDO
        ws.row_dimensions[ws.max_row].height = 22
    _larghezze(ws, [10, 12, 17, 16])

    # ---- ASSICURAZIONE
    ws = wb.create_sheet("ASSICURAZIONE")
    _intestazione(ws, ["LOTTO", "TITOLO", "RISERVA"])
    for l in lotti:
        ws.append([l.numero if not l.numero.isdigit() else int(l.numero), l.titolo or l.descrizione[:80], _prezzo(l)])
        ws.cell(ws.max_row, 3).number_format = EURO
    r = ws.max_row
    ws.append(["", "TOTALE VALORE ASSICURATO", f"=SUM(C2:C{r})" if r > 1 else 0])
    ws.cell(r + 1, 2).font = ws.cell(r + 1, 3).font = B
    ws.cell(r + 1, 3).number_format = EURO
    _larghezze(ws, [10, 50, 15])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
