"""Dopo l'asta: per ogni acquirente si sceglie la consegna (e quindi il costo) e si prepara l'email per Outlook."""
import io
import zipfile
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone

from .models import Asta, ModalitaConsegna, Paletta, StatoConsegna

PAESI_UE = {"austria", "belgio", "bulgaria", "cipro", "croazia", "danimarca", "estonia", "finlandia", "francia",
            "germania", "grecia", "irlanda", "lettonia", "lituania", "lussemburgo", "malta", "paesi bassi", "olanda",
            "polonia", "portogallo", "repubblica ceca", "romania", "slovacchia", "slovenia", "spagna", "svezia",
            "ungheria", "belgium", "france", "germany", "spain", "netherlands", "be", "fr", "de", "es", "nl", "at"}

perm = [login_required, permission_required("asta.view_dati_personali", raise_exception=True),
        permission_required("asta.change_paletta", raise_exception=True)]


def con_permessi(f):
    for d in reversed(perm):
        f = d(f)
    return f


def zona(nazione: str) -> str:
    n = (nazione or "Italia").strip().lower()
    if n in ("italia", "it", "italy", ""):
        return "it"
    return "ue" if n in PAESI_UE else "extra"


def proposta(paletta, modalita):
    """Modalità suggerita: quella già scelta, altrimenti la spedizione della zona dell'indirizzo."""
    if paletta.modalita_id:
        return paletta.modalita
    ind = paletta.acquirente.indirizzo_spedizione
    if not ind:
        return None
    z = zona(ind.nazione)
    return next((m for m in modalita if m.zona == z), None)


def dati_email(paletta) -> dict:
    asta = paletta.asta
    s = paletta.situazione_incasso()
    oggetto = f"Asta {asta.numero} – riepilogo dei Suoi acquisti (paletta {paletta.numero})"
    return {"p": paletta, "asta": asta, "s": s, "oggetto": oggetto, "casa": settings.CASA_ASTA_NOME,
            "iban": settings.CASA_ASTA_IBAN, "intestatario": settings.CASA_ASTA_INTESTATARIO or settings.CASA_ASTA_NOME,
            "firma": settings.CASA_ASTA_FIRMA or settings.CASA_ASTA_NOME,
            "indirizzo": paletta.indirizzo_spedizione if paletta.modalita and paletta.modalita.spedizione else None}


def costruisci_email(paletta):
    from . import email_util
    from .documento_aggiudicazioni import pdf_palette
    d = dati_email(paletta)
    return email_util.costruisci(
        d["oggetto"], paletta.acquirente.nome, paletta.acquirente.email,
        render_to_string("asta/email_acquirente.txt", d), render_to_string("asta/email_acquirente.html", d),
        [(f"Aggiudicazioni_Asta{paletta.asta.numero}_Paletta{paletta.numero}.pdf", pdf_palette([paletta]),
          "application/pdf")])


def crea_eml(paletta) -> bytes:
    """Bozza per Outlook: con l'intestazione X-Unsent Outlook la apre come nuovo messaggio da inviare."""
    from . import email_util
    return email_util.come_eml(costruisci_email(paletta))


def invia_email(request, palette):
    from . import email_util
    if not email_util.invio_diretto_attivo():
        messages.error(request, "L'invio diretto non è configurato: usa «Apri in Outlook».")
        return
    ok, errori = 0, []
    for p in palette:
        try:
            email_util.invia(costruisci_email(p))
            p.email_preparata_il = timezone.now()
            p.save(update_fields=["email_preparata_il"])
            ok += 1
        except Exception as e:  # una casella sbagliata non ferma le altre
            errori.append(f"paletta {p.numero}: {e}")
    if ok:
        messages.success(request, f"{ok} email inviat{'a' if ok == 1 else 'e'} agli acquirenti.")
    for e in errori:
        messages.error(request, f"Non inviata – {e}")


@con_permessi
def acquirenti(request, asta_id):
    asta = get_object_or_404(Asta, pk=asta_id)
    modalita = list(ModalitaConsegna.objects.all())
    if request.method == "POST" and request.POST.get("azione") in ("invia", "invia_tutte"):
        qs = asta.palette.filter(aggiudicazioni__isnull=False).exclude(modalita=None).distinct()
        if request.POST.get("azione") == "invia":
            qs = qs.filter(pk=request.POST.get("paletta"))
        else:
            qs = qs.filter(email_preparata_il=None)
        invia_email(request, list(qs.select_related("acquirente", "modalita")))
        return redirect("acquirenti", asta.pk)
    if request.method == "POST":
        p = get_object_or_404(Paletta, pk=request.POST.get("paletta"), asta=asta)
        m = ModalitaConsegna.objects.filter(pk=request.POST.get("modalita") or 0).first()
        try:
            spese = Decimal((request.POST.get("spese") or "").replace(",", ".")) if request.POST.get("spese") else (
                m.costo if m else Decimal(0))
        except InvalidOperation:
            messages.error(request, f"Paletta {p.numero}: costo non valido.")
            return redirect(f"{request.path}#p{p.numero}")
        if m and m.spedizione and not p.acquirente.indirizzo_spedizione:
            messages.error(request, f"{p.acquirente.nome} non ha un indirizzo in scheda: aggiungilo prima di scegliere la spedizione.")
            return redirect(f"{request.path}#p{p.numero}")
        p.modalita = m
        p.indirizzo_spedizione = p.acquirente.indirizzo_spedizione if m and m.spedizione else None
        p.spese_spedizione = spese if m else 0
        if p.stato_consegna in (StatoConsegna.DA_DEFINIRE, StatoConsegna.RITIRA, StatoConsegna.DA_SPEDIRE):
            p.stato_consegna = (StatoConsegna.DA_DEFINIRE if not m else
                                StatoConsegna.DA_SPEDIRE if m.spedizione else StatoConsegna.RITIRA)
        p.save()
        messages.success(request, f"Paletta {p.numero}: {m.nome if m else 'consegna da decidere'}"
                                  + (f", € {p.spese_spedizione}" if p.spese_spedizione else "") + ".")
        return redirect(f"{request.path}?{request.GET.urlencode()}#p{p.numero}")

    righe = []
    for p in (asta.palette.filter(aggiudicazioni__isnull=False).distinct()
              .select_related("acquirente", "modalita", "indirizzo_spedizione").order_by("numero")):
        s = p.situazione_incasso()
        righe.append({"p": p, "s": s, "proposta": proposta(p, modalita), "ind": p.acquirente.indirizzo_spedizione,
                      "n_lotti": len(s["aggiudicazioni"]), "email": p.acquirente.email})
    filtro = request.GET.get("filtro", "")
    n_da_pagare = sum(1 for r in righe if r["s"]["residuo"] > 0)
    if filtro == "da_pagare":
        righe = [r for r in righe if r["s"]["residuo"] > 0]
    elif filtro == "da_decidere":
        righe = [r for r in righe if not r["p"].modalita_id]
    elif filtro == "email":
        righe = [r for r in righe if r["p"].modalita_id and not r["p"].email_preparata_il]
    tutte = asta.palette.filter(aggiudicazioni__isnull=False).distinct()
    return render(request, "asta/acquirenti.html", {
        "asta": asta, "righe": righe, "modalita": modalita, "filtro": filtro,
        "n_tot": tutte.count(), "n_decise": tutte.exclude(modalita=None).count(),
        "n_da_decidere": tutte.filter(modalita=None).count(), "n_da_pagare": n_da_pagare,
        "n_email": tutte.exclude(email_preparata_il=None).count(),
        "manca_iban": not settings.CASA_ASTA_IBAN,
        "invio_diretto": __import__("asta.email_util", fromlist=["x"]).invio_diretto_attivo()})


@con_permessi
def email_anteprima(request, asta_id, numero):
    p = get_object_or_404(Paletta.objects.select_related("asta", "acquirente", "modalita"), asta_id=asta_id, numero=numero)
    return render(request, "asta/email_anteprima.html", {**dati_email(p), "corpo": render_to_string("asta/email_acquirente.html", dati_email(p))})


@con_permessi
def email_eml(request, asta_id, numero):
    p = get_object_or_404(Paletta.objects.select_related("asta", "acquirente", "modalita"), asta_id=asta_id, numero=numero)
    dati = crea_eml(p)
    p.email_preparata_il = timezone.now()
    p.save(update_fields=["email_preparata_il"])
    resp = HttpResponse(dati, content_type="message/rfc822")
    resp["Content-Disposition"] = f'attachment; filename="Asta{p.asta.numero}_paletta{p.numero}.eml"'
    return resp


@con_permessi
def email_tutte(request, asta_id):
    """Uno zip con le email di tutti gli acquirenti con la consegna già decisa e non ancora preparate."""
    asta = get_object_or_404(Asta, pk=asta_id)
    palette = (asta.palette.filter(aggiudicazioni__isnull=False, email_preparata_il=None).exclude(modalita=None)
               .distinct().select_related("acquirente", "modalita").order_by("numero"))
    if not palette:
        messages.info(request, "Nessuna email da preparare: scegli prima la consegna degli acquirenti.")
        return redirect("acquirenti", asta.pk)
    buf = io.BytesIO()
    adesso = timezone.now()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in palette:
            z.writestr(f"Asta{asta.numero}_paletta{p.numero}.eml", crea_eml(p))
            p.email_preparata_il = adesso
            p.save(update_fields=["email_preparata_il"])
    resp = HttpResponse(buf.getvalue(), content_type="application/zip")
    resp["Content-Disposition"] = f'attachment; filename="Email_acquirenti_asta{asta.numero}.zip"'
    return resp


@con_permessi
def pdf_conteggio(request, asta_id, numero):
    from .documento_aggiudicazioni import pdf_palette
    p = get_object_or_404(Paletta.objects.select_related("asta", "acquirente", "modalita"), asta_id=asta_id, numero=numero)
    resp = HttpResponse(pdf_palette([p]), content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="Aggiudicazioni_Asta{p.asta.numero}_Paletta{p.numero}.pdf"'
    return resp


@con_permessi
def pdf_conteggi_tutti(request, asta_id):
    """Un PDF con una pagina per ogni paletta che ha comprato (per stampare tutto in una volta)."""
    from .documento_aggiudicazioni import pdf_palette
    asta = get_object_or_404(Asta, pk=asta_id)
    palette = (asta.palette.filter(aggiudicazioni__isnull=False).distinct()
               .select_related("acquirente", "modalita").order_by("numero"))
    if request.GET.get("filtro") == "da_pagare":
        palette = [p for p in palette if p.situazione_incasso()["residuo"] > 0]
    resp = HttpResponse(pdf_palette(palette), content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="Aggiudicazioni_Asta{asta.numero}_tutte_le_palette.pdf"'
    return resp
