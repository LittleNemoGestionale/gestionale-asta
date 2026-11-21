"""PDF «Aggiudicazioni relative alla Paletta N.» (come il vecchio report GestAsteContAggPal).

Una pagina per paletta: intestazione della casa d'aste, codice/nome/email del cliente, lotti con base e
valore di aggiudicazione, totale, diritti, IVA sui diritti, rimborso spese di spedizione, totale generale.
"""
import io
import os
from decimal import Decimal

from django.conf import settings
from reportlab.lib.colors import black, HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

LARG, ALT = A4
MARG = 15 * mm
GRIGIO = HexColor("#555555")


def _e(v) -> str:
    """1234.5 -> '1.234,50'"""
    return f"{Decimal(v or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _taglia(c, testo, font, size, larghezza):
    testo = (testo or "").replace("\n", " ")
    while testo and c.stringWidth(testo, font, size) > larghezza:
        testo = testo[:-2]
    return testo


def _intestazione(c, asta, paletta, pagina, pagine):
    y = ALT - MARG
    logo = settings.CASA_ASTA_LOGO
    if logo and os.path.exists(logo):
        c.drawImage(logo, MARG, y - 22 * mm, width=65 * mm, height=22 * mm, preserveAspectRatio=True, anchor="nw",
                    mask="auto")
        y -= 25 * mm
    else:
        c.setFont("Helvetica-Bold", 16)
        c.drawString(MARG, y - 6 * mm, settings.CASA_ASTA_NOME)
        y -= 10 * mm
    c.setFont("Helvetica", 7)
    c.setFillColor(GRIGIO)
    for riga in [r.strip() for r in settings.CASA_ASTA_INTESTAZIONE.split("|") if r.strip()]:
        c.drawString(MARG, y, riga)
        y -= 3.4 * mm
    c.setFillColor(black)
    y -= 6 * mm
    fine = asta.data_fine or asta.data
    c.setFont("Helvetica", 12)
    c.drawString(MARG, y, f"Asta N.   {asta.numero or ''}")
    c.drawString(MARG + 32 * mm, y, f"{asta.data:%d/%m/%Y} - {fine:%d/%m/%Y}")
    c.drawRightString(LARG - MARG - 18 * mm, y, "Aggiudicazioni relative alla Paletta N.")
    c.setFont("Helvetica-Bold", 12)
    c.drawRightString(LARG - MARG, y, str(paletta.numero))
    y -= 9 * mm
    # riquadro con i dati del cliente
    c.rect(MARG, y - 3 * mm, LARG - 2 * MARG, 9 * mm)
    c.setFont("Helvetica", 11)
    cl = paletta.acquirente
    c.drawString(MARG + 3 * mm, y, cl.codice)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(MARG + 32 * mm, y, _taglia(c, cl.nome, "Helvetica-Bold", 11, 85 * mm))
    c.setFont("Helvetica", 10)
    c.drawString(MARG + 122 * mm, y, _taglia(c, cl.email, "Helvetica", 10, 55 * mm))
    y -= 13 * mm
    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(MARG, y, "Numero e Titolo Lotto")
    c.drawRightString(LARG - MARG - 30 * mm, y, "Base d'Asta")
    c.drawRightString(LARG - MARG, y, "Valore Agg.")
    y -= 2.5 * mm
    c.line(MARG, y, LARG - MARG, y)
    if pagine > 1:
        c.setFont("Helvetica", 8)
        c.setFillColor(GRIGIO)
        c.drawRightString(LARG - MARG, MARG / 2, f"pagina {pagina} di {pagine}")
        c.setFillColor(black)
    return y - 6 * mm


def _pagina_paletta(c, paletta):
    asta = paletta.asta
    s = paletta.situazione_incasso()
    agg = s["aggiudicazioni"]
    per_pagina = 30
    pagine = max(1, (len(agg) + per_pagina - 1) // per_pagina)
    for n in range(pagine):
        y = _intestazione(c, asta, paletta, n + 1, pagine)
        c.setFont("Helvetica", 9.5)
        for a in agg[n * per_pagina:(n + 1) * per_pagina]:
            c.drawString(MARG + 4 * mm, y, a.lotto.numero)
            titolo = a.lotto.etichetta + (" (post asta)" if a.post_asta else "")
            c.setFont("Helvetica", 8.5)
            c.drawString(MARG + 50 * mm, y, _taglia(c, titolo, "Helvetica", 8.5, 62 * mm))
            c.setFont("Helvetica", 9.5)
            c.drawRightString(LARG - MARG - 30 * mm, y, _e(a.lotto.base_asta) if a.lotto.base_asta else "")
            c.drawRightString(LARG - MARG, y, _e(a.prezzo_martello))
            y -= 6.3 * mm
        if n < pagine - 1:
            c.showPage()
    # riepilogo
    y -= 8 * mm
    x1, x2, x3 = MARG + 60 * mm, LARG - MARG - 42 * mm, LARG - MARG
    iva_pct = Decimal(0) if asta.diritti_iva_inclusa else asta.iva_pct
    iva = Decimal(0) if asta.diritti_iva_inclusa else s["iva_diritti"]
    righe = [("Totale Aggiudicazioni", "", f"{_e(s['martello'])} €"),
             ("Diritti d'asta", f"{_e(paletta.diritti_effettivi)} %", f"{_e(s['diritti'])} €"),
             ("Iva su Diritti", f"{_e(iva_pct)} %", f"{_e(iva)} €"),
             ("Rimborso Spese", (paletta.modalita.nome if paletta.modalita else "Spedizione")[:22],
              f"{_e(s['spese_spedizione'])} €")]
    if s["abbuono"]:
        righe.append(("Abbuono", "", f"-{_e(s['abbuono'])} €"))
    for etichetta, mezzo, valore in righe:
        c.setFont("Helvetica-Bold", 11)
        c.drawString(x1, y, etichetta)
        c.setFont("Helvetica-Bold", 9)
        c.drawRightString(x2, y, mezzo)
        c.drawRightString(x3, y, valore)
        y -= 7.5 * mm
    c.setFont("Helvetica-Bold", 11.5)
    c.drawString(x1, y, "TOTALE GENERALE")
    c.drawRightString(x3, y, f"{_e(s['totale'])} €")
    c.showPage()


def pdf_palette(palette) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle("Aggiudicazioni per paletta")
    c.setAuthor(settings.CASA_ASTA_NOME)
    palette = list(palette)
    for p in palette:
        _pagina_paletta(c, p)
    if not palette:
        c.setFont("Helvetica", 12)
        c.drawString(MARG, ALT - 40 * mm, "Nessuna paletta con aggiudicazioni.")
        c.showPage()
    c.save()
    return buf.getvalue()
