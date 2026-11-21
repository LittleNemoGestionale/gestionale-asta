"""Caricamento del catalogo da file: Word (schede), faldone Excel o Excel semplice.

Tutti i formati producono righe con la stessa forma, mostrate in anteprima prima del salvataggio.
"""
import io
import re
from decimal import Decimal, InvalidOperation

from .import_word import Riconoscitore, _parole, leggi_catalogo_word
from .models import Lotto, Mandato, Oggetto

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
        riga["mandato_numero"] = testo_mandato
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
            tornata=l.get("tornata"), illustratore=l.get("illustratore", ""),
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


PAROLE_VUOTE = {"DI", "DEL", "DELLA", "DEI", "DELLE", "CON", "PER", "UNA", "UNO", "THE", "AND", "LOTTO", "COMPOSTO",
                "DA", "IN", "SU", "AL", "ALLA", "NEL", "NELLA", "CM", "NN", "IL", "LO", "LA", "LE", "GLI", "ED"}


def _chiavi(testo: str) -> set[str]:
    testo = re.sub(r"[’‘'`´]", " ", testo or "")  # «L’Astronave» = «L'Astronave»
    return {p for p in _parole(testo) if (len(p) > 2 or p.isdigit()) and p not in PAROLE_VUOTE}


def _somiglianza(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    comuni = len(a & b)
    return 0.5 * comuni / len(a | b) + 0.5 * comuni / min(len(a), len(b))


def abbina_oggetti(righe: list[dict], asta, scelte: dict[int, str] | None = None) -> None:
    """Collega ogni lotto del catalogo all'oggetto ricevuto con il mandato (stesso mandante, descrizione simile).

    Sicuro → collegato da solo; dubbio → proposto, da confermare nell'anteprima; scelte = decisioni dell'utente."""
    scelte = scelte or {}
    mandanti = {m.pk: m.mandante_id for m in Mandato.objects.filter(pk__in={r["mandato_id"] for r in righe if r["mandato_id"]})}
    gia_in_asta = set(asta.lotti.exclude(oggetto=None).values_list("oggetto_id", flat=True))
    per_mandante = {}
    for o in (Oggetto.objects.filter(mandato__mandante_id__in=set(mandanti.values()), restituito_il=None)
              .select_related("mandato").prefetch_related("lotti__asta", "lotti__aggiudicazione")):
        if o.pk in gia_in_asta:
            continue
        o._lotti = sorted(o.lotti.all(), key=lambda l: (l.asta.data, l.pk))
        if o.disponibile:
            per_mandante.setdefault(o.mandato.mandante_id, []).append((o, _chiavi(o.descrizione)))
    usati = {int(v) for v in scelte.values() if v and str(v).isdigit()}
    for i, r in enumerate(righe):
        r["oggetto_id"], r["oggetto"], r["oggetto_candidati"], r["oggetto_dubbio"] = None, "", [], False
        if r["escluso"] or not r["mandato_id"]:
            continue
        testo = _chiavi(" ".join([r["titolo"], r.get("illustratore", ""), r["descrizione"][:400]]))
        titolo = _chiavi(" ".join([r["titolo"], r.get("illustratore", "")]))
        punteggi = []
        for o, chiavi in per_mandante.get(mandanti.get(r["mandato_id"]), []):
            p = max(_somiglianza(testo, chiavi), _somiglianza(titolo, chiavi))
            v = r["valori"]
            if o.stima_max and v.get("stima_max") and Decimal(v["stima_max"]) == o.stima_max:
                p += 0.2
            if o.riserva and v.get("riserva") and Decimal(v["riserva"]) == o.riserva:
                p += 0.1
            if p >= 0.2:
                punteggi.append((p, o))
        punteggi.sort(key=lambda x: -x[0])
        r["oggetto_candidati"] = [(o.pk, f"{o.codice} · {o.descrizione[:60]}") for _, o in punteggi[:4]]
        if i in scelte:  # deciso a mano nell'anteprima
            scelto = next((o for _, o in punteggi if str(o.pk) == str(scelte[i])), None)
            if scelte[i] and not scelto:
                scelto = Oggetto.objects.filter(pk=scelte[i]).select_related("mandato").first()
                if scelto:
                    r["oggetto_candidati"].insert(0, (scelto.pk, f"{scelto.codice} · {scelto.descrizione[:60]}"))
        else:
            liberi = [(p, o) for p, o in punteggi if o.pk not in usati]
            sicuro = liberi and liberi[0][0] >= 0.5 and (len(liberi) == 1 or liberi[0][0] - liberi[1][0] >= 0.15)
            scelto = liberi[0][1] if sicuro else None
            r["oggetto_dubbio"] = bool(liberi) and not sicuro
        if scelto:
            usati.add(scelto.pk)
            r["oggetto_id"], r["oggetto"] = scelto.pk, scelto.codice


def controlla(righe: list[dict], asta, abbinamenti: dict[str, int] | None = None,
              inizi_tornate: dict[int, int] | None = None, scelte_oggetti: dict[int, str] | None = None) -> list[dict]:
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
        if chiave not in abbinamenti:  # «ROSSI MANCA FOTO» usa l'abbinamento di «ROSSI»
            prefisso = next((k for k in sorted(abbinamenti, key=len, reverse=True)
                             if k and abbinamenti[k] and chiave.startswith(k + " ")), None)
            if prefisso:
                chiave = prefisso
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
    abbina_oggetti(righe, asta, scelte_oggetti)
    return righe


MANDANTE_SCONOSCIUTO = "MANDANTE DA ABBINARE"
ERRORI_NON_BLOCCANTI = ("mandante da abbinare", "mandante mancante")


def bloccante(r) -> bool:
    """Solo i numeri doppi o le righe vuote impediscono il caricamento; il resto si carica e si segnala."""
    return any(not e.startswith(ERRORI_NON_BLOCCANTI) for e in r["errori"])


PAROLE_DITTA = re.compile(r"\b(SRL|S\.R\.L|SNC|SAS|SPA|S\.P\.A|COMICS|LIBRERIA|GALLERIA|EDIZIONI|EDITORE|STUDIO|"
                          r"ASSOCIAZIONE|FONDAZIONE|ARCHIVIO|COLLEZIONE|SHOP|STORE|LTD|GMBH|INC|DITTA|CASA|MANDANTE|ACQUIRENTE)\b", re.I)


def nuova_scheda_mandante(testo: str, origine: str):
    """Scheda da completare: «ROSSI MARIO» → persona Rossi Mario; «VENERE COMICS» → ditta."""
    from .models import Soggetto
    parole = testo.split()
    nota = f"Scheda creata {origine}: completare i dati e verificare la percentuale del mandato."
    if 2 <= len(parole) <= 3 and testo.upper() == testo and not PAROLE_DITTA.search(testo):
        return Soggetto.objects.create(tipo="privato", cognome=parole[0].title(),
                                       nome_proprio=" ".join(parole[1:]).title(), is_mandante=True, note=nota)
    return Soggetto.objects.create(tipo="azienda", ragione_sociale=testo, is_mandante=True, note=nota)


def mandato_provvisorio(testo: str, asta, creati: dict, numero: str = ""):
    """Mandante dal catalogo/faldone: lo cerca in anagrafica, altrimenti crea la scheda da completare,
    con il mandato (il «Mand. N.» del faldone, se c'è)."""
    from django.utils import timezone

    from .models import Soggetto
    testo = (testo or "").strip() or MANDANTE_SCONOSCIUTO
    numero = (numero or "").strip()
    chiave = (testo, numero)
    if chiave in creati:
        return creati[chiave]
    gia = Mandato.objects.filter(numero=numero).select_related("mandante").first() if numero else None
    sogg = gia.mandante if gia else None
    if sogg is None and testo != MANDANTE_SCONOSCIUTO:
        sogg = Riconoscitore().trova(testo)
    if sogg is None:
        sogg = next((x for x in Soggetto.objects.filter(is_mandante=True) if (x.nome or "").upper() == testo.upper()), None)
    if sogg is None:
        sogg = nuova_scheda_mandante(testo, f"dal catalogo dell'asta {asta.numero or asta.nome}" if asta else "dal faldone")
    m = gia if gia and gia.mandante_id == sogg.pk else None
    if m is None and not numero:
        m = Riconoscitore.mandato_di(sogg, asta)
    if m is None:
        if not numero or Mandato.objects.filter(numero=numero).exists():
            anno = timezone.localdate().year
            n = Mandato.objects.filter(numero__startswith=f"{anno}/").count() + 1
            while Mandato.objects.filter(numero=f"{anno}/{n:03d}").exists():
                n += 1
            numero = f"{anno}/{n:03d}"
        m = Mandato.objects.create(mandante=sogg, numero=numero, data=timezone.localdate(), asta=asta,
                                   commissione_pct=asta.diritti_pct if asta else 15,
                                   note_accordo="DA VERIFICARE: mandato creato dal catalogo/faldone, controllare la percentuale.")
    creati[chiave] = m
    return m


def salva(righe: list[dict], asta) -> dict:
    """Carica tutto ciò che si può; restituisce il riepilogo di cosa va controllato."""
    esito = {"caricati": 0, "da_verificare": [], "non_caricati": [], "mandanti_creati": {},
             "collegati": sum(1 for r in righe if r.get("oggetto_id") and not r["escluso"] and not bloccante(r))}
    creati = {}
    for r in righe:
        if r["escluso"]:
            continue
        if bloccante(r):
            esito["non_caricati"].append(f"{r['numero'] or '?'} {r['titolo'][:40]}: {'; '.join(r['errori'])}")
            continue
        note = r["note_interne"]
        # il mandante scritto a parole diventa una scheda (segnalata a parte): il lotto in sé è a posto
        problemi = [e for e in r["errori"] if not e.startswith("mandante da abbinare")] + list(r.get("avvisi", []))
        mandato_id = r["mandato_id"]
        if not mandato_id:
            m = mandato_provvisorio(r["mandante_testo"], asta, creati, r.get("mandato_numero", ""))
            mandato_id = m.pk
            esito["mandanti_creati"].setdefault(m.mandante.nome, 0)
            esito["mandanti_creati"][m.mandante.nome] += 1
        if problemi:
            note = f"DA VERIFICARE: {'; '.join(problemi)}. {note}".strip()
            esito["da_verificare"].append(r["numero"])
        Lotto.objects.create(
            asta=asta, numero=r["numero"], tornata=r["tornata"] or 1, sezione=r["sezione"][:150],
            titolo=r["titolo"][:250], illustratore=r["illustratore"][:200], descrizione=r["descrizione"],
            note_interne=note[:250], da_fotografare=r["da_fotografare"], mandato_id=mandato_id,
            oggetto_id=r.get("oggetto_id") if mandato_id == r["mandato_id"] else None,
            **{k: (Decimal(v) if v is not None else None) for k, v in r["valori"].items()})
        esito["caricati"] += 1
    # i mandati usati senza un'asta indicata diventano «mandati per questa asta»
    usati = set(asta.lotti.values_list("mandato_id", flat=True))
    Mandato.objects.filter(pk__in=usati, asta=None).update(asta=asta)
    return esito
