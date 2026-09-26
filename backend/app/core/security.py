"""密钥加解密：模型 API Key 以 Fernet 密文落库，主密钥来自 .env 的 SECRET_KEY。"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def generate_secret_key() -> str:
    return Fernet.generate_key().decode()


@lru_cache
def _fernet() -> Fernet:
    """SECRET_KEY 缺失时用固定口令派生（仅本地开发），避免启动即崩。"""
    raw = settings.secret_key.strip()
    if not raw:
        derived = hashlib.sha256(b"resume-roaster-local-dev-secret").digest()
        return Fernet(base64.urlsafe_b64encode(derived))
    key = raw.encode()
    try:
        return Fernet(key)
    except ValueError:
        derived = hashlib.sha256(key).digest()
        return Fernet(base64.urlsafe_b64encode(derived))


def encrypt(plain: str) -> str:
    if not plain:
        return ""
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(cipher: str) -> str:
    if not cipher:
        return ""
    try:
        return _fernet().decrypt(cipher.encode()).decode()
    except InvalidToken:
        return ""


def mask(secret: str) -> str:
    """前端展示用掩码：sk-****abcd"""
    if not secret:
        return ""
    if len(secret) <= 8:
        return "****"
    return f"{secret[:3]}****{secret[-4:]}"
