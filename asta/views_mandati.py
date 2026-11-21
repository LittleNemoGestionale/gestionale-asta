"""Mandati e magazzino: caricare i mandati con i loro oggetti, stato del materiale,
destinazione (contovendita, prossima asta, asta futura, Catawiki, consegne), collocazione e consegne."""
import io
import re
from decimal import InvalidOperation

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .import_lotti import euro, prossimo_numero
from .import_word import Riconoscitore
from .models import Asta, Lotto, Mandato, Oggetto, Soggetto

DEST = Mandato.Destinazione
CATEGORIE = ["Fumetto", "Manifesto", "Tavola originale", "Illustrazione", "Grafica e stampe", "Libro illustrato",
             "Animazione", "Altro"]
CAMPI_RIGA = ["descrizione", "categoria", "stima_min", "stima_max", "riserva", "note"]


def numero_mandato_nuovo() -> str:
    anno = timezone.localdate().year
    n = Mandato.objects.filter(numero__startswith=f"{anno}/").count() + 1
    while Mandato.objects.filter(numero=f"{anno}/{n:03d}").exists():
        n += 1
    return f"{anno}/{n:03d}"


def categorie_usate():
    usate = set(Oggetto.objects.exclude(categoria="").values_list("categoria", flat=True).distinct())
    return CATEGORIE[:-1] + sorted(usate - set(CATEGORIE)) + ["Altro"]


def collocazioni_usate():
    return sorted(set(Oggetto.objects.exclude(collocazione="").values_list("collocazione", flat=True).distinct())
                  | {"Caveau", "Cassetto prossime aste"})


def con_lotti(qs):
    """Precarica i lotti per calcolare lo stato senza una query per oggetto."""
    qs = qs.select_related("mandato__mandante").prefetch_related(
        Prefetch("lotti", queryset=Lotto.objects.select_related("asta", "aggiudicazione").order_by("asta__data", "pk")))
    out = list(qs)
    for o in out:
        o._lotti = list(o.lotti.all())
    return out


# ------------------------------------------------------------------ righe di oggetti

def righe_da_post(post) -> tuple[list[dict], list[str]]:
    """Righe della tabella «Oggetti del mandato» + quelle incollate da Excel."""
    righe, errori = [], []
    colonne = [post.getlist(f"r_{c}") for c in CAMPI_RIGA]
    for i, valori in enumerate(zip(*colonne), start=1):
        r = dict(zip(CAMPI_RIGA, (v.strip() for v in valori)))
        if any(r.values()):
            righe.append(r)
    for n, riga in enumerate((post.get("incolla") or "").splitlines(), start=1):
        if not riga.strip():
            continue
        parti = [p.strip() for p in re.split(r"\t|;|\|", riga)]
        righe.append(dict(zip(CAMPI_RIGA, parti + [""] * len(CAMPI_RIGA))))
    puliti = []
    for n, r in enumerate(righe, start=1):
        if not r["descrizione"]:
            errori.append(f"riga {n}: manca la descrizione")
            continue
        try:
            valori = {k: euro(r[k]) for k in ("stima_min", "stima_max", "riserva")}
        except InvalidOperation:
            errori.append(f"riga {n} («{r['descrizione'][:30]}»): importo non valido")
            continue
        puliti.append({"descrizione": r["descrizione"], "categoria": r["categoria"][:80], "note": r["note"][:250],
                       **valori})
    return puliti, errori


def crea_oggetti(mandato, righe) -> int:
    for r in righe:
        Oggetto.objects.create(mandato=mandato, **r)
    return len(righe)


# ------------------------------------------------------------------ azioni su più oggetti

def azione_oggetti(request, oggetti_ammessi) -> bool:
    """Gestisce i pulsanti della barra «N selezionati». Restituisce True se ha fatto qualcosa."""
    azione = request.POST.get("azione")
    ids = [int(x) for x in request.POST.getlist("sel") if x.isdigit()]
    if not azione or azione.startswith("riga") or azione in ("salva_mandato", "aggiungi"):
        return False
    sel = con_lotti(oggetti_ammessi.filter(pk__in=ids))
    if not sel:
        messages.error(request, "Spunta almeno un oggetto.")
        return True
    oggi = timezone.localdate()
    if azione == "applica":
        cat, dest, coll = (request.POST.get(k, "").strip() for k in ("b_categoria", "b_destinazione", "b_collocazione"))
        if dest == "-":
            dest = None  # «togli la destinazione propria»: torna quella del mandato
        for o in sel:
            if cat:
                o.categoria = cat[:80]
            if dest is None:
                o.destinazione = ""
            elif dest:
                o.destinazione = dest
                if dest == DEST.CONSEGNE and not coll and not o.collocazione:
                    o.collocazione = "Caveau"
                if dest != DEST.CONSEGNE:
                    o.consegnato_il = None
            if coll:
                o.collocazione = coll[:100]
            o.save()
        messages.success(request, f"Aggiornati {len(sel)} oggetti.")
    elif azione == "assegna":
        asta = Asta.objects.filter(pk=request.POST.get("asta") or 0, chiusa=False).first()
        if not asta:
            messages.error(request, "Scegli l'asta a cui assegnarli.")
            return True
        n, saltati = prossimo_numero(asta), []
        with transaction.atomic():
            for o in sel:
                if not o.disponibile:
                    saltati.append(o.codice)
                    continue
                while asta.lotti.filter(numero=str(n)).exists():
                    n += 1
                Lotto.objects.create(asta=asta, numero=str(n), mandato=o.mandato, descrizione=o.descrizione,
                                     sezione=o.categoria[:150], stima_min=o.stima_min, stima_max=o.stima_max,
                                     riserva=o.riserva, note_interne=o.note[:250], oggetto=o)
                n += 1
        fatti = len(sel) - len(saltati)
        messages.success(request, f"{fatti} oggetti assegnati all'asta {asta.numero or asta.nome}: "
                                  "base d'asta e numeri si sistemano nella pagina Lotti.")
        if saltati:
            messages.info(request, "Non assegnati perché non disponibili (già in asta, venduti o restituiti): "
                          + ", ".join(saltati))
    elif azione == "restituisci":
        for o in sel:
            o.restituito_il = oggi
            o.save()
        messages.success(request, f"{len(sel)} oggetti segnati come restituiti al mandante.")
    elif azione == "consegnato":
        for o in sel:
            o.consegnato_il = oggi
            o.save()
        messages.success(request, f"{len(sel)} oggetti segnati come consegnati oggi.")
    elif azione == "annulla_consegna":
        for o in sel:
            o.consegnato_il = None
            o.save()
        messages.success(request, f"{len(sel)} oggetti rimessi da consegnare.")
    elif azione == "annulla_reso":
        for o in sel:
            o.restituito_il = None
            o.save()
        messages.success(request, f"{len(sel)} oggetti tornati in magazzino.")
    return True


def ctx_barra():
    return {"destinazioni": DEST.choices, "categorie": categorie_usate(), "collocazioni": collocazioni_usate(),
            "aste_aperte": Asta.objects.filter(chiusa=False).order_by("data")}


# ------------------------------------------------------------------ pagine

@login_required
@permission_required("asta.view_mandato", raise_exception=True)
def mandati(request):
    f = {k: request.GET.get(k, "") for k in ("q", "arrivo", "dest")}
    if request.method == "POST" and request.user.has_perm("asta.change_mandato"):
        m = get_object_or_404(Mandato, pk=request.POST.get("mandato"))
        campo = request.POST.get("campo")
        if campo in ("stato_arrivo", "destinazione"):
            setattr(m, campo, request.POST.get("valore", ""))
            m.save(update_fields=[campo])
        return redirect(request.get_full_path())
    qs = Mandato.objects.select_related("mandante").annotate(n_oggetti=Count("oggetti", distinct=True)) \
        .order_by("-data", "-pk")
    if f["arrivo"]:
        qs = qs.filter(stato_arrivo=f["arrivo"])
    if f["dest"]:
        qs = qs.filter(Q(destinazione=f["dest"]) | Q(oggetti__destinazione=f["dest"])).distinct()
    elenco = list(qs)
    if f["q"]:
        q = f["q"].lower()
        elenco = [m for m in elenco if q in (m.mandante.nome or "").lower() or q in m.numero.lower()
                  or q == m.mandante.codice]
    da_ritirare = Mandato.objects.filter(stato_arrivo__in=["spediti", "non_spediti", "da_ritirare"]).count()
    return render(request, "asta/mandati.html", {"mandati": elenco[:300], "f": f, "arrivi": Mandato.Arrivo.choices,
                                                 "destinazioni": DEST.choices, "da_ritirare": da_ritirare,
                                                 "totale": len(elenco)})


class MandatoForm(forms.ModelForm):
    class Meta:
        model = Mandato
        fields = ["numero", "data", "stato_arrivo", "destinazione", "commissione_pct", "spesa_inserimento",
                  "iva_su_commissione", "note_accordo"]
        labels = {"data": "data ricezione", "note_accordo": "note"}
        widgets = {"data": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                   "note_accordo": forms.Textarea(attrs={"rows": 2})}


@login_required
@permission_required("asta.add_mandato", raise_exception=True)
def mandato_nuovo(request, soggetto_id=None):
    s = get_object_or_404(Soggetto, pk=soggetto_id) if soggetto_id else None
    if not s and request.POST.get("mandante"):
        s = Soggetto.objects.filter(pk=request.POST["mandante"]).first()
    form = MandatoForm(request.POST or None, initial={
        "numero": numero_mandato_nuovo(), "data": timezone.localdate(), "commissione_pct": 15,
        "stato_arrivo": Mandato.Arrivo.IN_SEDE})
    righe, errori = ([], [])
    if request.method == "POST":
        righe, errori = righe_da_post(request.POST)
        if request.FILES.get("file"):
            try:
                r_file, e_file = leggi_excel_oggetti(request.FILES["file"].read())
                righe += [{k: r[k] for k in ("descrizione", "categoria", "note", "stima_min", "stima_max", "riserva")}
                          for r in r_file]
                errori += e_file
            except ValueError as e:
                errori.append(f"file Excel: {e}")
        if not s:
            errori.insert(0, "scegli il mandante")
        if form.is_valid() and not errori:
            with transaction.atomic():
                m = form.save(commit=False)
                m.mandante = s
                m.save()
                n = crea_oggetti(m, righe)
                if not s.is_mandante:
                    s.is_mandante = True
                    s.save()
            messages.success(request, f"Mandato {m.numero} registrato per {s.nome}"
                                      + (f" con {n} oggetti." if n else "."))
            return redirect("mandato", m.pk)
    mandanti_elenco = sorted(Soggetto.objects.filter(is_mandante=True), key=lambda x: (x.nome or "").lower()) \
        if not soggetto_id else []
    return render(request, "asta/mandato_nuovo.html", {
        "s": s, "form": form, "errori": errori, "mandanti": mandanti_elenco, "categorie": categorie_usate(),
        "righe_vuote": range(5), "incolla": request.POST.get("incolla", "")})


@login_required
@permission_required("asta.view_mandato", raise_exception=True)
def mandato(request, mandato_id):
    m = get_object_or_404(Mandato.objects.select_related("mandante"), pk=mandato_id)
    form = MandatoForm(instance=m)
    if request.method == "POST":
        if not request.user.has_perm("asta.change_mandato"):
            messages.error(request, "Non hai il permesso di modificare i mandati.")
            return redirect("mandato", m.pk)
        azione = request.POST.get("azione")
        if azione == "salva_mandato":
            form = MandatoForm(request.POST, instance=m)
            if form.is_valid():
                form.save()
                messages.success(request, "Mandato aggiornato.")
                return redirect("mandato", m.pk)
        elif azione == "aggiungi":
            righe, errori = righe_da_post(request.POST)
            if request.FILES.get("file"):
                try:
                    r_file, e_file = leggi_excel_oggetti(request.FILES["file"].read())
                    righe += [{k: r[k] for k in ("descrizione", "categoria", "note", "stima_min", "stima_max",
                                                 "riserva")} for r in r_file]
                    errori += e_file
                except ValueError as e:
                    errori.append(str(e))
            n = crea_oggetti(m, righe)
            if n:
                messages.success(request, f"Aggiunti {n} oggetti.")
            if errori:
                messages.error(request, "Non aggiunte: " + "; ".join(errori))
            return redirect("mandato", m.pk)
        elif azione_oggetti(request, m.oggetti.all()):
            return redirect(f"{request.path}#oggetti")
    oggetti = con_lotti(m.oggetti.all())
    return render(request, "asta/mandato.html", {"m": m, "form": form, "oggetti": oggetti,
                                                 "righe_vuote": range(3), **ctx_barra()})


@login_required
@permission_required("asta.view_mandato", raise_exception=True)
def magazzino(request):
    """Tutti gli oggetti dei mandati, con filtri: per preparare le aste, Catawiki, contovendita, consegne."""
    f = {k: request.GET.get(k, "") for k in ("q", "stato", "dest", "cat", "coll")}
    if not request.GET:
        f["stato"] = "disponibili"
    if request.method == "POST":
        if not request.user.has_perm("asta.change_mandato"):
            messages.error(request, "Non hai il permesso di modificare gli oggetti.")
        else:
            azione_oggetti(request, Oggetto.objects.all())
        return redirect(request.get_full_path())
    qs = Oggetto.objects.all()
    if f["cat"]:
        qs = qs.filter(categoria=f["cat"])
    if f["coll"]:
        qs = qs.filter(collocazione=f["coll"])
    if f["dest"]:
        qs = qs.filter(Q(destinazione=f["dest"]) | Q(destinazione="", mandato__destinazione=f["dest"]))
    if f["q"]:
        qs = qs.filter(Q(descrizione__icontains=f["q"]) | Q(note__icontains=f["q"]) | Q(mandato__numero=f["q"]))
    oggetti = con_lotti(qs.order_by("-mandato__data", "mandato_id", "progressivo"))
    if f["stato"] == "disponibili":
        oggetti = [o for o in oggetti if o.disponibile]
    elif f["stato"]:
        oggetti = [o for o in oggetti if o.stato == f["stato"]]
    stima = sum((o.stima_min or 0) for o in oggetti)
    return render(request, "asta/magazzino.html", {
        "oggetti": oggetti[:500], "totale": len(oggetti), "stima": stima, "f": f,
        "stati": Oggetto.Stato.choices, **ctx_barra()})


@login_required
@permission_required("asta.view_mandato", raise_exception=True)
def consegne(request):
    """Oggetti con destinazione «Consegne»: lista di lavoro e consegnati."""
    if request.method == "POST":
        if request.user.has_perm("asta.change_mandato"):
            azione_oggetti(request, Oggetto.objects.all())
        return redirect(request.get_full_path())
    coll = request.GET.get("coll", "")
    qs = Oggetto.objects.filter(Q(destinazione=DEST.CONSEGNE) | Q(destinazione="", mandato__destinazione=DEST.CONSEGNE))
    tutti = con_lotti(qs.order_by("collocazione", "mandato_id", "progressivo"))
    da_fare = [o for o in tutti if not o.consegnato_il and (not coll or o.collocazione == coll)]
    fatti = sorted([o for o in tutti if o.consegnato_il], key=lambda o: o.consegnato_il, reverse=True)[:100]
    per_coll = {}
    for o in tutti:
        if not o.consegnato_il:
            per_coll[o.collocazione or "(senza collocazione)"] = per_coll.get(o.collocazione or "(senza collocazione)", 0) + 1
    return render(request, "asta/consegne.html", {"da_fare": da_fare, "fatti": fatti, "per_coll": per_coll,
                                                  "coll": coll, **ctx_barra()})


# ------------------------------------------------------------------ Excel

COLONNE_EXCEL = {
    "codice_mandante": ["codice mandante", "cod mandante", "cod. mandante", "codice"],
    "mandante": ["mandante", "nome mandante", "mandatario"],
    "numero": ["n mandato", "n. mandato", "numero mandato", "mandato", "mandato n", "n° mandato"],
    "data": ["data", "data ricezione", "ricevuto il"],
    "descrizione": ["descrizione", "descrizione oggetto", "oggetto", "opera", "titolo", "articolo"],
    "categoria": ["categoria", "tipologia", "genere"],
    "stima_min": ["stima min", "stima minima", "stima", "valutazione"],
    "stima_max": ["stima max", "stima massima"],
    "riserva": ["riserva", "prezzo minimo"],
    "note": ["note", "note oggetto"],
}


def leggi_excel_oggetti(contenuto: bytes) -> tuple[list[dict], list[str]]:
    from openpyxl import load_workbook
    try:
        wb = load_workbook(io.BytesIO(contenuto), data_only=True)
    except Exception:
        raise ValueError("il file non è un Excel (.xlsx) leggibile")
    ws = wb.worksheets[0]
    righe = list(ws.iter_rows(values_only=True))
    norm = lambda c: re.sub(r"\s+", " ", str(c or "").strip().lower())  # noqa: E731
    idx = next((i for i, r in enumerate(righe[:20]) if any(norm(c) in COLONNE_EXCEL["descrizione"] for c in r)), None)
    if idx is None:
        raise ValueError("nel file non trovo la colonna «Descrizione»")
    head = [norm(c) for c in righe[idx]]
    pos = {}
    for campo, nomi in COLONNE_EXCEL.items():
        for i, h in enumerate(head):
            if h in nomi and i not in pos.values():
                pos[campo] = i
                break
    out, errori = [], []
    for n, r in enumerate(righe[idx + 1:], start=idx + 2):
        get = lambda k: r[pos[k]] if k in pos and pos[k] < len(r) and r[pos[k]] is not None else ""  # noqa: E731
        if not str(get("descrizione")).strip():
            continue
        riga = {"riga": n, "descrizione": str(get("descrizione")).strip(), "categoria": str(get("categoria")).strip()[:80],
                "note": str(get("note")).strip()[:250], "codice_mandante": str(get("codice_mandante")).strip().removesuffix(".0"),
                "mandante": str(get("mandante")).strip(), "numero": str(get("numero")).strip().removesuffix(".0"),
                "data": get("data")}
        try:
            for k in ("stima_min", "stima_max", "riserva"):
                riga[k] = euro(get(k))
        except InvalidOperation:
            errori.append(f"riga {n}: importo non valido")
            continue
        out.append(riga)
    return out, errori


@login_required
@permission_required("asta.add_mandato", raise_exception=True)
def importa_mandati(request):
    """Excel con una riga per oggetto e le colonne Mandante (o Codice mandante), N° mandato, Descrizione…"""
    if request.method == "POST" and request.FILES.get("file"):
        try:
            righe, errori = leggi_excel_oggetti(request.FILES["file"].read())
        except ValueError as e:
            messages.error(request, f"Non riesco a leggere il file: {e}")
            return redirect("importa_mandati")
        ric = Riconoscitore()
        gruppi, nuovi_mandanti, senza_mandante = {}, [], 0
        with transaction.atomic():
            for r in righe:
                s = None
                if r["codice_mandante"]:
                    s = Soggetto.objects.filter(codice=r["codice_mandante"]).first()
                testo = r["mandante"] or ("" if s else r["codice_mandante"])
                if not s and testo:
                    s = ric.trova(testo)
                if not s:
                    testo = testo or "MANDANTE DA ABBINARE"
                    s = next((x for x in Soggetto.objects.filter(is_mandante=True) if x.nome == testo), None)
                    if not s:
                        s = Soggetto.objects.create(tipo="azienda", ragione_sociale=testo, is_mandante=True,
                                                    note="Scheda creata dall'import dei mandati: completare i dati.")
                        nuovi_mandanti.append(testo)
                        ric = Riconoscitore()
                    senza_mandante += not r["mandante"] and not r["codice_mandante"]
                chiave = (s.pk, r["numero"])
                if chiave not in gruppi:
                    m = Mandato.objects.filter(numero=r["numero"]).first() if r["numero"] else None
                    if m and m.mandante_id != s.pk:
                        errori.append(f"riga {r['riga']}: il mandato {r['numero']} è di un altro mandante")
                        continue
                    if not m:
                        data = r["data"] if hasattr(r["data"], "year") else timezone.localdate()
                        m = Mandato.objects.create(
                            mandante=s, numero=r["numero"] or numero_mandato_nuovo(), data=data,
                            commissione_pct=15, stato_arrivo=Mandato.Arrivo.IN_SEDE,
                            note_accordo="DA VERIFICARE: percentuale messa in automatico dall'import.")
                    gruppi[chiave] = m
                Oggetto.objects.create(mandato=gruppi[chiave], descrizione=r["descrizione"], categoria=r["categoria"],
                                       note=r["note"], stima_min=r["stima_min"], stima_max=r["stima_max"],
                                       riserva=r["riserva"])
        n_ogg = sum(1 for _ in righe) - len([e for e in errori if "altro mandante" in e])
        messages.success(request, f"Caricati {n_ogg} oggetti in {len(gruppi)} mandati.")
        if nuovi_mandanti:
            messages.info(request, "Schede mandante create (da completare, percentuale da verificare): "
                          + ", ".join(sorted(set(nuovi_mandanti))))
        if senza_mandante:
            messages.info(request, f"{senza_mandante} righe senza mandante: sono nel mandato di «MANDANTE DA ABBINARE».")
        if errori:
            messages.error(request, "Righe non caricate: " + "; ".join(errori))
        return redirect("mandati")
    return render(request, "asta/mandati_importa.html", {})
