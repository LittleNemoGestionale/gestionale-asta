"""Dopo l'asta: conteggio per mandante (aggiudicato − diritti − spese), PDF del rendiconto e invio per email."""
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

from . import email_util
from .documento_mandante import pdf_mandanti
from .models import (Asta, Mandato, PagamentoMandante, RendicontoMandante, Soggetto, SpesaMandante,
                     rendiconto_mandante)


def con_permessi(f):
    for d in reversed([login_required, permission_required("asta.view_dati_personali", raise_exception=True),
                       permission_required("asta.view_spesamandante", raise_exception=True)]):
        f = d(f)
    return f


def mandanti_asta(asta):
    return Soggetto.objects.filter(mandati__lotti__asta=asta).distinct().order_by("codice")


def costruisci_email(asta, mandante):
    r = rendiconto_mandante(asta, mandante)
    rif = RendicontoMandante.per(asta, mandante)
    d = {"asta": asta, "m": mandante, "r": r, "rif": rif, "firma": settings.CASA_ASTA_FIRMA or settings.CASA_ASTA_NOME,
         "casa": settings.CASA_ASTA_NOME}
    oggetto = f"Asta {asta.numero} – rendiconto dei Suoi lotti (rif. {rif.numero}/{rif.anno})"
    return email_util.costruisci(
        oggetto, mandante.nome, mandante.email, render_to_string("asta/email_mandante.txt", d),
        render_to_string("asta/email_mandante.html", d),
        [(f"Rendiconto_Asta{asta.numero}_{mandante.codice}.pdf", pdf_mandanti(asta, [mandante]), "application/pdf")]), d


@con_permessi
def mandanti(request, asta_id):
    asta = get_object_or_404(Asta, pk=asta_id)
    if request.method == "POST":
        azione = request.POST.get("azione")
        if azione == "spesa":
            mandato = get_object_or_404(Mandato, pk=request.POST.get("mandato"))
            try:
                importo = Decimal((request.POST.get("importo") or "").replace(",", "."))
            except InvalidOperation:
                importo = None
            descr = (request.POST.get("descrizione") or "").strip()
            if descr and not descr.lower().startswith(("spese", "spesa")):
                descr = f"Spese {descr[0].lower()}{descr[1:]}"  # nel PDF: «ADDEBITO SPESE PICCOLO RESTAURO = 20€»
            if not descr or not importo or importo <= 0:
                messages.error(request, "Per aggiungere una spesa servono descrizione e importo.")
            else:
                SpesaMandante.objects.create(asta=asta, mandato=mandato, descrizione=descr[:120], importo=importo)
                messages.success(request, f"Spesa «{descr}» di € {importo} aggiunta a {mandato.mandante.nome}.")
            return redirect(f"{request.path}#m{mandato.mandante_id}")
        if azione == "inserimento":
            mandato = get_object_or_404(Mandato, pk=request.POST.get("mandato"))
            try:
                valore = Decimal((request.POST.get("valore") or "0").replace("€", "").replace(",", ".").strip() or "0")
            except InvalidOperation:
                valore = None
            if valore is None or valore < 0:
                messages.error(request, "Scrivi l'importo per lotto, es. 10 (0 = non addebitare).")
            else:
                mandato.spesa_inserimento = valore
                mandato.save(update_fields=["spesa_inserimento"])
                messages.success(request, f"Mandato {mandato.numero}: spese di inserimento a € {valore} per lotto.")
            return redirect(f"{request.path}#m{mandato.mandante_id}")
        if azione == "togli_spesa":
            s = get_object_or_404(SpesaMandante, pk=request.POST.get("spesa"), asta=asta)
            mid = s.mandato.mandante_id
            s.delete()
            messages.info(request, "Spesa tolta.")
            return redirect(f"{request.path}#m{mid}")
        if azione == "invia":
            m = get_object_or_404(Soggetto, pk=request.POST.get("mandante"))
            return _invia(request, asta, [m])
        if azione == "invia_tutti":
            return _invia(request, asta, list(mandanti_asta(asta)))

    righe = []
    for m in mandanti_asta(asta):
        r = rendiconto_mandante(asta, m)
        pagato = sum((p.importo for p in PagamentoMandante.objects.filter(asta=asta, mandato__mandante=m)), Decimal(0))
        rif = RendicontoMandante.objects.filter(asta=asta, mandante=m).first()
        righe.append({"m": m, "r": r, "n_lotti": len(r["lotti"]), "n_venduti": len(r["venduti"]), "pagato": pagato,
                      "residuo": r["totali"]["netto"] - pagato, "rif": rif,
                      "mandati": sorted({l.mandato for l in r["lotti"]}, key=lambda x: x.numero)})
    tot = {k: sum((x["r"]["totali"][k] for x in righe), Decimal(0)) for k in ["martello", "commissione", "spese", "netto"]}
    return render(request, "asta/mandanti.html", {
        "asta": asta, "righe": righe, "tot": tot, "invio_diretto": email_util.invio_diretto_attivo()})


def _invia(request, asta, lista):
    if not email_util.invio_diretto_attivo():
        messages.error(request, "L'invio diretto non è configurato: usa «Apri in Outlook» oppure indica i dati della "
                                "casella di posta nelle impostazioni.")
        return redirect("mandanti", asta.pk)
    ok, errori = 0, []
    for m in lista:
        try:
            em, d = costruisci_email(asta, m)
            email_util.invia(em)
            d["rif"].email_inviata_il = timezone.now()
            d["rif"].save(update_fields=["email_inviata_il"])
            ok += 1
        except Exception as e:  # una casella sbagliata non deve fermare le altre
            errori.append(f"{m.nome}: {e}")
    if ok:
        messages.success(request, f"{ok} rendicont{'o inviato' if ok == 1 else 'i inviati'} per email.")
    for e in errori:
        messages.error(request, f"Non inviato – {e}")
    return redirect("mandanti", asta.pk)


@con_permessi
def pdf_mandante(request, asta_id, soggetto_id):
    asta = get_object_or_404(Asta, pk=asta_id)
    m = get_object_or_404(Soggetto, pk=soggetto_id)
    resp = HttpResponse(pdf_mandanti(asta, [m]), content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="Rendiconto_Asta{asta.numero}_{m.codice}.pdf"'
    return resp


@con_permessi
def pdf_tutti(request, asta_id):
    asta = get_object_or_404(Asta, pk=asta_id)
    resp = HttpResponse(pdf_mandanti(asta, list(mandanti_asta(asta))), content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="Rendiconti_mandanti_Asta{asta.numero}.pdf"'
    return resp


@con_permessi
def email_anteprima(request, asta_id, soggetto_id):
    asta = get_object_or_404(Asta, pk=asta_id)
    m = get_object_or_404(Soggetto, pk=soggetto_id)
    em, d = costruisci_email(asta, m)
    return render(request, "asta/email_mandante_anteprima.html", {**d, "oggetto": em.subject,
                                                                    "corpo": em.alternatives[0][0]})


@con_permessi
def email_eml(request, asta_id, soggetto_id):
    asta = get_object_or_404(Asta, pk=asta_id)
    m = get_object_or_404(Soggetto, pk=soggetto_id)
    em, d = costruisci_email(asta, m)
    d["rif"].email_preparata_il = timezone.now()
    d["rif"].save(update_fields=["email_preparata_il"])
    resp = HttpResponse(email_util.come_eml(em), content_type="message/rfc822")
    resp["Content-Disposition"] = f'attachment; filename="Rendiconto_Asta{asta.numero}_{m.codice}.eml"'
    return resp


@con_permessi
def email_tutte(request, asta_id):
    asta = get_object_or_404(Asta, pk=asta_id)
    buf = io.BytesIO()
    adesso = timezone.now()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for m in mandanti_asta(asta):
            em, d = costruisci_email(asta, m)
            z.writestr(f"Rendiconto_Asta{asta.numero}_{m.codice}.eml", email_util.come_eml(em))
            d["rif"].email_preparata_il = adesso
            d["rif"].save(update_fields=["email_preparata_il"])
    resp = HttpResponse(buf.getvalue(), content_type="application/zip")
    resp["Content-Disposition"] = f'attachment; filename="Email_mandanti_asta{asta.numero}.zip"'
    return resp
