from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import get_settings


def _derive_aes_key() -> bytes:
    settings = get_settings()
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"kinetiq-studio-v1",
        info=b"oauth-token-encryption",
    )
    return hkdf.derive(settings.encryption_master_key.encode("utf-8"))


def encrypt_secret(plaintext: str) -> str:
    key = _derive_aes_key()
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.urlsafe_b64encode(nonce + ct).decode("ascii")


def decrypt_secret(token: str) -> str:
    key = _derive_aes_key()
    raw = base64.urlsafe_b64decode(token.encode("ascii"))
    nonce, ct = raw[:12], raw[12:]
    return AESGCM(key).decrypt(nonce, ct, None).decode("utf-8")


def generate_token(prefix: str = "evs") -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def sign_media_token(asset_id: str, ttl_seconds: int = 600) -> str:
    settings = get_settings()
    exp = int(time.time()) + ttl_seconds
    msg = f"{asset_id}:{exp}".encode("utf-8")
    sig = hmac.new(settings.media_signing_secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def verify_media_token(asset_id: str, token: str) -> bool:
    settings = get_settings()
    try:
        exp_str, sig = token.split(".", 1)
        exp = int(exp_str)
    except ValueError:
        return False
    if exp < int(time.time()):
        return False
    msg = f"{asset_id}:{exp}".encode("utf-8")
    expected = hmac.new(settings.media_signing_secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)
