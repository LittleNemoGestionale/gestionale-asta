"""Lettura del PDF «Lotti Disponibili / Valore Agg.» stampato dal vecchio gestionale (aggiudicazioni di un'asta)."""
import io
import re
from decimal import Decimal

NUM = r"\d{1,3}(?:\.\d{3})*,\d\d"
SALTA = re.compile(r"Asta N\.|Numero\s+Illustratore|NON AGGIUDICATI|SPESE DI SPEDIZIONE", re.I)


def _euro(s: str) -> Decimal:
    return Decimal(s.replace(".", "").replace(",", "."))


def testo_pdf(contenuto: bytes) -> str:
    import pypdf
    return "\n".join(p.extract_text(extraction_mode="layout") for p in pypdf.PdfReader(io.BytesIO(contenuto)).pages)


def leggi_righe(testo: str) -> list[dict]:
    """[{numero, titolo, base, aggiudicato}] — il secondo lotto con lo stesso numero diventa «NNNbis»;
    le righe andate a capo (titolo lungo, importi sotto) vengono riunite."""
    lotti = []
    for riga in testo.splitlines():
        if not riga.strip() or SALTA.search(riga):
            continue
        importi = [_euro(x) for x in re.findall(NUM, riga)]
        resto = re.sub(NUM, "   ", riga)
        m = re.match(r"^\s*(\d{1,4})\s{2,}(.*)$", resto)
        ultimo = int(re.match(r"\d+", lotti[-1]["numero"]).group()) if lotti else 0
        if m and (not lotti or int(m.group(1)) in (ultimo, ultimo + 1)):
            numero = m.group(1)
            if lotti and re.match(r"\d+", lotti[-1]["numero"]).group() == numero:
                numero += "bis"
            parti = [t.strip() for t in re.split(r"\s{3,}", m.group(2).strip()) if t.strip()]
            lotti.append({"numero": numero, "titolo": " ".join(parti), "base": importi[0] if importi else None,
                          "aggiudicato": importi[1] if len(importi) > 1 else None})
        elif lotti:
            l = lotti[-1]
            extra = " ".join(t.strip() for t in re.split(r"\s{3,}", resto.strip()) if t.strip())
            if extra:
                l["titolo"] = f"{l['titolo']} {extra}".strip()
            for v in importi:
                if l["base"] is None:
                    l["base"] = v
                else:
                    l["aggiudicato"] = v
    return lotti
