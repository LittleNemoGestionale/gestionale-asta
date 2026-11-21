"""Caricamento del catalogo da file: Word (schede), faldone Excel o Excel semplice.

Tutti i formati producono righe con la stessa forma, mostrate in anteprima prima del salvataggio.
"""
import io
import re
from decimal import Decimal, InvalidOperation

from .import_word import Riconoscitore, leggi_catalogo_word
from .models import Lotto, Mandato

COLONNE = {
    "numero": ["lotto", "n lotto", "nr lotto", "numero", "n."],
    "bis": ["bis"],
    "tornata": ["tornata", "tornate", "sessione"],
    "titolo": ["titolo"],
    "illustratore": ["illustratore", "autore"],
    "descrizione": ["descrizione", "d.contenutistica", "oggetto"],
    "mandato": ["mandato", "mand. n.", "n mandato", "codice mandante", "cod mandante"],
    "mandante_testo": ["mandante", "mandanti"],
    "stima_min": ["stima min", "stima minima", "stima da"],
    "stima_max": ["stima max", "stima massima", "stima a", "stima", "valore c.a."],
    "base_asta": ["base", "base asta", "base d'asta", "base catalogo", "partenza"],
    "riserva": ["riserva", "prezzo di riserva", "prezzo"],
    "commissione_pct": ["commissione", "commissione %", "comm %"],
    "note_interne": ["note"],
}
CAMPI_EURO = ["stima_min", "stima_max", "base_asta", "riserva", "commissione_pct"]


def euro(v):
    if v in (None, ""):
        return None
    if isinstance(v, (int, float, Decimal)):
        return Decimal(str(v))
    s = str(v).replace("€", "").replace(" ", "").strip()
    if not s:
        return None
    if "," in s or re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "").replace(",", ".")
    return Decimal(s)


def prossimo_numero(asta) -> int:
    ultimo = asta.lotti.order_by("-ordine").values_list("ordine", flat=True).first()
    return (ultimo or 0) + 1


def _riga_vuota(**kw):
    r = {"riga": None, "numero": "", "titolo": "", "illustratore": "", "descrizione": "", "sezione": "",
         "tornata": None, "mandato_id": None, "mandato": "", "mandante_testo": "", "note_interne": "",
         "da_fotografare": False, "escluso": False, "errori": [], "avvisi": [],
         "valori": {k: None for k in CAMPI_EURO}}
    r.update(kw)
    return r


def leggi_excel(contenuto: bytes, asta) -> list[dict]:
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(contenuto), data_only=True)
    ws = wb["Foglio1"] if "Foglio1" in wb.sheetnames else wb.worksheets[0]  # il faldone ha i dati nel Foglio1
    righe = list(ws.iter_rows(values_only=True))
    norm = lambda c: re.sub(r"\s+", " ", str(c or "").strip().lower())  # noqa: E731
    idx_head = next((i for i, r in enumerate(righe[:15])
                     if any(norm(c) in COLONNE["descrizione"] + COLONNE["titolo"] for c in r)), None)
    if idx_head is None:
        raise ValueError("Non trovo la riga di intestazione: serve una colonna «Titolo» o «Descrizione».")
    head = [norm(c) for c in righe[idx_head]]
    pos = {}
    for campo, nomi in COLONNE.items():
        for i, h in enumerate(head):
            if h in nomi and i not in pos.values():
                pos[campo] = i
                break
    if "mandato" not in pos and "mandante_testo" not in pos:
        raise ValueError("Manca la colonna del mandante («Mandato», «Mandante» o «Mand. N.»).")
    ric = Riconoscitore()
    prossimo = prossimo_numero(asta)
    out = []
    for n_riga, r in enumerate(righe[idx_head + 1:], start=idx_head + 2):
        get = lambda campo: r[pos[campo]] if campo in pos and pos[campo] < len(r) else None  # noqa: E731
        if not get("titolo") and not get("descrizione"):
            continue
        riga = _riga_vuota(riga=n_riga)
        numero = str(get("numero") or "").strip()
        numero = numero[:-2] if numero.endswith(".0") else numero
        if not numero:
            numero = str(prossimo)
            prossimo += 1
        riga["numero"] = numero + str(get("bis") or "").strip()
        for k in ["titolo", "illustratore", "descrizione", "note_interne"]:
            riga[k] = str(get(k) or "").strip()
        try:
            riga["tornata"] = int(get("tornata")) if get("tornata") not in (None, "") else None
        except (TypeError, ValueError):
            riga["errori"].append(f"tornata non valida: «{get('tornata')}»")
        for k in CAMPI_EURO:
            try:
                v = euro(get(k))
                riga["valori"][k] = str(v) if v is not None else None
            except InvalidOperation:
                riga["errori"].append(f"{k.replace('_', ' ')} non valido: «{get(k)}»")
        # nel faldone «Prezzo» = riserva; se coincide con la base non è una vera riserva
        if riga["valori"]["riserva"] and riga["valori"]["riserva"] == riga["valori"]["base_asta"]:
            riga["valori"]["riserva"] = None
        testo_mandato = str(get("mandato") or "").strip().removesuffix(".0")
        riga["mandante_testo"] = str(get("mandante_testo") or "").strip() or testo_mandato
        m = Mandato.objects.filter(numero__iexact=testo_mandato).select_related("mandante").first() \
            if testo_mandato else None
        if not m and testo_mandato:
            ms = Mandato.objects.filter(mandante__codice__iexact=testo_mandato).order_by("-data")
            m = ms.first()
        if not m and riga["mandante_testo"]:
            m = Riconoscitore.mandato_di(ric.trova(riga["mandante_testo"]), asta)
        if m:
            riga["mandato_id"], riga["mandato"] = m.pk, f"{m.mandante.codice} · {m.mandante.nome} · {m.numero}"
        out.append(riga)
    return out


def leggi_word(contenuto: bytes, asta) -> list[dict]:
    out = []
    for l in leggi_catalogo_word(contenuto, asta, prossimo_numero(asta)):
        out.append(_riga_vuota(
            numero=l["numero"], titolo=l["titolo"], descrizione=l["descrizione"], sezione=l["sezione"],
            mandato_id=l["mandato_id"], mandato=l["mandato"], mandante_testo=l["mandante_testo"],
            note_interne=l["note_interne"], da_fotografare=l["da_fotografare"], escluso=l["escluso"],
            avvisi=[a for a in l["avvisi"] if "mandante" not in a and "mandato" not in a],
            valori={"stima_min": None, "stima_max": l["stima"], "base_asta": l["base"], "riserva": l["riserva"],
                    "commissione_pct": None}))
    return out


def leggi_file(nome: str, contenuto: bytes, asta) -> tuple[str, list[dict]]:
    if nome.lower().endswith(".docx"):
        return "word", leggi_word(contenuto, asta)
    if nome.lower().endswith((".xlsx", ".xlsm")):
        return "excel", leggi_excel(contenuto, asta)
    raise ValueError("Formato non riconosciuto: carica un catalogo Word (.docx) o un Excel (.xlsx).")


def controlla(righe: list[dict], asta, abbinamenti: dict[str, int] | None = None,
              inizi_tornate: dict[int, int] | None = None) -> list[dict]:
    """Applica abbinamenti dei mandanti e tornate, e ricalcola gli errori bloccanti."""
    esistenti = set(asta.lotti.values_list("numero", flat=True))
    visti = set()
    abbinamenti = abbinamenti or {}
    mandati = {m.pk: m for m in Mandato.objects.filter(pk__in=[v for v in abbinamenti.values() if v])
               .select_related("mandante")}
    for r in righe:
        r["errori"] = [e for e in r["errori"] if not e.startswith(("il lotto", "lotto", "mandante", "descrizione"))]
        if r["escluso"]:
            continue
        chiave = r["mandante_testo"] or ""
        if chiave in abbinamenti and abbinamenti[chiave]:
            m = mandati.get(abbinamenti[chiave])
            if m:
                r["mandato_id"], r["mandato"] = m.pk, f"{m.mandante.codice} · {m.mandante.nome} · {m.numero}"
        if not r["mandato_id"]:
            r["errori"].append("mandante da abbinare" if chiave else "mandante mancante")
        if r["numero"] in esistenti:
            r["errori"].append(f"il lotto {r['numero']} esiste già nell'asta")
        if r["numero"] in visti:
            r["errori"].append(f"lotto {r['numero']} ripetuto nel file")
        visti.add(r["numero"])
        if not r["titolo"] and not r["descrizione"]:
            r["errori"].append("descrizione vuota")
        if inizi_tornate:
            try:
                n = int(re.match(r"\d+", r["numero"]).group())
                r["tornata"] = max([t for t, da in inizi_tornate.items() if da and n >= da], default=1)
            except (AttributeError, ValueError):
                pass
    return righe


def salva(righe: list[dict], asta) -> int:
    n = 0
    for r in righe:
        if r["escluso"] or r["errori"]:
            continue
        Lotto.objects.create(
            asta=asta, numero=r["numero"], tornata=r["tornata"] or 1, sezione=r["sezione"][:150],
            titolo=r["titolo"][:250], illustratore=r["illustratore"][:200], descrizione=r["descrizione"],
            note_interne=r["note_interne"][:250], da_fotografare=r["da_fotografare"], mandato_id=r["mandato_id"],
            **{k: (Decimal(v) if v is not None else None) for k, v in r["valori"].items()})
        n += 1
    return n
