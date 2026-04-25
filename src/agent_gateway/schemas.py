"""
Message shapes for WebSocket agents (reference; validation can be tightened later).

MVP routing: in-memory map **token_id → WebSocket**. Persistent token↔device binding belongs
in a Dockerised DB or Redis in Phase 3.
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field


class WsRegister(BaseModel):
    type: Literal["register"] = "register"
    # Legacy shared secret (legacy/dual mode).
    device_token: str = ""
    # Keygen-backed identity token (keygen/dual mode).
    agent_token: str = ""
    # Optional Keygen token resource id (UUID). Preferred for introspection lookup.
    agent_token_id: str = ""
    # Optional machine or license identifier (for introspection fallback / debugging).
    agent_id: str = ""
    # Client-side hint only; server should intersect against authorized ownership map.
    token_ids: list[int] = Field(default_factory=list)


class AgentIdentity(BaseModel):
    auth_method: Literal["legacy", "keygen"]
    subject: str
    machine_id: str = ""
    license_id: str = ""
    account_id: str = ""


class WsSolveResult(BaseModel):
    type: Literal["solve_result"] = "solve_result"
    job_id: str
    token: str
    session_id: str
    fingerprint: Optional[dict] = None


class WsSolveError(BaseModel):
    type: Literal["solve_error"] = "solve_error"
    job_id: str
    error: str = "agent_error"
