"""Publica el panel web estático en docs/ (GitHub Pages) con los datos cifrados.

GitHub Pages es público, así que los datos se cifran con AES-GCM usando una clave derivada
(PBKDF2-SHA256) de SITE_PASSWORD. El navegador los descifra con WebCrypto al introducir la contraseña.
"""
import base64
import json
import os
import shutil
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ITERATIONS = 250_000


def encrypt(payload: dict, password: str) -> dict:
    salt, iv = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERATIONS).derive(password.encode())
    ct = AESGCM(key).encrypt(iv, json.dumps(payload, ensure_ascii=False).encode(), None)
    b64 = lambda b: base64.b64encode(b).decode()
    return {"v": 1, "iter": ITERATIONS, "salt": b64(salt), "iv": b64(iv), "ct": b64(ct)}


def publish(root: Path, listings: list[dict], meta: dict, password: str):
    docs = root / "docs"
    docs.mkdir(exist_ok=True)
    shutil.copyfile(root / "web" / "index.html", docs / "index.html")
    (docs / ".nojekyll").touch()
    payload = {"meta": meta, "listings": listings}
    (docs / "data.enc.json").write_text(json.dumps(encrypt(payload, password)), encoding="utf-8")
