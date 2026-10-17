"""Twilio webhook signatures (`X-Twilio-Signature`).

signature = base64(HMAC-SHA1(auth_token, url + concat(sorted(key + value for form params))))

`url` is the public URL Twilio called (configured, not taken from the request, so proxies and
host headers cannot change it). Comparison is constant-time.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Iterable


def compute_signature(url: str, params: Iterable[tuple[str, str]], auth_token: str) -> str:
    payload = url + "".join(k + v for k, v in sorted(params, key=lambda kv: (kv[0], kv[1])))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def is_valid(
    url: str, params: Iterable[tuple[str, str]], signature: str | None, auth_token: str
) -> bool:
    if not signature or not auth_token:
        return False
    expected = compute_signature(url, params, auth_token)
    # Bytes, not str: compare_digest raises on non-ASCII str (a 500 instead of a 403).
    return hmac.compare_digest(expected.encode(), signature.encode("utf-8", "replace"))
