"""Impostazioni della casa d'aste (intestazione dei documenti, logo, IBAN, email), modificabili dal programma.

Si salvano in dati/impostazioni.ini e valgono subito, senza riavviare."""
import io
import os
import re

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render

CAMPI = [  # (chiave nell'ini, etichetta, aiuto, righe)
    ("CASA_ASTA_NOME", "Nome della casa d'aste", "come compare sui documenti, es. Little Nemo srl", 1),
    ("CASA_ASTA_INTESTAZIONE", "Intestazione sotto il logo", "una riga per riga: indirizzo, telefono, C.F. e P.IVA, sito ed email…", 6),
    ("CASA_ASTA_EMAIL", "Email da cui scrivete", "", 1),
    ("CASA_ASTA_IBAN", "IBAN per i pagamenti degli acquirenti", "", 1),
    ("CASA_ASTA_INTESTATARIO", "Intestatario del conto", "vuoto = il nome della casa d'aste", 1),
    ("CASA_ASTA_FIRMA", "Firma delle email", "", 3),
]


class ImpostazioniForm(forms.Form):
    logo = forms.ImageField(required=False, label="Logo (PNG o JPG)",
                            help_text="compare in alto a sinistra su conteggi e rendiconti")
    togli_logo = forms.BooleanField(required=False, label="Togli il logo")
    anthropic = forms.CharField(required=False, label="Chiave di Claude (lettura delle foto)",
                                widget=forms.PasswordInput(render_value=False),
                                help_text="lascia vuoto per non cambiarla")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for chiave, etichetta, aiuto, righe in reversed(CAMPI):
            valore = getattr(settings, chiave, "") or ""
            if righe > 1:
                valore = "\n".join(r.strip() for r in valore.split("|"))
            campo = forms.CharField(required=chiave == "CASA_ASTA_NOME", label=etichetta, help_text=aiuto, initial=valore,
                                    widget=forms.Textarea(attrs={"rows": righe}) if righe > 1 else forms.TextInput())
            self.fields = {chiave: campo, **self.fields}


def scrivi_ini(valori: dict):
    """Aggiorna (o aggiunge) le righe «CHIAVE = valore» senza toccare il resto del file e i commenti."""
    ini = settings.DATI_DIR / "impostazioni.ini"
    righe = ini.read_text(encoding="utf-8").splitlines() if ini.exists() else ["[gestionale]"]
    if not any(r.strip() == "[gestionale]" for r in righe):
        righe.insert(0, "[gestionale]")
    for chiave, valore in valori.items():
        testo = f"{chiave} = {valore.replace('%', '%%')}"  # «%» va raddoppiato nel file .ini
        for i, r in enumerate(righe):
            if re.match(rf"^\s*{chiave}\s*[=:]", r):
                righe[i] = testo
                break
        else:
            righe.append(testo)
    ini.write_text("\n".join(righe) + "\n", encoding="utf-8")


@user_passes_test(lambda u: u.is_superuser)
def impostazioni(request):
    form = ImpostazioniForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        valori = {}
        for chiave, _, _, righe in CAMPI:
            v = d[chiave].strip()
            if righe > 1:
                v = " | ".join(r.strip() for r in v.splitlines() if r.strip())
            valori[chiave] = v
        if d["anthropic"].strip():
            valori["ANTHROPIC_API_KEY"] = d["anthropic"].strip()
        scrivi_ini(valori)
        for chiave, v in valori.items():  # valgono subito, senza riavviare
            os.environ[chiave] = v
            if chiave.startswith("CASA_ASTA_"):
                setattr(settings, chiave, v)
        logo = settings.DATI_DIR / "logo.png"
        if d["togli_logo"]:
            logo.unlink(missing_ok=True)
        elif d["logo"]:
            from PIL import Image
            img = Image.open(d["logo"])
            img.save(logo, "PNG")
            settings.CASA_ASTA_LOGO = str(logo)
        messages.success(request, "Impostazioni salvate: valgono subito per i nuovi documenti e le email.")
        return redirect("impostazioni")
    c_logo = getattr(settings, "CASA_ASTA_LOGO", "")
    return render(request, "asta/impostazioni.html", {
        "form": form, "ha_logo": bool(c_logo) and os.path.exists(c_logo),
        "chiave_claude": bool(os.environ.get("ANTHROPIC_API_KEY"))})


@user_passes_test(lambda u: u.is_superuser)
def logo(request):
    percorso = getattr(settings, "CASA_ASTA_LOGO", "")
    if not percorso or not os.path.exists(percorso):
        raise Http404
    return FileResponse(open(percorso, "rb"), content_type="image/png")
