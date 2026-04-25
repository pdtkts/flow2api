import hashlib
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
import jwt
from jwt import InvalidTokenError

from .config import Settings


@dataclass
class KeygenIdentity:
    subject: str
    machine_id: str
    license_id: str
    account_id: str
    raw_claims: dict[str, Any]


def _normalize_subject(claims: dict[str, Any]) -> str:
    # Prefer machine id then license id; fallback to sub.
    for k in ("machine", "machine_id", "machineId"):
        v = claims.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    for k in ("license", "license_id", "licenseId"):
        v = claims.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    sub = claims.get("sub")
    return str(sub or "").strip()


def _extract_identity(claims: dict[str, Any]) -> KeygenIdentity:
    subject = _normalize_subject(claims)
    if not subject:
        raise ValueError("keygen token missing subject")
    machine_id = str(
        claims.get("machine")
        or claims.get("machine_id")
        or claims.get("machineId")
        or ""
    ).strip()
    license_id = str(
        claims.get("license")
        or claims.get("license_id")
        or claims.get("licenseId")
        or ""
    ).strip()
    account_id = str(
        claims.get("account")
        or claims.get("account_id")
        or claims.get("accountId")
        or ""
    ).strip()
    return KeygenIdentity(
        subject=subject,
        machine_id=machine_id,
        license_id=license_id,
        account_id=account_id,
        raw_claims=claims,
    )


def verify_keygen_jwt(agent_token: str, s: Settings) -> KeygenIdentity:
    if not s.keygen_public_key:
        raise ValueError("KEYGEN_PUBLIC_KEY not configured")
    try:
        claims = jwt.decode(
            agent_token,
            key=s.keygen_public_key,
            algorithms=["RS256", "ES256", "EdDSA"],
            audience=s.keygen_audience or None,
            issuer=s.keygen_issuer or None,
            leeway=s.keygen_leeway_seconds,
        )
    except InvalidTokenError as e:
        raise ValueError(f"invalid keygen jwt: {e}") from e
    if not isinstance(claims, dict):
        raise ValueError("invalid keygen jwt payload")
    return _extract_identity(claims)


def _stable_token_hash(v: str) -> str:
    return hashlib.sha256(v.encode("utf-8")).hexdigest()


async def verify_keygen_introspection(agent_token: str, s: Settings) -> KeygenIdentity:
    if not s.keygen_api_token:
        raise ValueError("KEYGEN_API_TOKEN not configured")
    token_hash = _stable_token_hash(agent_token)
    base_url = s.keygen_api_url.rstrip("/")
    # Keygen account-scoped endpoints are required by many setups.
    if s.keygen_account:
        account = quote(s.keygen_account.strip(), safe="")
        endpoint = f"{base_url}/v1/accounts/{account}/tokens/{token_hash}"
    else:
        endpoint = f"{base_url}/v1/tokens/{token_hash}"
    headers = {
        "Authorization": f"Bearer {s.keygen_api_token}",
        "Accept": "application/vnd.api+json",
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(endpoint, headers=headers)
    if r.status_code >= 400:
        detail = (r.text or "").strip()
        if len(detail) > 300:
            detail = detail[:300] + "..."
        raise ValueError(f"keygen introspection failed status={r.status_code}: {detail}")
    payload = r.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    attrs = data.get("attributes") if isinstance(data, dict) else None
    if not isinstance(attrs, dict):
        raise ValueError("keygen introspection malformed response")
    if attrs.get("revoked") or attrs.get("expired"):
        raise ValueError("keygen token revoked or expired")
    sub = str(
        attrs.get("machine")
        or attrs.get("license")
        or attrs.get("id")
        or ""
    ).strip()
    if not sub:
        raise ValueError("keygen introspection missing subject")
    claims = {
        "sub": sub,
        "machine": attrs.get("machine") or "",
        "license": attrs.get("license") or "",
        "account": attrs.get("account") or "",
    }
    return _extract_identity(claims)


async def verify_agent_token(agent_token: str, s: Settings) -> KeygenIdentity:
    if not agent_token:
        raise ValueError("agent_token required")
    if s.keygen_verify_mode == "introspection":
        return await verify_keygen_introspection(agent_token, s)
    return verify_keygen_jwt(agent_token, s)
