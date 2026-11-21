"""Registro delle offerte scritte e dei telefoni: inserimento per paletta, elenco per lotto
(per il banditore), lista per telefonista."""
import re
from decimal import InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import get_object_or_404, redirect, render

from .import_lotti import euro
from .models import Asta, Lotto, OffertaScritta, Paletta, Telefonata


def numeri_lotti(testo: str) -> list[str]:
    """«12, 15, 40-42 18a» → ["12", "15", "40", "41", "42", "18a"]."""
    out = []
    for pezzo in re.split(r"[,;\s]+", (testo or "").strip()):
        if not pezzo:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", pezzo)
        if m and int(m.group(1)) <= int(m.group(2)) and int(m.group(2)) - int(m.group(1)) < 500:
            out += [str(n) for n in range(int(m.group(1)), int(m.group(2)) + 1)]
        else:
            out.append(pezzo.lower().lstrip("0") or "0")
    return out


def righe_offerte(testo: str) -> list[tuple[str, str]]:
    """Una riga per lotto: «12 150», «12: 1.500», «12 = 150,00 €», «12bis 80»."""
    out = []
    for riga in (testo or "").splitlines():
        riga = riga.strip()
        if not riga:
            continue
        m = re.match(r"^(\d+\s*[a-zA-Z]{0,3})\s*[:=\-–\s]\s*€?\s*([\d.,]+)\s*€?\s*(.*)$", riga)
        out.append((re.sub(r"\s+", "", m.group(1)).lower().lstrip("0"), m.group(2), m.group(3)) if m
                   else ("", riga, ""))
    return out


@login_required
@permission_required("asta.view_paletta", raise_exception=True)
def offerte_telefoni(request, asta_id):
    asta = get_object_or_404(Asta, pk=asta_id)
    lotti = {l.numero.lower(): l for l in asta.lotti.all()}
    if request.method == "POST":
        if not request.user.has_perm("asta.change_paletta"):
            messages.error(request, "Non hai il permesso di modificare il registro.")
            return redirect("offerte_telefoni", asta.pk)
        azione = request.POST.get("azione")
        if azione in ("offerte", "telefoni"):
            num = (request.POST.get("paletta") or "").strip()
            paletta = Paletta.objects.filter(asta=asta, numero=num).select_related("acquirente").first() \
                if num.isdigit() else None
            if not paletta:
                messages.error(request, f"La paletta «{num}» non è assegnata in questa asta: assegnala prima nella "
                                        "pagina Palette, poi torna qui.")
                return redirect(f"{request.path}?paletta={num}#inserisci")
            fatti, errori, avvisi = 0, [], []
            if azione == "offerte":
                for numero, importo_txt, nota in righe_offerte(request.POST.get("testo")):
                    lotto = lotti.get(numero)
                    if not lotto:
                        errori.append(f"«{numero or importo_txt}»: lotto non trovato" if numero else
                                      f"«{importo_txt}»: scrivi lotto e importo, es. «12 150»")
                        continue
                    try:
                        importo = euro(importo_txt)
                    except InvalidOperation:
                        importo = None
                    if not importo or importo <= 0:
                        errori.append(f"lotto {lotto.numero}: importo «{importo_txt}» non valido")
                        continue
                    if lotto.base_asta and importo < lotto.base_asta:
                        avvisi.append(f"lotto {lotto.numero}: {importo} € è sotto la base ({lotto.base_asta} €)")
                    OffertaScritta.objects.update_or_create(lotto=lotto, paletta=paletta,
                                                            defaults={"importo": importo, "note": nota[:200]})
                    fatti += 1
                testo = "offerte scritte registrate"
            else:
                telefonista = (request.POST.get("telefonista") or "").strip()[:80]
                nota = (request.POST.get("note") or "").strip()[:200]
                for numero in numeri_lotti(request.POST.get("lotti")):
                    lotto = lotti.get(numero)
                    if not lotto:
                        errori.append(f"lotto «{numero}» non trovato")
                        continue
                    t, nuovo = Telefonata.objects.get_or_create(lotto=lotto, paletta=paletta)
                    if telefonista or nota or nuovo:
                        t.telefonista = telefonista or t.telefonista
                        t.note = nota or t.note
                        t.save()
                    fatti += 1
                testo = "lotti al telefono registrati"
            if fatti:
                messages.success(request, f"Paletta {paletta.numero}: {fatti} {testo}.")
            for a in avvisi:
                messages.info(request, a)
            if errori:
                messages.error(request, "Non registrati: " + "; ".join(errori))
            return redirect(f"{request.path}?paletta={paletta.numero}#inserisci")
        if azione == "togli_offerta":
            OffertaScritta.objects.filter(pk=request.POST.get("id"), lotto__asta=asta).delete()
        elif azione == "togli_telefonata":
            Telefonata.objects.filter(pk=request.POST.get("id"), lotto__asta=asta).delete()
        elif azione == "telefonista":
            nome = (request.POST.get("telefonista") or "").strip()[:80]
            n = Telefonata.objects.filter(pk__in=request.POST.getlist("sel"), lotto__asta=asta).update(telefonista=nome)
            messages.success(request, f"{n} telefonate assegnate a {nome or '(nessuno)'}.")
        return redirect(f"{request.path}?{request.GET.urlencode()}")

    vista = request.GET.get("vista", "lotti")
    offerte = list(OffertaScritta.objects.filter(lotto__asta=asta).select_related("lotto", "paletta__acquirente"))
    telefonate = list(Telefonata.objects.filter(lotto__asta=asta).select_related("lotto", "paletta__acquirente"))
    per_lotto = {}
    for o in offerte:
        per_lotto.setdefault(o.lotto_id, {"lotto": o.lotto, "offerte": [], "telefoni": []})["offerte"].append(o)
    for t in telefonate:
        per_lotto.setdefault(t.lotto_id, {"lotto": t.lotto, "offerte": [], "telefoni": []})["telefoni"].append(t)
    righe = sorted(per_lotto.values(), key=lambda r: (r["lotto"].ordine, r["lotto"].numero))
    for r in righe:  # la più alta vince; a parità, la prima ricevuta
        r["offerte"].sort(key=lambda o: (-o.importo, o.ricevuta_il))
    telefonisti = {}
    for t in sorted(telefonate, key=lambda t: (t.lotto.ordine, t.lotto.numero)):
        telefonisti.setdefault(t.telefonista or "(da assegnare)", []).append(t)
    sel = (request.GET.get("paletta") or "").strip()
    paletta_sel = Paletta.objects.filter(asta=asta, numero=sel).select_related("acquirente").first() if sel.isdigit() else None
    return render(request, "asta/offerte_telefoni.html", {
        "asta": asta, "vista": vista, "righe": righe, "telefonisti": sorted(telefonisti.items()),
        "n_offerte": len(offerte), "n_telefonate": len(telefonate),
        "nomi_telefonisti": sorted({t.telefonista for t in telefonate if t.telefonista}),
        "paletta_sel": paletta_sel, "sel": sel,
        "offerte_sel": [o for o in offerte if paletta_sel and o.paletta_id == paletta_sel.pk],
        "telefonate_sel": [t for t in telefonate if paletta_sel and t.paletta_id == paletta_sel.pk],
        "palette": Paletta.objects.filter(asta=asta).order_by("numero").values_list("numero", flat=True),
        "vede_dati": request.user.has_perm("asta.view_dati_personali"),
    })

