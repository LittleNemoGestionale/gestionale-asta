import re
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render

from .models import (Aggiudicazione, Asta, Lotto, Paletta, Soggetto, rendiconto_mandante,
                     totale_asta)

def parse_euro(s: str) -> Decimal:
    """Accetta '1.500,50', '1500,50', '1500.50', '1500'."""
    s = s.strip().replace("€", "").replace(" ", "")
    if "," in s or re.fullmatch(r"\d{1,3}(\.\d{3})+", s):  # formato italiano
        s = s.replace(".", "").replace(",", ".")
    v = Decimal(s)
    if v <= 0:
        raise InvalidOperation
    return v


pii_required =permission_required("asta.view_dati_personali", raise_exception=True)


@login_required
def cruscotto(request, asta_id):
    asta = get_object_or_404(Asta, pk=asta_id)
    lotti = asta.lotti.all()
    mandanti = Soggetto.objects.filter(mandati__lotti__asta=asta).distinct().order_by("codice")
    return render(request, "asta/cruscotto.html", {
        "asta": asta,
        "n_lotti": lotti.count(),
        "n_aggiudicati": lotti.filter(stato=Lotto.Stato.AGGIUDICATO).count(),
        "n_invenduti": lotti.exclude(stato__in=[Lotto.Stato.AGGIUDICATO, Lotto.Stato.IN_ASTA]).count(),
        "totale": totale_asta(asta),
        "palette": asta.palette.all(),
        "mandanti": mandanti,
    })


@login_required
def doc_aggiudicazioni(request, asta_id):
    asta = get_object_or_404(Asta, pk=asta_id)
    agg = list(Aggiudicazione.objects.filter(lotto__asta=asta)
               .select_related("lotto__mandato", "lotto__asta", "paletta").order_by("lotto__ordine", "lotto__numero"))
    tot = {k: sum((getattr(a, k) for a in agg), Decimal(0))
           for k in ["prezzo_martello", "diritti", "iva_diritti", "commissione", "netto_mandante"]}
    tot["spedizioni"] = sum((p.spese_spedizione for p in asta.palette.all()), Decimal(0))
    return render(request, "asta/doc_aggiudicazioni.html", {"asta": asta, "agg": agg, "tot": tot})


@login_required
@pii_required
def doc_conto(request, asta_id, numero):
    paletta = get_object_or_404(Paletta.objects.select_related("asta", "acquirente"), asta_id=asta_id, numero=numero)
    return render(request, "asta/doc_conto.html", {"asta": paletta.asta, "p": paletta, "c": paletta.conto()})


@login_required
@pii_required
def doc_spedizione(request, asta_id, numero):
    paletta = get_object_or_404(Paletta.objects.select_related("asta", "acquirente", "indirizzo_spedizione"),
                                asta_id=asta_id, numero=numero)
    return render(request, "asta/doc_spedizione.html", {
        "asta": paletta.asta, "p": paletta, "lotti": [a.lotto for a in paletta.aggiudicazioni.all()]})


@login_required
@pii_required
def doc_rendiconto(request, asta_id, soggetto_id):
    asta = get_object_or_404(Asta, pk=asta_id)
    mandante = get_object_or_404(Soggetto, pk=soggetto_id)
    return render(request, "asta/doc_rendiconto.html",
                  {"asta": asta, "m": mandante, **rendiconto_mandante(asta, mandante)})


@login_required
def doc_invenduti(request, asta_id):
    asta = get_object_or_404(Asta, pk=asta_id)
    lotti = (asta.lotti.exclude(stato__in=[Lotto.Stato.AGGIUDICATO, Lotto.Stato.IN_ASTA])
             .select_related("mandato__mandante").order_by("mandato__mandante__codice", "ordine", "numero"))
    return render(request, "asta/doc_invenduti.html", {"asta": asta, "lotti": lotti})


@login_required
@permission_required("asta.view_dati_personali", raise_exception=True)
@permission_required("asta.change_mandato", raise_exception=True)
def scarica_backup(request):
    """Backup Excel al volo (protetto con ASTA_BACKUP_PASSWORD) per l'amministrazione."""
    import os
    from datetime import datetime

    from django.http import HttpResponse

    from .export_excel import esporta_bytes
    pwd = os.environ.get("ASTA_BACKUP_PASSWORD")
    if not pwd and not settings.DEBUG:
        return HttpResponse("Password di backup non configurata.", status=500)
    resp = HttpResponse(esporta_bytes(pwd),
                        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="gestionale-asta_{datetime.now():%Y-%m-%d_%H%M}.xlsx"'
    return resp


# ------------------------------------------------------------------ contabilità

def _puo_contabilita(user):
    return user.has_perm("asta.view_incasso")


@login_required
@permission_required("asta.view_incasso", raise_exception=True)
def incassi(request, asta_id):
    from .models import iva_mensile
    asta = get_object_or_404(Asta, pk=asta_id)
    righe = []
    for p in asta.palette.filter(aggiudicazioni__isnull=False).distinct().order_by("numero"):
        righe.append({"p": p, **p.situazione_incasso()})
    tot = {k: sum((r[k] for r in righe), Decimal(0))
           for k in ["totale", "martello", "diritti", "spese_spedizione", "incassato", "residuo"]}
    return render(request, "asta/incassi.html", {
        "asta": asta, "righe": righe, "tot": tot, "iva": iva_mensile(asta),
        "n_da_incassare": sum(1 for r in righe if r["stato"] != "incassato")})


@login_required
@permission_required("asta.view_pagamentomandante", raise_exception=True)
def pagamenti_mandanti(request, asta_id):
    from .models import situazione_mandanti
    asta = get_object_or_404(Asta, pk=asta_id)
    righe = situazione_mandanti(asta)
    tot = {k: sum((r[k] for r in righe), Decimal(0)) for k in ["dovuto", "pagato", "residuo", "pagabile"]}
    return render(request, "asta/pagamenti_mandanti.html", {"asta": asta, "righe": righe, "tot": tot})


@login_required
def excel_contabilita(request, asta_id, quale):
    from django.core.exceptions import PermissionDenied
    from django.http import HttpResponse

    from .export_contabile import excel_contabile
    perm = "asta.view_incasso" if quale == "incassi" else "asta.view_pagamentomandante"
    if quale not in ("incassi", "mandanti") or not request.user.has_perm(perm):
        raise PermissionDenied
    asta = get_object_or_404(Asta, pk=asta_id)
    nome = f"{'incassi' if quale == 'incassi' else 'pagamenti_mandanti'}_asta_{asta.numero or asta.pk}.xlsx"
    resp = HttpResponse(excel_contabile(asta, quale),
                        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="{nome}"'
    return resp
