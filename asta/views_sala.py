"""Aggiudicazione dei lotti durante l'asta: una riga per lotto con paletta e prezzo (come il foglio FAX)."""
import json
import re
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .models import Aggiudicazione, Asta, Lotto, Paletta, Soggetto, Tornata


def parse_euro(s: str) -> Decimal:
    """Accetta '1.500,50', '1500,50', '1500.50', '1500', '1.500'."""
    s = (s or "").strip().replace("€", "").replace(" ", "")
    if "," in s or re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "").replace(",", ".")
    v = Decimal(s)
    if v <= 0:
        raise InvalidOperation
    return v


def stato_lotto(l: Lotto) -> dict:
    a = getattr(l, "aggiudicazione", None)
    return {"id": l.pk, "numero": l.numero, "stato": l.stato,
            "paletta": a.paletta.numero if a else None,
            "prezzo": f"{a.prezzo_martello:.2f}" if a else None,
            "sotto_riserva": bool(a and a.sotto_riserva)}


def registra(asta, lotto, azione, paletta_txt="", prezzo_txt="", conferma=False, utente=None) -> dict:
    """Registra un'aggiudicazione, un invenduto o una correzione. Restituisce l'esito per la pagina."""
    if asta.chiusa:
        return {"ok": False, "errore": "L'asta è conclusa: non si possono più registrare aggiudicazioni."}
    if azione == "annulla":
        with transaction.atomic():
            Aggiudicazione.objects.filter(lotto=lotto).delete()
            lotto.stato = Lotto.Stato.IN_ASTA
            lotto.save(update_fields=["stato"])
        return {"ok": True, "msg": f"Lotto {lotto.numero}: registrazione annullata, si può rifare."}
    if azione == "invenduto":
        if hasattr(lotto, "aggiudicazione"):
            return {"ok": False, "errore": f"Il lotto {lotto.numero} è già aggiudicato: prima premi «Correggi»."}
        lotto.stato = Lotto.Stato.INVENDUTO
        lotto.save(update_fields=["stato"])
        return {"ok": True, "msg": f"Lotto {lotto.numero}: invenduto."}

    # aggiudicazione
    try:
        numero = int(str(paletta_txt).strip())
        paletta = Paletta.objects.get(asta=asta, numero=numero)
    except (ValueError, Paletta.DoesNotExist):
        return {"ok": False, "campo": "paletta",
                "errore": f"La paletta «{paletta_txt}» non è assegnata in questa asta."}
    try:
        prezzo = parse_euro(str(prezzo_txt))
    except (InvalidOperation, ValueError):
        return {"ok": False, "campo": "prezzo", "errore": "Prezzo non valido."}
    avvisi = []
    if lotto.riserva and prezzo < lotto.riserva:
        avvisi.append(f"sotto la riserva di € {lotto.riserva:,.0f}".replace(",", "."))
    elif lotto.base_asta and prezzo < lotto.base_asta:
        avvisi.append(f"sotto la base di € {lotto.base_asta:,.0f}".replace(",", "."))
    if avvisi and not conferma:
        return {"ok": False, "conferma": True,
                "errore": f"Lotto {lotto.numero} a € {prezzo:,.0f}: {avvisi[0]}. Confermi?".replace(",", ".")}
    try:
        with transaction.atomic():
            Aggiudicazione.objects.create(lotto=lotto, paletta=paletta, prezzo_martello=prezzo, registrata_da=utente,
                                          sotto_riserva=bool(lotto.riserva and prezzo < lotto.riserva))
    except IntegrityError:
        return {"ok": False, "errore": f"Il lotto {lotto.numero} è già aggiudicato: per cambiarlo premi «Correggi»."}
    lotto.refresh_from_db()
    return {"ok": True, "msg": f"Lotto {lotto.numero} → paletta {paletta.numero} a € {prezzo:,.2f}"
            .replace(",", "X").replace(".", ",").replace("X", ".")}


@login_required
@permission_required("asta.add_aggiudicazione", raise_exception=True)
def sala(request, asta_id):
    asta = get_object_or_404(Asta, pk=asta_id)

    # registrazione da modulo (senza JavaScript) — campo «lotto» = numero del lotto
    if request.method == "POST" and request.content_type != "application/json":
        try:
            lotto = Lotto.objects.select_related("aggiudicazione").get(
                asta=asta, numero=request.POST.get("lotto", "").strip())
        except Lotto.DoesNotExist:
            messages.error(request, f"Il lotto {request.POST.get('lotto')} non esiste in questa asta.")
            return redirect("sala", asta.pk)
        esito = registra(asta, lotto, request.POST.get("azione", "aggiudica"), request.POST.get("paletta", ""),
                         request.POST.get("prezzo", ""), request.POST.get("conferma") == "1", request.user)
        (messages.success if esito["ok"] else messages.error)(request, esito.get("msg") or esito.get("errore"))
        return redirect(f"{request.path}?tornata={lotto.tornata or 1}#l{lotto.pk}")

    # registrazione dalla tabella (JavaScript): risposta JSON con lo stato aggiornato della riga
    if request.method == "POST":
        dati = json.loads(request.body or "{}")
        lotto = get_object_or_404(Lotto.objects.select_related("aggiudicazione__paletta"), pk=dati.get("lotto"),
                                  asta=asta)
        esito = registra(asta, lotto, dati.get("azione", "aggiudica"), dati.get("paletta", ""),
                         dati.get("prezzo", ""), bool(dati.get("conferma")), request.user)
        lotto = Lotto.objects.select_related("aggiudicazione__paletta").get(pk=lotto.pk)
        return JsonResponse({**esito, "lotto": stato_lotto(lotto)})

    # aggiornamento periodico da altre postazioni
    if request.GET.get("formato") == "json":
        lotti = asta.lotti.select_related("aggiudicazione__paletta")
        return JsonResponse({"lotti": [stato_lotto(l) for l in lotti]})

    tornate_lotti = sorted({t or 1 for t in asta.lotti.values_list("tornata", flat=True)}) or [1]
    try:
        tornata = int(request.GET.get("tornata") or 0)
    except ValueError:
        tornata = 0
    if tornata not in tornate_lotti:  # parte dalla prima tornata con lotti ancora da battere
        da_battere = asta.lotti.filter(stato=Lotto.Stato.IN_ASTA).order_by("ordine").values_list("tornata", flat=True).first()
        tornata = da_battere or tornate_lotti[0]
    lotti = list(asta.lotti.prefetch_related("offerte_scritte__paletta", "telefonate__paletta")
                 .filter(Q(tornata=tornata) | (Q(tornata__isnull=True) if tornata == 1 else Q(pk__in=[])))
                 .exclude(stato=Lotto.Stato.RIPROPOSTO)
                 .select_related("aggiudicazione__paletta").order_by("ordine", "numero"))
    venduti = [l for l in lotti if hasattr(l, "aggiudicazione")]
    orari = {t.numero: t for t in Tornata.objects.filter(asta=asta)}
    return render(request, "asta/sala.html", {
        "asta": asta, "lotti": lotti, "tornata": tornata, "tornate": [(t, orari.get(t)) for t in tornate_lotti],
        "n_venduti": len(venduti), "n_invenduti": sum(1 for l in lotti if l.stato == Lotto.Stato.INVENDUTO),
        "n_da_battere": sum(1 for l in lotti if l.stato == Lotto.Stato.IN_ASTA),
        "totale": sum((l.aggiudicazione.prezzo_martello for l in venduti), Decimal(0)),
        "palette": sorted(asta.palette.values_list("numero", flat=True)),
    })


# ------------------------------------------------------------------ post asta

@login_required
@permission_required("asta.add_aggiudicazione", raise_exception=True)
@permission_required("asta.view_dati_personali", raise_exception=True)
def post_asta(request, asta_id):
    """Vendita degli invenduti nei giorni dopo l'asta (di solito 30), con palette della fascia post asta."""
    from django.utils import timezone

    from .models import Soggetto
    from .views_anagrafica import prossima_per_canale
    asta = get_object_or_404(Asta, pk=asta_id)
    if request.method == "POST":
        lotto = get_object_or_404(Lotto, pk=request.POST.get("lotto"), asta=asta)
        if request.POST.get("azione") == "annulla":
            a = Aggiudicazione.objects.filter(lotto=lotto, post_asta=True).first()
            if a:
                a.delete()
                lotto.stato = Lotto.Stato.INVENDUTO
                lotto.save(update_fields=["stato"])
                messages.info(request, f"Vendita post asta del lotto {lotto.numero} annullata.")
            return redirect("post_asta", asta.pk)
        if not asta.post_asta_aperto:
            messages.error(request, f"Il post asta è chiuso (dal {asta.fine_post_asta:%d/%m/%Y}). "
                                    "Per prolungarlo cambia i giorni di post asta nei dati dell'asta.")
            return redirect("post_asta", asta.pk)
        if lotto.stato != Lotto.Stato.INVENDUTO:
            messages.error(request, f"Il lotto {lotto.numero} non è tra gli invenduti.")
            return redirect("post_asta", asta.pk)
        try:
            cliente = Soggetto.objects.get(pk=request.POST.get("cliente"))
        except (Soggetto.DoesNotExist, ValueError):
            messages.error(request, f"Lotto {lotto.numero}: scegli il cliente dall'elenco.")
            return redirect(f"{request.path}#l{lotto.pk}")
        try:
            prezzo = parse_euro(request.POST.get("prezzo", ""))
        except (InvalidOperation, ValueError):
            messages.error(request, f"Lotto {lotto.numero}: prezzo non valido.")
            return redirect(f"{request.path}#l{lotto.pk}")
        if lotto.riserva and prezzo < lotto.riserva and request.POST.get("conferma") != "1":
            messages.error(request, f"Lotto {lotto.numero}: € {prezzo:,.0f} è sotto la riserva di € {lotto.riserva:,.0f}. "
                                    "Spunta «sotto riserva» per vendere comunque.".replace(",", "."))
            return redirect(f"{request.path}#l{lotto.pk}")
        with transaction.atomic():
            paletta = Paletta.objects.filter(asta=asta, acquirente=cliente).first()
            if not paletta:  # chi non ha già una paletta in quest'asta ne riceve una della fascia post asta
                numero = prossima_per_canale(asta, "post_asta")
                if numero is None:
                    messages.error(request, "Non ci sono più numeri liberi nella fascia post asta.")
                    return redirect("post_asta", asta.pk)
                paletta = Paletta.objects.create(asta=asta, numero=numero, acquirente=cliente)
            Aggiudicazione.objects.create(lotto=lotto, paletta=paletta, prezzo_martello=prezzo, post_asta=True,
                                          sotto_riserva=bool(lotto.riserva and prezzo < lotto.riserva),
                                          registrata_da=request.user)
        if not cliente.is_cliente:
            cliente.is_cliente = True
            cliente.save()
        messages.success(request, f"Lotto {lotto.numero} venduto in post asta a {cliente.nome} "
                                  f"(paletta {paletta.numero}) per € {prezzo:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        return redirect("post_asta", asta.pk)

    oggi = timezone.localdate()
    invenduti = asta.lotti.filter(stato=Lotto.Stato.INVENDUTO).select_related("mandato__mandante").order_by("ordine")
    venduti = (Aggiudicazione.objects.filter(lotto__asta=asta, post_asta=True)
               .select_related("lotto", "paletta__acquirente").order_by("-registrata_il"))
    return render(request, "asta/post_asta.html", {
        "asta": asta, "invenduti": invenduti, "venduti": venduti, "aperto": asta.post_asta_aperto,
        "prima": oggi < asta.fine, "giorni_rimasti": (asta.fine_post_asta - oggi).days,
        "clienti": Soggetto.objects.order_by("codice"),
        "totale": sum((a.prezzo_martello for a in venduti), Decimal(0)),
    })


COLONNE_AGG = {  # in ordine di preferenza: nel faldone «Prezzo» è la riserva, quindi viene dopo «Aggiudicato»
    "lotto": ["lotto", "n lotto", "nr lotto", "numero", "n.", "n"],
    "bis": ["bis"],
    "prezzo": ["aggiudicato", "aggiudicazione", "valore agg.", "valore agg", "prezzo martello", "agg.", "prezzo"],
    "paletta": ["paletta", "pal.", "pal", "n paletta"],
    "acquirente": ["acquirente", "cliente", "nome acquirente"],
}


@login_required
@permission_required("asta.add_aggiudicazione", raise_exception=True)
def importa_aggiudicazioni(request, asta_id):
    """Aggiudicazioni da Excel (lotto, paletta, prezzo): per caricare un'asta passata o fare una prova."""
    import io

    from openpyxl import load_workbook
    asta = get_object_or_404(Asta, pk=asta_id)
    if request.method == "POST" and request.FILES.get("file"):
        try:
            wb = load_workbook(io.BytesIO(request.FILES["file"].read()), data_only=True)
        except Exception:
            messages.error(request, "Il file non è un Excel (.xlsx) leggibile.")
            return redirect("importa_aggiudicazioni", asta.pk)
        righe = list(wb.worksheets[0].iter_rows(values_only=True))
        norm = lambda c: re.sub(r"\s+", " ", str(c or "").strip().lower())  # noqa: E731
        idx = next((i for i, r in enumerate(righe[:20]) if any(norm(c) in COLONNE_AGG["prezzo"] for c in r)), None)
        if idx is None:
            messages.error(request, "Nel file non trovo la colonna del prezzo («Aggiudicato», «Valore Agg.» o «Prezzo»).")
            return redirect("importa_aggiudicazioni", asta.pk)
        head = [norm(c) for c in righe[idx]]
        pos = {k: next((head.index(n) for n in nomi if n in head), None) for k, nomi in COLONNE_AGG.items()}
        predefinita = (request.POST.get("paletta_predefinita") or "").strip()
        invenduti = request.POST.get("invenduti") == "1"
        lotti = {l.numero.lower(): l for l in asta.lotti.select_related("aggiudicazione")}
        fatti, nuove_palette, errori, n_inv = 0, set(), [], 0
        for n, r in enumerate(righe[idx + 1:], start=idx + 2):
            get = lambda k: r[pos[k]] if pos[k] is not None and pos[k] < len(r) else None  # noqa: E731
            num = (str(get("lotto") or "").strip().removesuffix(".0") + str(get("bis") or "").strip()).lower()
            if not num:
                continue
            lotto = lotti.get(num)
            if not lotto:
                errori.append(f"riga {n}: lotto {num} non c'è nell'asta")
                continue
            prezzo = get("prezzo")
            if prezzo in (None, "", 0):
                if invenduti and not hasattr(lotto, "aggiudicazione") and lotto.stato == Lotto.Stato.IN_ASTA:
                    lotto.stato = Lotto.Stato.INVENDUTO
                    lotto.save(update_fields=["stato"])
                    n_inv += 1
                continue
            pal = str(get("paletta") or "").strip().removesuffix(".0") or predefinita
            if not pal.isdigit():
                errori.append(f"riga {n}: lotto {num} senza paletta")
                continue
            if not Paletta.objects.filter(asta=asta, numero=int(pal)).exists():
                nome = str(get("acquirente") or "").strip() or f"ACQUIRENTE PALETTA {pal}"
                gia = Paletta.objects.filter(asta=asta).values_list("acquirente_id", flat=True)
                acquirente = next((x for x in Soggetto.objects.exclude(pk__in=gia) if x.nome == nome), None) \
                    if get("acquirente") else None
                if not acquirente:
                    acquirente = Soggetto.objects.create(
                        tipo="azienda", ragione_sociale=nome, is_cliente=True,
                        note=f"Scheda creata caricando le aggiudicazioni dell'asta {asta.numero or asta.nome}: da completare.")
                Paletta.objects.create(asta=asta, numero=int(pal), acquirente=acquirente)
                nuove_palette.add(int(pal))
            if hasattr(lotto, "aggiudicazione"):
                errori.append(f"lotto {num} già aggiudicato: lasciato com'era")
                continue
            esito = registra(asta, lotto, "aggiudica", pal, str(prezzo), conferma=True, utente=request.user)
            if esito["ok"]:
                fatti += 1
            else:
                errori.append(f"riga {n}: {esito['errore']}")
        messages.success(request, f"{fatti} aggiudicazioni caricate" + (f", {n_inv} lotti segnati invenduti" if n_inv else "") + ".")
        if nuove_palette:
            messages.info(request, f"Palette create con un acquirente da completare: {', '.join(map(str, sorted(nuove_palette)))}.")
        if errori:
            messages.error(request, "Non caricate: " + "; ".join(errori[:40]) + (" …" if len(errori) > 40 else ""))
        return redirect("sala", asta.pk)
    return render(request, "asta/aggiudicazioni_importa.html", {"asta": asta})
