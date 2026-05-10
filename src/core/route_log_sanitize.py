"""Redact / truncate payloads before persisting to request_logs."""

from __future__ import annotations

import json
from typing import Any

_MAX_STR = 2048
_KEY_SUBSTR_REDACT = ("base64", "embedding", "dataurl", "session_token", "access_token")


def _truncate_str(s: str) -> str:
    if len(s) <= _MAX_STR:
        return s
    return s[: _MAX_STR - 48] + f"...[truncated len={len(s)}]"


def sanitize_for_request_log(value: Any, depth: int = 0) -> Any:
    if depth > 28:
        return "<max_depth>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _truncate_str(value)
    if isinstance(value, bytes):
        return f"<bytes len={len(value)}>"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            ks = str(k)
            lk = ks.lower()
            if any(tok in lk for tok in _KEY_SUBSTR_REDACT):
                out[ks] = "<redacted>"
                continue
            out[ks] = sanitize_for_request_log(v, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        cap = 120
        seq = [sanitize_for_request_log(x, depth + 1) for x in value[:cap]]
        if len(value) > cap:
            seq.append(f"<... {len(value) - cap} more items>")
        return seq
    return _truncate_str(str(value))


def dumps_for_request_log(obj: Any) -> str:
    try:
        return json.dumps(sanitize_for_request_log(obj), ensure_ascii=False)
    except Exception:
        return json.dumps({"error": "serialization_failed"}, ensure_ascii=False)
