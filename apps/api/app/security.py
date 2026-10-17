"""Authentication: X-API-Key for the public API, signed cookie for the web UI."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Optional

SESSION_COOKIE = "fv_session"
SESSION_TTL = 7 * 24 * 3600


def _sign(secret: str, payload: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def create_session(secret: str, key_id: str, ttl: int = SESSION_TTL) -> str:
    body = json.dumps({"k": key_id, "exp": int(time.time()) + ttl}).encode("utf-8")
    token = base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")
    return f"{token}.{_sign(secret, body)}"


def verify_session(secret: str, token: Optional[str]) -> Optional[str]:
    if not token or "." not in token:
        return None
    encoded, signature = token.rsplit(".", 1)
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        body = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(_sign(secret, body), signature):
        return None
    try:
        data = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        return None
    if int(data.get("exp", 0)) < int(time.time()):
        return None
    return str(data.get("k") or "")


def check_api_key(provided: Optional[str], accepted: list[str]) -> Optional[str]:
    """Return an opaque key id when ``provided`` matches an accepted key."""

    if not provided or not accepted:
        return None
    for index, key in enumerate(accepted):
        if hmac.compare_digest(provided.strip(), key):
            return f"key{index + 1}"
    return None


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
