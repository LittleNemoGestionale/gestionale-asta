"""Lettura del catalogo scritto in Word (es. «Schede Proprietà AT91.docx»).

Struttura riconosciuta:
    LOCANDINE                                   ← titolo di sezione (MAIUSCOLO, grassetto)
    FANTASCIENZA U.S.A. ANNI '50                ← sottosezione
    00. Dr. Cyclops                             ← lotto: numero (00 = da numerare) e titolo
    USA, regia di E. Schoedsack. Fotobusta …    ← una o più righe di descrizione
    € 90/200 R100 ROSSI DA FOTOGRAFARE         ← base/stima, riserva, mandante, note di lavoro

Le sezioni «DA INSERIRE» e «PROSSIME ASTE O VENDITA DIRETTA» non vanno nell'asta.
"""
import io
import re
import unicodedata

from .models import Mandato, Soggetto

SEZIONI_ESCLUSE = ("DA INSERIRE", "PROSSIME ASTE", "VENDITA DIRETTA", "ESCLUSI", "DA TOGLIERE")
ORDINALI = {"PRIMA": 1, "SECONDA": 2, "TERZA": 3, "QUARTA": 4, "QUINTA": 5, "SESTA": 6}
RX_TORNATA = re.compile(r"\b(FINE\s+)?(PRIMA|SECONDA|TERZA|QUARTA|QUINTA|SESTA)\s+TORNATA\b", re.I)
# parole che indicano note di lavoro nella riga del prezzo (da qui in poi non è più il mandante)
INIZIO_NOTE = re.compile(r"\b(DA\s+FOTOGRAFARE|FOTOGRAFARE|FOTO\s+DA\s+FARE|MANCA\s+FOTO|MANCA|DA\s+ABBINARE|"
                         r"VALORE\s+ESIGUO|RISTAMPONE|FAR\s+VEDERE|DA\s+ACCORPARE|DA\s+VERIFICARE|DA\s+CONTROLLARE|"
                         r"RESO|NON\s+\w+|CATAWIKI)\b|[(?]", re.I)
RX_LOTTO = re.compile(r"^\s*(\d{1,4})\s*([a-zA-Z]{0,3})\s*[.)]\s+(.+)$")
RX_LOTTO_ATTACCATO = re.compile(r"^\s*(\d{1,4})\s*([a-zA-Z]{0,3})\s*[.)](?=[^\d\s.,/])(.+)$")  # «3.Pinocchio»
RX_STIME = re.compile(r"^\s*(?P<stime>\d[\d.,]*\s*/\s*\d[\d.,]*|\d[\d.,]*)?\s*")
RX_RISERVA = re.compile(r"\bR\.?\s*(\d[\d.,]*)", re.I)
RX_PREZZO_SENZA_EURO = re.compile(r"^\s*\d[\d.,]*\s*/\s*\d[\d.,]*\b")


def _parole(t: str) -> list[str]:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9 ]+", " ", t.upper()).split()


def _euro(s):
    if not s:
        return None
    s = s.strip().replace(" ", "")
    if "," in s or re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "").replace(",", ".")
    return s


def _lotto(t: str):
    return RX_LOTTO.match(t) or RX_LOTTO_ATTACCATO.match(t)


RX_PREFISSO_PREZZO = re.compile(r"^(?P<pre>[^€\d]{1,25}?)\s*(?=€\s*\d)")  # «La coppia € 150/350»


def _e_prezzo(t: str) -> bool:
    return t.startswith("€") or bool(RX_PREZZO_SENZA_EURO.match(t)) or bool(RX_PREFISSO_PREZZO.match(t))


def leggi_prezzo(t: str) -> dict:
    """«€ 90/200 R100 ROSSI DA FOTOGRAFARE», «€100/200 BIANCHI R.150», «150/300 VERDI»."""
    t = t.strip()
    pre = RX_PREFISSO_PREZZO.match(t)
    prefisso = pre.group("pre").strip() if pre else ""
    t = t[pre.end():] if pre else t
    t = t.lstrip("€").strip()
    m = RX_STIME.match(t)
    stime, resto = (m.group("stime"), t[m.end():]) if m else (None, t)
    ris = RX_RISERVA.search(resto)
    if ris:
        resto = (resto[:ris.start()] + " " + resto[ris.end():]).strip()
    return {"stime": stime, "riserva": ris.group(1) if ris else None, "resto": " ".join(resto.split()),
            "prefisso": prefisso}


def _e_intestazione(t: str, grassetto: bool) -> bool:
    if not t or _e_prezzo(t) or _lotto(t):
        return False
    lettere = [c for c in t if c.isalpha()]
    maiuscolo = lettere and all(c.isupper() for c in lettere)
    return bool(maiuscolo and grassetto)


def _righe_documento(doc):
    """Ogni riga del Word con il suo grassetto; un «a capo» dentro il paragrafo vale come riga nuova."""
    for p in doc.paragraphs:
        grassetto = any(r.bold for r in p.runs if r.text.strip())
        for t in p.text.split("\n"):
            if t.strip():
                yield t.strip(), grassetto


def _e_riga_maiuscola(t: str) -> bool:
    lettere = [c for c in t if c.isalpha()]
    return bool(lettere) and all(c.isupper() for c in lettere) and len(t) < 80


def separa_mandante_e_note(resto: str) -> tuple[str, str]:
    resto = (resto or "").strip()
    m = INIZIO_NOTE.search(resto)
    if m:
        return resto[:m.start()].strip(" -–,;"), resto[m.start():].strip()
    return resto, ""


class Riconoscitore:
    """Abbina il testo del mandante scritto nel catalogo (es. «ROSSI») alle schede dell'anagrafica."""

    def __init__(self, solo_mandanti=True):
        qs = Soggetto.objects.filter(is_mandante=True) if solo_mandanti else Soggetto.objects.all()
        self.mandanti = [(s, set(_parole(s.nome))) for s in qs]

    def candidati(self, testo: str) -> list:
        parole = set(_parole(testo))
        return [s for s, nome in self.mandanti if parole and parole <= nome]  # tutte le parole del testo nel nome

    def trova(self, testo: str):
        parole = set(_parole(testo))
        if not parole:
            return None
        candidati = self.candidati(testo)
        if len(candidati) == 1:
            return candidati[0]
        esatti = [s for s, nome in self.mandanti if parole == nome]
        if len(esatti) == 1:
            return esatti[0]
        mandanti = [s for s in candidati if s.is_mandante]  # tra più schede, quella che è già mandante
        return mandanti[0] if len(mandanti) == 1 else None

    @staticmethod
    def mandato_di(soggetto, asta=None):
        if not soggetto:
            return None
        qs = Mandato.objects.filter(mandante=soggetto)
        if asta is not None:
            usato = qs.filter(lotti__asta=asta).first()
            if usato:
                return usato
        return qs.order_by("-data").first()


def leggi_catalogo_word(contenuto: bytes, asta, primo_numero: int) -> list[dict]:
    import docx
    doc = docx.Document(io.BytesIO(contenuto))
    ric = Riconoscitore()
    lotti, sezione, escluso, tornata = [], "", False, None
    corrente = None

    def chiudi():
        nonlocal corrente
        if corrente:
            corrente["descrizione"] = "\n".join(corrente.pop("_righe")).strip()
            sotto = corrente.pop("sottotitolo")
            if sotto and _e_riga_maiuscola(corrente["titolo"]):  # titolo = autore, sottotitolo = opera
                corrente["illustratore"], corrente["titolo"] = corrente["titolo"], sotto
            elif sotto:
                corrente["titolo"] = f"{corrente['titolo']} – {sotto}"
            lotti.append(corrente)
        corrente = None

    for t, grassetto in _righe_documento(doc):
        mt = RX_TORNATA.search(t)
        if mt and len(t) < 80 and not _e_prezzo(t):
            chiudi()
            n = ORDINALI[mt.group(2).upper()]
            tornata = n + 1 if mt.group(1) else n
            continue
        m = _lotto(t)
        # «1991. Raccolta di 13 litografie…» dentro una descrizione non è un lotto nuovo: i titoli sono in grassetto
        if m and not grassetto and corrente is not None and not corrente["riga_prezzo"]:
            m = None
        if m:
            chiudi()
            corrente = {"numero_doc": m.group(1), "bis": m.group(2), "titolo": m.group(3).strip(), "_righe": [],
                        "sezione": sezione, "escluso": escluso, "tornata": tornata, "stima": None, "base": None,
                        "riserva": None, "mandante_testo": "", "note": [], "da_fotografare": False,
                        "riga_prezzo": "", "sottotitolo": "", "illustratore": ""}
            continue
        maiuscola = _e_riga_maiuscola(t) and not _e_prezzo(t)
        # riga in grassetto subito sotto il titolo: sottotitolo («155. HUGO PRATT» / «…Et in Helvetia Corto»)
        if corrente is not None and grassetto and not corrente["_righe"] and not corrente["riga_prezzo"] \
                and not corrente["sottotitolo"]:
            corrente["sottotitolo"] = t
            continue
        # titolo di sezione: maiuscolo e grassetto, oppure maiuscolo dopo un lotto già completo (con la riga €)
        if _e_intestazione(t, grassetto) or (maiuscola and (corrente is None or corrente["riga_prezzo"])):
            chiudi()
            if any(re.search(rf"\b{x}\b", t.upper()) for x in SEZIONI_ESCLUSE):
                escluso = True
            if not escluso:
                sezione = t
            continue
        if corrente is None:
            continue
        mp = leggi_prezzo(t) if _e_prezzo(t) else None
        if mp or (maiuscola and corrente["_righe"]):
            # riga del prezzo (€ base/stima R riserva MANDANTE note) o riga maiuscola di mandante/note
            stime, ris, resto = (mp["stime"], mp["riserva"], mp["resto"]) if mp else (None, None, t)
            if stime:
                parti = [x.strip() for x in stime.split("/")]
                corrente["base"] = _euro(parti[0])
                corrente["stima"] = _euro(parti[1]) if len(parti) > 1 else None
            if ris:
                corrente["riserva"] = _euro(ris)
            if mp and mp["prefisso"]:
                corrente["note"].append(f"prezzo: {mp['prefisso']}")
            mand, note = separa_mandante_e_note(resto)
            if mand and not corrente["mandante_testo"]:
                corrente["mandante_testo"] = mand
            elif mand:
                note = f"{mand} {note}".strip()
            if note:
                corrente["note"].append(note)
            corrente["riga_prezzo"] = t
            continue
        corrente["_righe"].append(t)
    chiudi()

    espliciti = [int(l["numero_doc"]) for l in lotti if not l["escluso"] and l["numero_doc"].strip("0")]
    numero = max([primo_numero - 1] + espliciti) + 1
    for l in lotti:
        if l["escluso"]:
            l["numero"] = ""
        elif l["numero_doc"].strip("0") == "":
            l["numero"] = str(numero)
            numero += 1
        else:
            l["numero"] = l["numero_doc"].lstrip("0") + l["bis"]
        note = " ".join(l["note"])
        l["note_interne"] = note[:250]
        l["da_fotografare"] = bool(re.search(r"FOTO", note, re.I))
        sogg = ric.trova(l["mandante_testo"])
        parole = l["mandante_testo"].split()
        for k in range(len(parole) - 1, 0, -1):
            if sogg:
                break
            sogg = ric.trova(" ".join(parole[:k]))
            if sogg:
                l["note_interne"] = (" ".join(parole[k:]) + " " + l["note_interne"]).strip()[:250]
                l["mandante_testo"] = " ".join(parole[:k])
        mandato = Riconoscitore.mandato_di(sogg, asta)
        l["mandato_id"] = mandato.pk if mandato else None
        l["mandato"] = f"{sogg.codice} · {sogg.nome} · {mandato.numero}" if mandato else ""
        l["avvisi"] = []
        if not l["base"]:
            l["avvisi"].append("manca la base/stima")
        if not l["mandante_testo"]:
            l["avvisi"].append("manca il mandante")
        elif not mandato:
            l["avvisi"].append(f"mandante «{l['mandante_testo']}» da abbinare" if not sogg else
                               f"{sogg.nome}: nessun mandato")
    return lotti
