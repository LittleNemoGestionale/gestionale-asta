"""Email del gestionale: bozze per Outlook (.eml) oppure invio diretto, se è configurato.

Invio diretto, due modi (in dati/impostazioni.ini):
  • Microsoft 365 (consigliato): ASTA_M365_TENANT_ID, ASTA_M365_CLIENT_ID, ASTA_M365_CLIENT_SECRET,
    ASTA_M365_MITTENTE (la casella da cui partono, es. aste@…). Usa Microsoft Graph: niente password della
    casella, e le email inviate compaiono in «Posta inviata» di Outlook.
  • SMTP classico (altri fornitori): ASTA_SMTP_HOST, ASTA_SMTP_PORT, ASTA_SMTP_UTENTE, ASTA_SMTP_PASSWORD.
Senza questi dati il programma prepara solo le bozze da aprire in Outlook.
"""
import base64
import json
import urllib.parse
import urllib.request
from email.utils import getaddresses

from django.conf import settings
from django.core.mail import EmailMultiAlternatives


def m365_attivo() -> bool:
    return all([settings.ASTA_M365_TENANT_ID, settings.ASTA_M365_CLIENT_ID, settings.ASTA_M365_CLIENT_SECRET,
                settings.ASTA_M365_MITTENTE])


def invio_diretto_attivo() -> bool:
    return m365_attivo() or bool(settings.ASTA_SMTP_HOST)


def _http_json(url, dati: bytes, intestazioni: dict) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=dati, headers=intestazioni, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            corpo = r.read()
            return r.status, (json.loads(corpo) if corpo else {})
    except urllib.error.HTTPError as e:
        corpo = e.read()
        try:
            dettaglio = json.loads(corpo)
        except ValueError:
            dettaglio = {"errore": corpo.decode(errors="replace")[:300]}
        return e.code, dettaglio


_token = {"valore": None, "scade": 0}


def _token_m365() -> str:
    import time
    if _token["valore"] and _token["scade"] > time.time() + 60:
        return _token["valore"]
    url = f"https://login.microsoftonline.com/{settings.ASTA_M365_TENANT_ID}/oauth2/v2.0/token"
    dati = urllib.parse.urlencode({"client_id": settings.ASTA_M365_CLIENT_ID,
                                   "client_secret": settings.ASTA_M365_CLIENT_SECRET,
                                   "scope": "https://graph.microsoft.com/.default",
                                   "grant_type": "client_credentials"}).encode()
    stato, r = _http_json(url, dati, {"Content-Type": "application/x-www-form-urlencoded"})
    if stato != 200 or "access_token" not in r:
        raise RuntimeError("accesso a Microsoft 365 non riuscito: " + str(r.get("error_description") or r)[:200])
    _token.update(valore=r["access_token"], scade=time.time() + int(r.get("expires_in", 3600)))
    return _token["valore"]


def _invia_m365(em: EmailMultiAlternatives) -> None:
    html = next((c for c, tipo in em.alternatives if tipo == "text/html"), None)
    allegati = []
    for a in em.attachments:
        nome, contenuto, tipo = (a.filename, a.content, a.mimetype) if hasattr(a, "filename") else a
        if isinstance(contenuto, str):
            contenuto = contenuto.encode()
        allegati.append({"@odata.type": "#microsoft.graph.fileAttachment", "name": nome,
                         "contentType": tipo or "application/octet-stream",
                         "contentBytes": base64.b64encode(contenuto).decode()})
    messaggio = {
        "subject": em.subject,
        "body": {"contentType": "HTML" if html else "Text", "content": html or em.body},
        "toRecipients": [{"emailAddress": {"name": n, "address": a}} for n, a in getaddresses(em.to)],
        "attachments": allegati,
    }
    url = f"https://graph.microsoft.com/v1.0/users/{urllib.parse.quote(settings.ASTA_M365_MITTENTE)}/sendMail"
    stato, r = _http_json(url, json.dumps({"message": messaggio, "saveToSentItems": True}).encode(),
                          {"Authorization": f"Bearer {_token_m365()}", "Content-Type": "application/json"})
    if stato != 202:
        errore = (r.get("error") or {}).get("message") if isinstance(r.get("error"), dict) else r
        raise RuntimeError(f"Microsoft 365 non ha accettato il messaggio ({stato}): {errore}")


def connessione():
    """Connessione alla casella configurata (creata direttamente, indipendente da MAILERS di Django)."""
    from django.utils.module_loading import import_string
    return import_string(settings.ASTA_EMAIL_BACKEND)(
        host=settings.ASTA_SMTP_HOST, port=settings.ASTA_SMTP_PORT, username=settings.ASTA_SMTP_UTENTE,
        password=settings.ASTA_SMTP_PASSWORD, use_tls=settings.ASTA_SMTP_PORT == 587,
        use_ssl=settings.ASTA_SMTP_PORT == 465, timeout=30, fail_silently=False)


def costruisci(oggetto, nome, indirizzo, testo, html, allegati=()) -> EmailMultiAlternatives:
    from email.utils import formataddr
    mittente = formataddr((settings.CASA_ASTA_NOME, settings.CASA_ASTA_EMAIL)) if settings.CASA_ASTA_EMAIL else None
    em = EmailMultiAlternatives(subject=oggetto, body=testo, from_email=mittente,
                                to=[formataddr((nome, indirizzo))] if indirizzo else [])
    em.attach_alternative(html, "text/html")
    for nome_file, dati, tipo in allegati:
        em.attach(nome_file, dati, tipo)
    return em


def come_eml(em: EmailMultiAlternatives) -> bytes:
    """Bozza per Outlook: con X-Unsent Outlook la apre come nuovo messaggio, pronto da inviare."""
    msg = em.message()
    if not settings.CASA_ASTA_EMAIL:  # in Outlook il mittente è la casella di chi invia
        del msg["From"]
    msg["X-Unsent"] = "1"
    return msg.as_bytes()


def invia(em: EmailMultiAlternatives) -> None:
    if not em.to:
        raise ValueError("manca l'indirizzo email del destinatario")
    if m365_attivo():
        _invia_m365(em)
        return
    if connessione().send_messages([em]) != 1:
        raise RuntimeError("il server di posta non ha accettato il messaggio")
