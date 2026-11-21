"""Gestione delle aste: elenco (in corso / prossime / passate), creazione, caricamento lotti."""
import io
import json
import re
from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import Oggetto, Asta, Lotto, Mandato

STATI_RIPROPONIBILI = [Lotto.Stato.INVENDUTO, Lotto.Stato.CONTOVENDITA]


# ------------------------------------------------------------------ elenco aste

@login_required
def elenco_aste(request):
    oggi = timezone.localdate()
    aste = (Asta.objects.annotate(
        n_lotti=Count("lotti", distinct=True),
        n_aggiudicati=Count("lotti", filter=Q(lotti__stato=Lotto.Stato.AGGIUDICATO), distinct=True),
        totale=Sum("lotti__aggiudicazione__prezzo_martello"))
        .order_by("-data"))
    in_corso = [a for a in aste if not a.chiusa and a.data <= oggi]
    prossime = sorted([a for a in aste if not a.chiusa and a.data > oggi], key=lambda a: a.data)
    passate = [a for a in aste if a.chiusa]
    return render(request, "asta/aste.html", {"in_corso": in_corso, "prossime": prossime, "passate": passate})


# ------------------------------------------------------------------ crea / modifica

class AstaForm(forms.ModelForm):
    class Meta:
        model = Asta
        fields = ["numero", "nome", "data", "data_fine", "numero_tornate",
                  "giorni_post_asta", "luogo", "diritti_pct", "diritti_iva_inclusa", "iva_pct", "note"]
        widgets = {"data": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                   "data_fine": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                   "note": forms.Textarea(attrs={"rows": 3})}

    def clean(self):
        d = super().clean()
        if d.get("data_fine") and d.get("data") and d["data_fine"] < d["data"]:
            self.add_error("data_fine", "La fine non può essere prima dell'inizio.")
        if d.get("numero_tornate") is not None and d["numero_tornate"] < 1:
            self.add_error("numero_tornate", "Almeno una tornata.")
        return d


@login_required
@permission_required("asta.add_asta", raise_exception=True)
def asta_form(request, asta_id=None):
    asta = get_object_or_404(Asta, pk=asta_id) if asta_id else None
    if request.method == "POST":
        form = AstaForm(request.POST, instance=asta)
        if form.is_valid():
            nuova = form.save()
            from .models import Tornata
            from datetime import date as _d, time as _t
            for n in range(1, nuova.numero_tornate + 1):
                d, o = request.POST.get(f"tornata_{n}_data"), request.POST.get(f"tornata_{n}_ora")
                Tornata.objects.update_or_create(asta=nuova, numero=n, defaults={
                    "data": _d.fromisoformat(d) if d else (nuova.data if n == 1 else None),
                    "ora": _t.fromisoformat(o) if o else None})
            Tornata.objects.filter(asta=nuova, numero__gt=nuova.numero_tornate).delete()
            # fasce delle palette
            from .models import FasciaPalette
            if any(request.POST.get(f"fascia_{i}_da") for i in range(12)):
                nuova.fasce.all().delete()
                for i in range(12):
                    tipo, da, a = (request.POST.get(f"fascia_{i}_{k}") for k in ("tipo", "da", "a"))
                    if tipo and da and a and da.isdigit() and a.isdigit():
                        pct = (request.POST.get(f"fascia_{i}_pct") or "").replace(",", ".")
                        FasciaPalette.objects.create(asta=nuova, tipo=tipo, da=int(da), a=int(a),
                                                     diritti_pct=pct or None)
            else:
                nuova.crea_fasce(Asta.objects.exclude(pk=nuova.pk).order_by("-data").first())
            messages.success(request, f"Asta {nuova.numero or ''} salvata." if asta else
                             f"Asta {nuova.numero or ''} aperta. Ora carica i lotti.")
            return redirect("lotti_asta", nuova.pk)
    elif asta:
        form = AstaForm(instance=asta)
    else:
        # nuova asta: parte dalle impostazioni dell'ultima
        ultima = Asta.objects.order_by("-data").first()
        iniziali = {"numero": Asta.prossimo_numero()}
        if ultima:
            iniziali.update({k: getattr(ultima, k) for k in
                             ["diritti_pct", "diritti_online_pct", "palette_online_da", "diritti_iva_inclusa",
                              "iva_pct", "luogo"]})
        form = AstaForm(initial=iniziali)
    from .models import Tornata
    esistenti = {t.numero: t for t in Tornata.objects.filter(asta=asta)} if asta else {}
    righe_tornate = [(n, esistenti.get(n)) for n in range(1, 11)]
    from .models import FasciaPalette
    if asta and asta.fasce.exists():
        fasce = [(f.tipo, f.da, f.a, f.diritti_pct) for f in asta.fasce.all()]
    else:
        modello = Asta.objects.exclude(pk=getattr(asta, "pk", None)).order_by("-data").first()
        fasce = ([(f.tipo, f.da, f.a, f.diritti_pct) for f in modello.fasce.all()] if modello and modello.fasce.exists()
                 else FasciaPalette.STANDARD)
    righe_fasce = list(enumerate(fasce + [("", "", "", None)] * 2))
    return render(request, "asta/asta_form.html", {"form": form, "asta": asta, "modifica": bool(asta),
                                                   "righe_tornate": righe_tornate, "righe_fasce": righe_fasce,
                                                   "tipi_fascia": FasciaPalette.Tipo.choices})


@login_required
@permission_required("asta.change_asta", raise_exception=True)
def asta_conclusa(request, asta_id):
    asta = get_object_or_404(Asta, pk=asta_id)
    if request.method == "POST":
        asta.chiusa = request.POST.get("chiusa") == "1"
        asta.save(update_fields=["chiusa"])
        messages.success(request, "Asta segnata come conclusa: resta consultabile tra le aste passate."
                         if asta.chiusa else "Asta riaperta.")
    return redirect("asta_cruscotto", asta.pk)


# ------------------------------------------------------------------ lotti

def trova_mandato(testo: str):
    """Accetta il numero del mandato (2026/103) o il codice del mandante (M003)."""
    t = (testo or "").strip()
    if not t:
        return None, "manca il mandato"
    t = t.split("·")[0].strip()
    m = Mandato.objects.filter(numero__iexact=t).select_related("mandante").first()
    if m:
        return m, None
    mandati = list(Mandato.objects.filter(mandante__codice__iexact=t).order_by("-data"))
    if not mandati:
        return None, f"mandato o mandante «{testo}» non trovato"
    return mandati[0], None  # il mandato più recente del mandante


def _euro(v):
    if v in (None, ""):
        return None
    if isinstance(v, (int, float, Decimal)):
        return Decimal(str(v))
    s = str(v).replace("€", "").replace(" ", "")
    if not s:
        return None
    if "," in s or re.fullmatch(r"\d{1,3}(\.\d{3})+", s):  # formato italiano: 1.500 / 1.500,50
        s = s.replace(".", "").replace(",", ".")
    return Decimal(s)


def prossimo_numero_lotto(asta) -> int:
    ultimo = asta.lotti.order_by("-ordine").values_list("ordine", flat=True).first()
    return (ultimo or 0) + 1


class LottoForm(forms.Form):
    numero = forms.CharField(max_length=10)
    tornata = forms.IntegerField(required=False, min_value=1)
    mandato = forms.CharField(help_text="numero mandato o codice mandante")
    descrizione = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))
    stima_min = forms.CharField(required=False)
    stima_max = forms.CharField(required=False)
    base_asta = forms.CharField(required=False)
    riserva = forms.CharField(required=False)
    commissione_pct = forms.CharField(required=False)


@login_required
@permission_required("asta.view_lotto", raise_exception=True)
def lotti_asta(request, asta_id):
    asta = get_object_or_404(Asta, pk=asta_id)
    puo = request.user.has_perm("asta.add_lotto")
    ultima_tornata = asta.lotti.exclude(tornata=None).order_by("-ordine").values_list("tornata", flat=True).first()
    form = LottoForm(initial={"numero": prossimo_numero_lotto(asta), "tornata": ultima_tornata or 1})
    if request.method == "POST" and puo:
        if request.POST.get("azione") == "cambia_mandato":
            nuovo = Mandato.objects.select_related("mandante").filter(pk=request.POST.get("nuovo_mandato") or 0).first()
            sel = asta.lotti.filter(pk__in=request.POST.getlist("sel"))
            if asta.chiusa:
                messages.error(request, "L'asta è conclusa: i mandanti dei lotti non si cambiano più.")
            elif not nuovo or not sel.exists():
                messages.error(request, "Spunta almeno un lotto e scegli il mandante.")
            else:
                n = 0
                for l in sel:
                    l.mandato = nuovo
                    l.note_interne = re.sub(r"(DA VERIFICARE: )?mandante (mancante|da abbinare)\.?\s*", "", l.note_interne).strip()
                    if l.note_interne in ("DA VERIFICARE:", "DA VERIFICARE: ."):
                        l.note_interne = ""
                    l.save()
                    n += 1
                messages.success(request, f"{n} lotti assegnati a {nuovo.mandante.nome} (mandato {nuovo.numero}).")
            return redirect("lotti_asta", asta.pk)
        if request.POST.get("azione") == "elimina":
            l = get_object_or_404(Lotto, pk=request.POST.get("lotto"), asta=asta)
            if hasattr(l, "aggiudicazione"):
                messages.error(request, f"Il lotto {l.numero} è aggiudicato e non si può eliminare.")
            else:
                if l.lotto_origine_id:  # torna disponibile nell'asta di provenienza
                    Lotto.objects.filter(pk=l.lotto_origine_id).update(stato=Lotto.Stato.INVENDUTO)
                l.delete()
                messages.success(request, f"Lotto {l.numero} eliminato.")
            return redirect("lotti_asta", asta.pk)
        form = LottoForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            mandato, err = trova_mandato(d["mandato"])
            try:
                valori = {k: _euro(d[k]) for k in ["stima_min", "stima_max", "base_asta", "riserva",
                                                   "commissione_pct"]}
            except InvalidOperation:
                err = "importo non valido"
            if not err and asta.lotti.filter(numero=d["numero"]).exists():
                err = f"il lotto {d['numero']} esiste già in questa asta"
            if err:
                form.add_error(None, err)
            else:
                Lotto.objects.create(asta=asta, numero=d["numero"], mandato=mandato, descrizione=d["descrizione"],
                                     tornata=d.get("tornata"), **valori)
                messages.success(request, f"Lotto {d['numero']} aggiunto ({mandato.mandante.codice}).")
                return redirect("lotti_asta", asta.pk)
    lotti = asta.lotti.select_related("mandato__mandante", "lotto_origine__asta").order_by("ordine", "numero")
    n_foto = lotti.filter(da_fotografare=True).count()
    n_verifica = lotti.filter(note_interne__startswith="DA VERIFICARE").count()
    if request.GET.get("foto"):
        lotti = lotti.filter(da_fotografare=True)
    if request.GET.get("verifica"):
        lotti = lotti.filter(note_interne__startswith="DA VERIFICARE")
    mandati = Mandato.objects.select_related("mandante").order_by("mandante__codice", "-data")
    from .views_mandati import con_lotti
    mandati_asta = list(asta.mandati_ricevuti.select_related("mandante").order_by("numero"))
    presenti = {}
    for l in lotti:
        presenti.setdefault(l.mandato_id, [l.mandato, 0])[1] += 1
    mandati_presenti = []
    for m, n in presenti.values():
        m.n = n
        mandati_presenti.append(m)
    mandati_presenti.sort(key=lambda m: (m.mandante.nome or "").lower())
    oggetti = con_lotti(Oggetto.objects.filter(mandato__in=mandati_asta))
    for m in mandati_asta:
        propri = [o for o in oggetti if o.mandato_id == m.pk]
        m.n_ogg = len(propri)
        m.n_in_asta = sum(1 for o in propri if any(l.asta_id == asta.pk for l in o._lotti))
        m.n_disp = sum(1 for o in propri if o.disponibile)
    da_assegnare = [o.pk for o in oggetti if o.disponibile]
    return render(request, "asta/lotti.html", {"asta": asta, "lotti": lotti, "form": form, "puo": puo,
                                               "mandati_asta": mandati_asta, "da_assegnare": da_assegnare,
                                               "mandati_presenti": mandati_presenti,
                                               "mandati": mandati, "n_foto": n_foto, "n_verifica": n_verifica,
                                               "solo_foto": bool(request.GET.get("foto")),
                                               "solo_verifica": bool(request.GET.get("verifica"))})


# ------------------------------------------------------------------ import da file (Word / Excel)

@login_required
@permission_required("asta.add_lotto", raise_exception=True)
def importa_lotti(request, asta_id):
    """Carica il catalogo da Word o Excel: anteprima, abbinamento mandanti, tornate, conferma."""
    from . import import_lotti as imp
    asta = get_object_or_404(Asta, pk=asta_id)
    chiave = f"import_lotti_{asta.pk}"
    dati = json.loads(request.session.get(chiave, "null") or "null")
    if request.method == "POST" and request.FILES.get("file"):
        f = request.FILES["file"]
        try:
            tipo, righe = imp.leggi_file(f.name, f.read(), asta)
            dati = {"tipo": tipo, "file": f.name, "righe": imp.controlla(righe, asta), "abbinamenti": {},
                    "tornate": {}}
            request.session[chiave] = json.dumps(dati)
        except Exception as e:  # file illeggibile o formato non previsto
            messages.error(request, f"Non riesco a leggere il file: {e}")
            dati = None
    elif request.method == "POST" and dati and request.POST.get("azione") in ("aggiorna", "conferma"):
        abbinamenti = {}
        for i, testo in enumerate(request.POST.getlist("testo_mandante")):
            v = request.POST.get(f"abbina_{i}")
            abbinamenti[testo] = int(v) if v else None
        tornate = {}
        for t in range(2, asta.numero_tornate + 1):
            v = request.POST.get(f"tornata_{t}")
            tornate[t] = int(v) if v and v.isdigit() else None
        scelte = {int(k): v for k, v in dati.get("oggetti", {}).items()}
        for i in range(len(dati["righe"])):
            v = request.POST.get(f"oggetto_{i}")
            if v is not None and v != str(dati["righe"][i].get("oggetto_id") or ""):  # cambiato a mano
                scelte[i] = v
        dati["abbinamenti"], dati["tornate"] = abbinamenti, {str(k): v for k, v in tornate.items()}
        dati["oggetti"] = {str(k): v for k, v in scelte.items()}
        dati["righe"] = imp.controlla(dati["righe"], asta, abbinamenti, {1: 1, **tornate} if any(tornate.values()) else None,
                                      scelte)
        request.session[chiave] = json.dumps(dati)
        if request.POST.get("azione") == "conferma":
            with transaction.atomic():
                esito = imp.salva(dati["righe"], asta)
            request.session.pop(chiave, None)
            messages.success(request, f"{esito['caricati']} lotti caricati"
                             + (f", {esito['collegati']} collegati agli oggetti dei mandati." if esito["collegati"] else "."))
            if esito["mandanti_creati"]:
                elenco = ", ".join(f"{k} ({v} lotti)" for k, v in sorted(esito["mandanti_creati"].items()))
                messages.info(request, "Mandanti non trovati in anagrafica: ho creato le schede da completare "
                                       f"(dati e percentuale del mandato): {elenco}.")
            if esito["da_verificare"]:
                messages.info(request, f"{len(esito['da_verificare'])} lotti da verificare (mandante, prezzo…): "
                                       "li trovi con il filtro «Da verificare».")
            if esito["non_caricati"]:
                messages.error(request, "Non caricati (aggiungili a mano): " + " · ".join(esito["non_caricati"]))
            return redirect(f"{reverse('lotti_asta', args=[asta.pk])}?verifica=1"
                            if esito["da_verificare"] else reverse("lotti_asta", args=[asta.pk]))
    elif request.method == "GET" and request.GET.get("nuovo"):
        request.session.pop(chiave, None)
        dati = None

    ctx = {"asta": asta, "dati": dati}
    if dati:
        righe = dati["righe"]
        da_abbinare = sorted({r["mandante_testo"] for r in righe
                              if not r["escluso"] and (not r["mandato_id"] or r["mandante_testo"] in dati["abbinamenti"])})
        ctx.update({
            "righe": righe,
            "n_ok": sum(1 for r in righe if not r["escluso"] and not r["errori"]),
            "n_err": sum(1 for r in righe if not r["escluso"] and imp.bloccante(r)),
            "n_avvisi": sum(1 for r in righe if not r["escluso"] and r["errori"] and not imp.bloccante(r)),
            "n_esclusi": sum(1 for r in righe if r["escluso"]),
            "n_foto": sum(1 for r in righe if r["da_fotografare"] and not r["escluso"]),
            "n_collegati": sum(1 for r in righe if r.get("oggetto_id") and not r["escluso"]),
            "n_dubbi": sum(1 for r in righe if r.get("oggetto_dubbio") and not r.get("oggetto_id") and not r["escluso"]),
            "da_abbinare": [(i, t, dati["abbinamenti"].get(t), sum(1 for r in righe if r["mandante_testo"] == t and not r["escluso"]))
                            for i, t in enumerate(da_abbinare)],
            "mandati": Mandato.objects.select_related("mandante").order_by("mandante__codice", "-data"),
            "tornate": [(t, dati["tornate"].get(str(t))) for t in range(2, asta.numero_tornate + 1)],
        })
    return render(request, "asta/lotti_importa.html", ctx)


@login_required
def modello_lotti(request):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "Lotti"
    ws.append(["Lotto", "Tornata", "Mandato", "Descrizione", "Stima min", "Stima max", "Base", "Riserva",
               "Commissione %"])
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="7A1F2B")
    ws.append([1, 1, "1", "Tavola originale a china, 1972, 35x50 cm", 800, 1200, 600, 700, None])
    ws.append([2, 1, "2026/003", "Acquerello su carta, firmato", 300, 500, 250, None, 12])
    ws.append([None, 2, "1", "Lotto senza numero: prende il primo numero libero", None, None, 100, None, None])
    for col, w in zip("ABCDEFGHI", [8, 9, 14, 60, 11, 11, 10, 10, 14]):
        ws.column_dimensions[col].width = w
    note = wb.create_sheet("Istruzioni")
    for riga in ["Una riga per lotto. Solo «Mandato» e «Descrizione» sono obbligatori.",
                 "Mandato: il numero del mandato (es. 2026/003) oppure il codice del mandante (es. 1);",
                 "  con il codice del mandante si usa il suo mandato più recente.",
                 "Lotto vuoto: il programma assegna il primo numero libero.",
                 "Commissione % vuota: vale quella del mandato.",
                 "Prima di caricare il programma mostra un'anteprima con gli eventuali errori."]:
        note.append([riga])
    note.column_dimensions["A"].width = 100
    buf = io.BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(),
                        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = 'attachment; filename="modello_caricamento_lotti.xlsx"'
    return resp


# ------------------------------------------------------------------ riproporre invenduti

@login_required
@permission_required("asta.add_lotto", raise_exception=True)
def riproponi_invenduti(request, asta_id):
    asta = get_object_or_404(Asta, pk=asta_id)
    candidati = (Lotto.objects.filter(stato__in=STATI_RIPROPONIBILI).exclude(asta=asta)
                 .select_related("asta", "mandato__mandante").order_by("-asta__data", "ordine"))
    if request.method == "POST":
        scelti = list(candidati.filter(pk__in=request.POST.getlist("lotti")))
        numero = prossimo_numero_lotto(asta)
        with transaction.atomic():
            for l in scelti:
                Lotto.objects.create(
                    asta=asta, numero=str(numero), mandato=l.mandato, descrizione=l.descrizione, tornata=1,
                    stima_min=l.stima_min, stima_max=l.stima_max, riserva=l.riserva,
                    base_asta=_euro(request.POST.get(f"base_{l.pk}")) if request.POST.get(f"base_{l.pk}") else l.base_asta,
                    commissione_pct=l.commissione_pct, lotto_origine=l, oggetto=l.oggetto)
                l.stato = Lotto.Stato.RIPROPOSTO
                l.save(update_fields=["stato"])
                numero += 1
        messages.success(request, f"{len(scelti)} lotti riproposti nell'asta {asta.numero or asta.nome}.")
        return redirect("lotti_asta", asta.pk)
    return render(request, "asta/riproponi.html", {"asta": asta, "candidati": candidati})


@login_required
@permission_required("asta.view_lotto", raise_exception=True)
def scarica_faldone(request, asta_id):
    from .faldone import crea_faldone
    asta = get_object_or_404(Asta, pk=asta_id)
    resp = HttpResponse(crea_faldone(asta),
                        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="FALDONE_AT{asta.numero or asta.pk}.xlsx"'
    return resp
