"""Cifratura a livello di campo per i dati personali (GDPR).

- EncryptedTextField: il valore è salvato cifrato (Fernet/AES) nel database;
  viene decifrato solo in memoria, quando il codice lo legge.
- blind_index(): impronta HMAC per cercare (es. "esiste già questo CF?")
  senza dover decifrare tutta la tabella.

Le chiavi stanno in variabili d'ambiente (mai nel codice o nel database):
  ASTA_FIELD_KEY  -> chiave Fernet
  ASTA_INDEX_KEY  -> chiave HMAC per gli indici di ricerca
"""
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models


def _fernet():
    return Fernet(settings.ASTA_FIELD_KEY.encode())


def cifra_bytes(dati: bytes) -> bytes:
    """Per i file allegati (foto dei mandati firmati, documenti): sul disco restano cifrati."""
    return _fernet().encrypt(dati)


def decifra_bytes(dati: bytes) -> bytes:
    return _fernet().decrypt(dati)


def blind_index(value: str) -> str:
    if not value:
        return ""
    norm = "".join(value.split()).upper()
    return hmac.new(settings.ASTA_INDEX_KEY.encode(), norm.encode(), hashlib.sha256).hexdigest()


class EncryptedTextField(models.TextField):
    def from_db_value(self, value, expression, connection):
        if not value:
            return value
        try:
            return _fernet().decrypt(value.encode()).decode()
        except InvalidToken:
            return "[non decifrabile]"

    def get_prep_value(self, value):
        if not value:
            return value
        return _fernet().encrypt(str(value).encode()).decode()
