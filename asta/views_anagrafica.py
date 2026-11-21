"""Pagina iniziale, schede anagrafiche (clienti e mandanti) e assegnazione palette."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .crypto import blind_index
from .models import Aggiudicazione, Asta, Indirizzo, Interesse, Lotto, Paletta, Soggetto

pii = permission_required("asta.view_dati_personali", raise_exception=True)


# ------------------------------------------------------------------ pagina iniziale

@login_required
def home(request):
    oggi = timezone.localdate()
    aperte = Asta.objects.filter(chiusa=False).order_by("data")
    return render(request, "asta/home.html", {
        "in_corso": [a for a in aperte if a.data <= oggi],
        "prossime": [a for a in aperte if a.data > oggi],
        "n_clienti": Soggetto.objects.filter(is_cliente=True).count(),
        "n_mandanti": Soggetto.objects.filter(is_mandante=True).count(),
    })


# ------------------------------------------------------------------ anagrafica

@login_required
@pii
def anagrafica(request):
    q = request.GET.get("q", "").strip()
    ruolo = request.GET.get("ruolo", "")
    interesse = request.GET.get("interesse", "")
    qs = Soggetto.objects.prefetch_related("interessi", "indirizzi").order_by("-modificato")
    if ruolo == "clienti":
        qs = qs.filter(is_cliente=True)
    elif ruolo == "mandanti":
        qs = qs.filter(is_mandante=True)
    if interesse:
        qs = qs.filter(interessi__pk=interesse)
    schede = list(qs)
    if q:
        # i nomi sono cifrati nel database: il filtro avviene qui, dopo la decifratura
        ql = q.lower()
        h = blind_index(q)
        schede = [s for s in schede if ql in (s.nome or "").lower() or ql == s.codice.lower()
                  or s.cf_index == h or s.email_index == h
                  or any(ql in (i.citta or "").lower() for i in s.indirizzi.all())]
    return render(request, "asta/anagrafica.html", {
        "schede": schede[:300], "totale": len(schede), "q": q, "ruolo": ruolo, "interesse": interesse,
        "interessi": Interesse.objects.all()})


class SchedaForm(forms.ModelForm):
    nuovo_interesse = forms.CharField(required=False, label="Aggiungi un interesse",
                                      help_text="se manca nella lista (es. «Figurine»)")

    class Meta:
        model = Soggetto
        fields = ["tipo", "nome_proprio", "cognome", "ragione_sociale", "codice_fiscale", "is_cliente",
                  "is_mandante", "email", "telefono", "iban", "interessi", "documento", "note"]
        widgets = {"interessi": forms.CheckboxSelectMultiple, "note": forms.Textarea(attrs={"rows": 3}),
                   "tipo": forms.RadioSelect}
        labels = {"codice_fiscale": "Codice fiscale / P.IVA", "is_cliente": "Cliente (compra in asta)",
                  "is_mandante": "Mandante (affida lotti)", "iban": "IBAN (per ricevere i pagamenti)"}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for nome in ["nome_proprio", "cognome", "ragione_sociale", "codice_fiscale", "email", "telefono",
                     "iban", "documento"]:
            self.fields[nome].widget = forms.TextInput()
        self.fields["email"].widget.input_type = "email"
        self.fields["telefono"].widget.input_type = "tel"

    def clean(self):
        d = super().clean()
        if d.get("tipo") == "azienda" and not d.get("ragione_sociale"):
            self.add_error("ragione_sociale", "Indica la ragione sociale.")
        if d.get("tipo") == "privato" and not d.get("cognome"):
            self.add_error("cognome", "Indica il cognome.")
        if not d.get("is_cliente") and not d.get("is_mandante"):
            self.add_error(None, "Indica se è cliente, mandante o entrambi.")
        if d.get("iban"):
            d["iban"] = d["iban"].replace(" ", "").upper()
        return d


class IndirizzoForm(forms.Form):
    via = forms.CharField(label="Via e numero civico", required=False)
    cap = forms.CharField(label="CAP", required=False, max_length=10)
    citta = forms.CharField(label="Città", required=False)
    provincia = forms.CharField(label="Provincia", required=False, max_length=4)
    nazione = forms.CharField(label="Nazione", required=False, initial="Italia")
    destinatario = forms.CharField(label="Destinatario (se diverso)", required=False)
    telefono_consegna = forms.CharField(label="Telefono per il corriere", required=False)
    note_consegna = forms.CharField(label="Note per la consegna", required=False)

    def compilato(self):
        d = self.cleaned_data
        return bool(d.get("via") and d.get("citta"))


def _da_indirizzo(ind):
    if not ind:
        return {"nazione": "Italia"}
    return {k: getattr(ind, k) for k in ["via", "cap", "citta", "provincia", "nazione", "destinatario",
                                         "telefono_consegna", "note_consegna"]}


def _salva_indirizzo(soggetto, tipo, dati):
    ind = soggetto.indirizzi.filter(tipo=tipo).first() or Indirizzo(soggetto=soggetto, tipo=tipo, etichetta=tipo)
    for k, v in dati.items():
        setattr(ind, k, v or "")
    ind.nazione = ind.nazione or "Italia"
    ind.save()


@login_required
@pii
def scheda(request, soggetto_id=None):
    s = get_object_or_404(Soggetto, pk=soggetto_id) if soggetto_id else None
    puo = request.user.has_perm("asta.change_soggetto" if s else "asta.add_soggetto")
    ritorno = request.GET.get("ritorno") or request.POST.get("ritorno") or ""
    spedizione_esistente = s.indirizzi.filter(tipo="spedizione").first() if s else None
    if request.method == "POST" and puo:
        form = SchedaForm(request.POST, instance=s)
        dom = IndirizzoForm(request.POST, prefix="dom")
        sped = IndirizzoForm(request.POST, prefix="sped")
        diversa = request.POST.get("spedizione_diversa") == "1"
        if form.is_valid() and dom.is_valid() and sped.is_valid():
            if diversa and not sped.compilato():
                sped.add_error("via", "Compila l'indirizzo di spedizione o togli la spunta.")
            else:
                with transaction.atomic():
                    nuovo = form.save()
                    if form.cleaned_data["nuovo_interesse"]:
                        i, _ = Interesse.objects.get_or_create(nome=form.cleaned_data["nuovo_interesse"].strip().capitalize())
                        nuovo.interessi.add(i)
                    if dom.compilato():
                        _salva_indirizzo(nuovo, "domicilio", dom.cleaned_data)
                    if diversa:
                        _salva_indirizzo(nuovo, "spedizione", sped.cleaned_data)
                    else:
                        nuovo.indirizzi.filter(tipo="spedizione", paletta__isnull=True).delete()
                messages.success(request, f"Scheda {nuovo.codice} – {nuovo.nome} salvata.")
                if ritorno:
                    return redirect(f"{ritorno}{'&' if '?' in ritorno else '?'}cliente={nuovo.pk}")
                return redirect("scheda", nuovo.pk)
    else:
        iniziali = {}
        if not s and request.GET.get("ruolo") == "mandante":
            iniziali["is_mandante"] = True
        elif not s:
            iniziali["is_cliente"] = True
        form = SchedaForm(instance=s, initial=iniziali)
        dom = IndirizzoForm(prefix="dom", initial=_da_indirizzo(s.domicilio if s else None))
        sped = IndirizzoForm(prefix="sped", initial=_da_indirizzo(spedizione_esistente))
        diversa = bool(spedizione_esistente)

    storico = {}
    if s:
        storico["acquisti"] = (Aggiudicazione.objects.filter(paletta__acquirente=s)
                               .select_related("lotto__asta", "paletta").order_by("-lotto__asta__data", "lotto__ordine"))
        storico["palette"] = s.palette.select_related("asta").order_by("-asta__data")
        storico["mandati"] = s.mandati.order_by("-data")
        storico["affidati"] = (Lotto.objects.filter(mandato__mandante=s)
                               .select_related("asta", "mandato").order_by("-asta__data", "ordine"))
        storico["totale_acquisti"] = sum(a.prezzo_martello for a in storico["acquisti"])
    return render(request, "asta/scheda.html", {
        "s": s, "form": form, "dom": dom, "sped": sped, "diversa": diversa, "puo": puo,
        "ritorno": ritorno, **storico})


# ------------------------------------------------------------------ palette

def _asta_di_lavoro(request):
    if request.GET.get("asta"):
        return get_object_or_404(Asta, pk=request.GET["asta"])
    oggi = timezone.localdate()
    aperte = list(Asta.objects.filter(chiusa=False).order_by("data"))
    prossima = next((a for a in aperte if a.data >= oggi), None)
    return prossima or (aperte[-1] if aperte else Asta.objects.order_by("-data").first())


def prossima_per_canale(asta, tipo: str):
    """Primo numero libero nelle fasce di quel canale (es. telefono → 300, 301…)."""
    numeri = set(asta.palette.values_list("numero", flat=True))
    for f in asta.fasce.filter(tipo=tipo).order_by("da"):
        for n in range(f.da, f.a + 1):
            if n not in numeri:
                return n
    return None


def prossima_paletta(asta, online=False) -> int:
    if asta.fasce.exists():
        return prossima_per_canale(asta, "live" if online else "sala")
    numeri = set(asta.palette.values_list("numero", flat=True))
    limite = asta.palette_online_da or 10 ** 6
    n = limite if online else 1
    if not online and not numeri:
        n = 101  # le palette in sala partono da 101
    elif not online:
        n = max([x for x in numeri if x < limite], default=100) + 1
    while n in numeri:
        n += 1
    return n


@login_required
@permission_required("asta.view_paletta", raise_exception=True)
def palette(request):
    asta = _asta_di_lavoro(request)
    if not asta:
        messages.info(request, "Prima apri un'asta.")
        return redirect("asta_nuova")
    puo = request.user.has_perm("asta.add_paletta")
    vede = request.user.has_perm("asta.view_dati_personali")
    cliente = None
    if request.GET.get("cliente"):
        cliente = Soggetto.objects.filter(pk=request.GET["cliente"]).first()
    if request.method == "POST" and puo:
        try:
            cliente = Soggetto.objects.get(pk=request.POST.get("cliente"))
            numero = int(request.POST.get("numero"))
            if asta.fasce.exists() and not asta.fascia_di(numero):
                raise ValueError("fuori fascia")
            with transaction.atomic():  # la consegna (ritiro o spedizione) si decide dopo l'asta
                p = Paletta.objects.create(asta=asta, numero=numero, acquirente=cliente)
            if not cliente.is_cliente:
                cliente.is_cliente = True
                cliente.save()
            messages.success(request, f"Paletta {p.numero} assegnata a {cliente.nome}.")
            return redirect(f"{request.path}?asta={asta.pk}")
        except Soggetto.DoesNotExist:
            messages.error(request, "Scegli il cliente dall'elenco (o crea una nuova scheda).")
        except (TypeError, ValueError) as e:
            messages.error(request, "Il numero non rientra in nessuna fascia di palette di questa asta."
                           if "fascia" in str(e) else "Numero di paletta non valido.")
        except IntegrityError:
            messages.error(request, "Quella paletta è già assegnata, oppure il cliente ha già una paletta in questa asta.")
    elenco = asta.palette.select_related("acquirente", "indirizzo_spedizione").order_by("numero")
    gia = set(elenco.values_list("acquirente_id", flat=True))
    clienti = [c for c in Soggetto.objects.order_by("codice") if c.pk not in gia] if vede else []
    return render(request, "asta/palette.html", {
        "asta": asta, "elenco": elenco, "puo": puo, "clienti": clienti, "cliente": cliente,
        "num_sala": prossima_paletta(asta), "num_online": prossima_paletta(asta, online=True) if asta.palette_online_da or asta.fasce.exists() else None,
        "canali": [(v, n, prossima_per_canale(asta, v), [f for f in asta.fasce.all() if f.tipo == v])
                   for v, n in __import__("asta.models", fromlist=["FasciaPalette"]).FasciaPalette.Tipo.choices
                   if asta.fasce.filter(tipo=v).exists()],
        "aste": Asta.objects.filter(chiusa=False).order_by("data")})
