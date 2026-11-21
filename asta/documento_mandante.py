"""PDF del rendiconto al mandante (come il vecchio report GestAsteAggMand).

Destinatario, riferimento e data; lotti venduti per mandato; nelle note le spese di inserimento a catalogo
(n. lotti × 10 €) e le altre spese (restauri…); a destra totale aggiudicazioni, diritti d'asta, rimborso spese
e il totale che il mandante riceve.
"""
import io
import os
from decimal import Decimal

from django.conf import settings
from reportlab.lib.colors import black, HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from .documento_aggiudicazioni import _e, _taglia
from .models import RendicontoMandante, rendiconto_mandante

LARG, ALT = A4
M = 14 * mm
GRIGIO = HexColor("#555555")


def _box(c, x, y, w, h, titolo=None):
    c.rect(x, y, w, h)
    if titolo:
        c.setFont("Helvetica-BoldOblique", 8.5)
        c.drawString(x + 1.5 * mm, y + h - 3.8 * mm, titolo)


def _testata(c, asta, mandante, rif):
    y = ALT - M
    logo = settings.CASA_ASTA_LOGO
    if logo and os.path.exists(logo):
        c.drawImage(logo, M, y - 22 * mm, width=65 * mm, height=22 * mm, preserveAspectRatio=True, anchor="nw",
                    mask="auto")
        yi = y - 25 * mm
    else:
        c.setFont("Helvetica-Bold", 16)
        c.drawString(M, y - 6 * mm, settings.CASA_ASTA_NOME)
        yi = y - 10 * mm
    c.setFont("Helvetica", 7)
    c.setFillColor(GRIGIO)
    for riga in [r.strip() for r in settings.CASA_ASTA_INTESTAZIONE.split("|") if r.strip()]:
        c.drawString(M, yi, riga)
        yi -= 3.4 * mm
    c.setFillColor(black)
    # destinatario
    bx, bw, bh, by = LARG - M - 80 * mm, 80 * mm, 34 * mm, ALT - M - 72 * mm
    _box(c, bx, by, bw, bh, "Spett.le")
    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(bx + 4 * mm, by + bh - 11 * mm, _taglia(c, (mandante.nome or "").upper(), "Helvetica-Bold", 10.5, bw - 8 * mm))
    ind = mandante.domicilio
    c.setFont("Helvetica", 9.5)
    if ind:
        c.drawString(bx + 4 * mm, by + bh - 18 * mm, _taglia(c, ind.via.upper(), "Helvetica", 9.5, bw - 8 * mm))
        c.drawString(bx + 4 * mm, by + bh - 25 * mm, f"{ind.cap}   {ind.citta.upper()}")
        c.drawRightString(bx + bw - 4 * mm, by + bh - 25 * mm, ind.provincia or (ind.nazione if ind.nazione != "Italia" else ""))
    # condizioni, riferimento, data
    y2 = by - 18 * mm
    _box(c, M, y2, 112 * mm, 12 * mm, "Condizioni di Pagamento")
    c.setFont("Helvetica", 8.5)
    if mandante.iban:
        c.drawString(M + 3 * mm, y2 + 2.5 * mm, f"Bonifico su IBAN {mandante.iban}")
    _box(c, M + 118 * mm, y2, 30 * mm, 12 * mm, "Riferimento")
    c.setFont("Helvetica", 10)
    c.drawCentredString(M + 133 * mm, y2 + 2.5 * mm, f"{rif.numero} / {rif.anno}")
    _box(c, M + 152 * mm, y2, LARG - 2 * M - 152 * mm, 12 * mm, "Data")
    c.drawCentredString(M + 152 * mm + (LARG - 2 * M - 152 * mm) / 2, y2 + 2.5 * mm, f"{rif.data:%d/%m/%Y}")
    y3 = y2 - 9 * mm
    c.setFont("Helvetica-BoldOblique", 10)
    c.drawCentredString(M + 55 * mm, y3, "Descrizione Lotto")
    c.drawRightString(LARG - M, y3, "Prezzo di Aggiudicazione")
    c.line(M, y3 - 1.5 * mm, M + 130 * mm, y3 - 1.5 * mm)
    c.line(M + 138 * mm, y3 - 1.5 * mm, LARG - M, y3 - 1.5 * mm)
    return y3 - 7 * mm


def _pagina_mandante(c, asta, mandante):
    r = rendiconto_mandante(asta, mandante)
    rif = RendicontoMandante.per(asta, mandante)
    y = _testata(c, asta, mandante, rif)
    limite = M + 72 * mm  # sotto ci sono note e totali
    for blocco in r["mandati"]:
        if y < limite + 10 * mm:
            c.showPage()
            y = _testata(c, asta, mandante, rif)
        c.setFont("Helvetica", 10)
        c.drawString(M, y, f"Aggiudicazione Asta N.   {asta.numero}   {settings.CASA_ASTA_NOME} - Mandato N.  {blocco['mandato'].numero}")
        y -= 6 * mm
        for a in blocco["venduti"]:
            if y < limite:
                c.showPage()
                y = _testata(c, asta, mandante, rif)
            c.setFont("Helvetica", 9.5)
            c.drawString(M, y, "Lotto")
            c.drawString(M + 16 * mm, y, a.lotto.numero)
            c.setFont("Helvetica", 8.5)
            titolo = a.lotto.etichetta + ("  (post asta)" if a.post_asta else "")
            c.drawString(M + 38 * mm, y, _taglia(c, titolo, "Helvetica", 8.5, 105 * mm))
            c.setFont("Helvetica", 9.5)
            c.drawRightString(LARG - M - 4 * mm, y, _e(a.prezzo_martello))
            y -= 4.8 * mm
        if not blocco["venduti"]:
            c.setFont("Helvetica-Oblique", 9)
            c.drawString(M + 16 * mm, y, "nessun lotto venduto")
            y -= 4.8 * mm
        y -= 3 * mm

    # note e totali
    t = r["totali"]
    _box(c, M, M + 22 * mm, 132 * mm, 48 * mm, "Note")
    c.setFont("Helvetica", 9.5)
    yn = M + 62 * mm
    for i in r["inserimenti"]:
        c.drawString(M + 8 * mm, yn, f"SPESE INSERIMENTO LOTTI NR. {i['n']} x {_e(i['unitario']).replace(',00', '')}€ = "
                                     f"{_e(i['importo']).replace(',00', '')}€")
        yn -= 4.5 * mm
    for s in r["spese"]:
        c.drawString(M + 8 * mm, yn, _taglia(c, f"ADDEBITO {s.descrizione.upper()} = {_e(s.importo).replace(',00', '')}€",
                                             "Helvetica", 9.5, 120 * mm))
        yn -= 4.5 * mm
    _box(c, M, M, 132 * mm, 12 * mm, "Data e Firma per Ricevuta")
    xr, wr = M + 138 * mm, LARG - 2 * M - 138 * mm
    pct = f"{_e(r['percentuale'])} %" if r["percentuale"] is not None else "vari %"
    blocchi = [("Totale Aggiudicazioni", _e(t["martello"])), (f"{pct} Diritti d'Asta", _e(t["commissione"]))]
    if t["iva_commissione"]:
        blocchi.append(("IVA su Diritti", _e(t["iva_commissione"])))
    blocchi.append(("Rimborso Spese", _e(t["spese"])))
    h = 12 * mm if len(blocchi) <= 3 else 10 * mm
    yb = M + 70 * mm - h
    for titolo, valore in blocchi:
        _box(c, xr, yb, wr, h, titolo)
        c.setFont("Helvetica", 10)
        c.drawRightString(xr + wr - 3 * mm, yb + 2 * mm, valore)
        yb -= h + 2 * mm
    _box(c, xr, M, wr, 12 * mm, "Totale")
    c.setFont("Helvetica-Bold", 10.5)
    c.drawRightString(xr + wr - 3 * mm, M + 2.5 * mm, _e(t["netto"]))
    c.showPage()


def pdf_mandanti(asta, mandanti) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"Rendiconti mandanti asta {asta.numero}")
    c.setAuthor(settings.CASA_ASTA_NOME)
    mandanti = list(mandanti)
    for m in mandanti:
        _pagina_mandante(c, asta, m)
    if not mandanti:
        c.setFont("Helvetica", 12)
        c.drawString(M, ALT - 40 * mm, "Nessun mandante con lotti in questa asta.")
        c.showPage()
    c.save()
    return buf.getvalue()
