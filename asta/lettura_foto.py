"""Lettura con Claude della tabella degli oggetti fotografata dal modulo del mandato.

Riceve SOLO il ritaglio della tabella scelto a mano sulla foto (niente riquadro con nome,
nascita, indirizzo, IBAN): i dati personali non lasciano il computer dell'ufficio.
La chiave si mette in dati/impostazioni.ini:  ANTHROPIC_API_KEY = sk-ant-...
"""
import base64
import json
import os
from datetime import datetime

from django.conf import settings

MODELLO = "claude-opus-5-5"
SCHEMA = {
    "type": "object",
    "properties": {
        "righe": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "quantita": {"type": "string"},
                    "descrizione": {"type": "string"},
                    "riserva": {"type": "string"},
                    "incerta": {"type": "boolean"},
                    "dubbio": {"type": "string"},
                },
                "required": ["quantita", "descrizione", "riserva", "incerta", "dubbio"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["righe"],
    "additionalProperties": False,
}
ISTRUZIONI = """Questa è la foto (ritagliata) della tabella degli oggetti di un mandato di vendita di una casa d'aste
italiana di fumetti, manifesti e illustrazione. Colonne tipiche: Q.tà, Codice, Descrizione, Prezzo minimo di riserva.
Trascrivi ogni riga compilata, nell'ordine, così com'è scritta a mano:
- le virgolette di ripetizione (" " o 〃) significano «come la riga sopra»: sostituiscile con il testo che ripetono;
- scrivi la descrizione in modo leggibile (maiuscole e minuscole normali), senza aggiungere informazioni;
- riserva: solo il numero in euro, vuoto se manca; quantita: il numero, vuoto se manca;
- se i prezzi sembrano spostati di riga o una parola non si legge bene, metti incerta=true e spiega in «dubbio»;
- non inventare righe: salta le righe vuote."""


def attiva() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _registro(msg: str):
    with open(settings.DATI_DIR / "claude.log", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%d/%m/%Y %H:%M} {msg}\n")


def leggi_tabella(immagine: bytes, media_type: str = "image/jpeg") -> list[dict]:
    import anthropic
    client = anthropic.Anthropic()  # legge ANTHROPIC_API_KEY
    try:
        risposta = client.beta.messages.create(
            model=MODELLO,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                             "data": base64.standard_b64encode(immagine).decode()}},
                {"type": "text", "text": ISTRUZIONI},
            ]}],
        )
    except anthropic.AuthenticationError:
        raise RuntimeError("la chiave di Claude non è valida: controlla ANTHROPIC_API_KEY in impostazioni.ini")
    except anthropic.RateLimitError:
        raise RuntimeError("troppe richieste a Claude in poco tempo: riprova tra un minuto")
    except anthropic.APIConnectionError:
        raise RuntimeError("nessun collegamento a internet o a Claude")
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"Claude ha risposto con un errore ({e.status_code}): riprova più tardi")
    u = risposta.usage
    _registro(f"lettura foto: {u.input_tokens} token in, {u.output_tokens} token out")  # solo i conteggi, mai il contenuto
    if risposta.stop_reason == "refusal":
        raise RuntimeError("Claude non ha letto questa immagine: prova con un ritaglio più preciso della tabella")
    testo = next((b.text for b in risposta.content if b.type == "text"), "")
    return json.loads(testo)["righe"]
