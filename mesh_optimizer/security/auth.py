"""HMAC-SHA256 token generation and verification for agent-controller auth."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Dict, Optional, Sequence

logger = logging.getLogger(__name__)

# Paths that never require authentication
DEFAULT_EXEMPT_PATHS = ("/health", "/docs", "/openapi.json")


def generate_token(
    node_id: str,
    secret: str,
    ttl_hours: int = 24,
    role: str = "node",
    extra: Optional[Dict] = None,
) -> str:
    """Create an HMAC-SHA256 signed token with expiry.

    Format: base64(json_payload).base64(signature)
    """
    payload = {
        "node_id": node_id,
        "role": role,
        "iat": int(time.time()),
        "exp": int(time.time()) + ttl_hours * 3600,
    }
    if extra:
        payload.update(extra)

    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode()

    sig = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode()

    return f"{payload_b64}.{sig_b64}"


def verify_token(token: str, secret: str) -> dict:
    """Verify an HMAC-SHA256 token and return its payload.

    Raises ValueError on invalid or expired tokens.
    """
    parts = token.split(".")
    if len(parts) != 2:
        raise ValueError("Malformed token")

    payload_b64, sig_b64 = parts

    try:
        payload_bytes = base64.urlsafe_b64decode(payload_b64)
        provided_sig = base64.urlsafe_b64decode(sig_b64)
    except Exception:
        raise ValueError("Malformed token encoding")

    expected_sig = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).digest()
    if not hmac.compare_digest(provided_sig, expected_sig):
        raise ValueError("Invalid token signature")

    try:
        payload = json.loads(payload_bytes)
    except json.JSONDecodeError:
        raise ValueError("Corrupt token payload")

    exp = payload.get("exp", 0)
    if exp and time.time() > exp:
        raise ValueError("Token expired")

    return payload
