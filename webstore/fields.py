"""Column-level encryption for sensitive secrets.

Backed by `cryptography.fernet` (AES-128-CBC + HMAC-SHA256). The encryption
key is derived once at process start from settings.FIELD_ENCRYPTION_KEY
(or settings.SECRET_KEY as a fallback) by SHA-256-hashing into Fernet's
required 32-byte form.

Stored ciphertext is opaque base64 — a DB snapshot reveals nothing
useful without the runtime key. Decryption only happens on attribute
access in Python; raw SQL queries can't filter by plaintext value.
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models


def _derive_key() -> bytes:
    secret = (
        getattr(settings, "FIELD_ENCRYPTION_KEY", None)
        or settings.SECRET_KEY
    )
    if not secret:
        raise RuntimeError(
            "FIELD_ENCRYPTION_KEY (or SECRET_KEY) must be set to encrypt fields."
        )
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


_fernet: Fernet | None = None


def _cipher() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_derive_key())
    return _fernet


class EncryptedCharField(models.CharField):
    """Stores text encrypted at rest. Compatible with .save() and admin edits.

    Limitations:
      - Cannot be used in `filter(field__exact=...)` since the DB value is
        ciphertext that changes each save (Fernet uses a random IV). To
        look up a row by an encrypted value, query by another field
        (e.g. a public_id) first.
      - `db_index=True` is a no-op for lookups but still costs storage.
    """
    description = "Encrypted CharField (Fernet)"

    def __init__(self, *args, **kwargs):
        # Encrypted blobs are larger than plaintext; default to 1024 so
        # callers don't have to remember.
        kwargs.setdefault("max_length", 1024)
        super().__init__(*args, **kwargs)

    def from_db_value(self, value, expression, connection):
        if value in (None, ""):
            return value
        try:
            return _cipher().decrypt(value.encode("utf-8")).decode("utf-8")
        except (InvalidToken, ValueError):
            # Either the row was written before encryption was enabled
            # or the key has changed. Return as-is so the caller can
            # surface a "re-enter" UX.
            return value

    def to_python(self, value):
        # Called when constructing from a form or .clean(). Treat as plain
        # text; encryption happens at write time in get_prep_value.
        if value is None:
            return value
        return str(value)

    def get_prep_value(self, value):
        if value in (None, ""):
            return value
        token = _cipher().encrypt(str(value).encode("utf-8"))
        return token.decode("utf-8")
