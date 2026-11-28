"""Encrypting a tenant's file with its data key (ADR 0028).

    file = MAGIC (5 bytes) | nonce (12 bytes) | AES-256-GCM ciphertext and tag

The object's name is the GCM associated data, so a file cannot be moved to another tenant's or day's path
and still decrypt. Without the tenant's data key (deleted with the tenant) the file is unreadable.
"""
from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"RFLE1"        # Riffle encrypted file, format 1
NONCE = 12
KEY_BYTES = 32


def encrypt(data: bytes, key: bytes, object_name: str) -> bytes:
    nonce = os.urandom(NONCE)
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, data, object_name.encode())


def decrypt(blob: bytes, key: bytes, object_name: str) -> bytes:
    """Raises ValueError for a file in another format, and cryptography's InvalidTag for a wrong key, a
    changed file or a file read under another name."""
    if not blob.startswith(MAGIC):
        raise ValueError("not a Riffle encrypted file")
    nonce = blob[len(MAGIC):len(MAGIC) + NONCE]
    return AESGCM(key).decrypt(nonce, blob[len(MAGIC) + NONCE:], object_name.encode())


def new_key() -> bytes:
    return AESGCM.generate_key(bit_length=256)
