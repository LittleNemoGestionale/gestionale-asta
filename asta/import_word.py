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

SEZIONI_ESCLUSE = ("DA INSERIRE", "PROSSIME ASTE", "VENDITA DIRETTA")
# parole che indicano note di lavoro nella riga del prezzo (da qui in poi non è più il mandante)
INIZIO_NOTE = re.compile(r"\b(DA\s+FOTOGRAFARE|FOTOGRAFARE|FOTO\s+DA\s+FARE|DA\s+ABBINARE|VALORE\s+ESIGUO|"
                         r"RISTAMPONE|FAR\s+VEDERE|DA\s+ACCORPARE|DA\s+VERIFICARE|DA\s+CONTROLLARE)\b|\(", re.I)
RX_LOTTO = re.compile(r"^\s*(\d{1,4})\s*([a-zA-Z]{0,3})\s*[.)]\s+(.+)$")
RX_PREZZO = re.compile(r"^\s*€\s*(?P<stime>[\d.,]+\s*/\s*[\d.,]+|[\d.,]+)?\s*(?:R\s*(?P<ris>[\d.,]+))?\s*(?P<resto>.*)$", re.I)


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


def _e_intestazione(p) -> bool:
    t = p.text.strip()
    if not t or t.startswith("€") or RX_LOTTO.match(t):
        return False
    lettere = [c for c in t if c.isalpha()]
    maiuscolo = lettere and all(c.isupper() for c in lettere)
    grassetto = any(r.bold for r in p.runs if r.text.strip())
    return bool(maiuscolo and grassetto)


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

    def __init__(self):
        self.mandanti = [(s, set(_parole(s.nome))) for s in Soggetto.objects.filter(is_mandante=True)]

    def trova(self, testo: str):
        parole = set(_parole(testo))
        if not parole:
            return None
        candidati = [s for s, nome in self.mandanti if parole <= nome]  # tutte le parole del testo nel nome
        if len(candidati) == 1:
            return candidati[0]
        esatti = [s for s, nome in self.mandanti if parole == nome]
        return esatti[0] if len(esatti) == 1 else None

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
    lotti, sezione, escluso = [], "", False
    corrente = None

    def chiudi():
        nonlocal corrente
        if corrente:
            corrente["descrizione"] = "\n".join(corrente.pop("_righe")).strip()
            lotti.append(corrente)
        corrente = None

    for p in doc.paragraphs:
        t = p.text.strip()
        if not t:
            continue
        m = RX_LOTTO.match(t)
        if m:
            chiudi()
            corrente = {"numero_doc": m.group(1), "bis": m.group(2), "titolo": m.group(3).strip(), "_righe": [],
                        "sezione": sezione, "escluso": escluso, "stima": None, "base": None,
                        "riserva": None, "mandante_testo": "", "note": [], "da_fotografare": False,
                        "riga_prezzo": ""}
            continue
        if _e_intestazione(p):
            chiudi()
            if any(x in t.upper() for x in SEZIONI_ESCLUSE):
                escluso = True
            if not escluso:
                sezione = t
            continue
        if corrente is None:
            continue
        mp = RX_PREZZO.match(t) if t.startswith("€") else None
        if mp or (_e_riga_maiuscola(t) and corrente["_righe"]):
            # riga del prezzo (€ base/stima R riserva MANDANTE note) o riga maiuscola di mandante/note
            stime, ris, resto = (mp.group("stime"), mp.group("ris"), mp.group("resto")) if mp else (None, None, t)
            if stime:
                parti = [x.strip() for x in stime.split("/")]
                corrente["base"] = _euro(parti[0])
                corrente["stima"] = _euro(parti[1]) if len(parti) > 1 else None
            if ris:
                corrente["riserva"] = _euro(ris)
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

    numero = primo_numero
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
