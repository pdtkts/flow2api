"""Authentication module"""

import bcrypt
from typing import Optional
from fastapi import Header, HTTPException, Query, Security, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from .config import config
from .api_key_manager import AuthContext

security = HTTPBearer()
optional_security = HTTPBearer(auto_error=False)
api_key_manager = None


def set_api_key_manager(manager):
    global api_key_manager
    api_key_manager = manager

class AuthManager:
    """Authentication manager"""

    @staticmethod
    def verify_api_key(api_key: str) -> bool:
        """Verify API key"""
        return api_key == config.api_key

    @staticmethod
    def verify_admin(username: str, password: str) -> bool:
        """Verify admin credentials"""
        # Compare with current config (which may be from database or config file)
        return username == config.admin_username and password == config.admin_password

    @staticmethod
    def hash_password(password: str) -> str:
        """Hash password"""
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    @staticmethod
    def verify_password(password: str, hashed: str) -> bool:
        """Verify password"""
        return bcrypt.checkpw(password.encode(), hashed.encode())

async def verify_api_key_header(credentials: HTTPAuthorizationCredentials = Security(security)) -> str:
    """Verify API key from Authorization header"""
    api_key = credentials.credentials
    if not AuthManager.verify_api_key(api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")
    return api_key


async def verify_api_key_flexible(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Security(optional_security),
    x_goog_api_key: Optional[str] = Header(None, alias="x-goog-api-key"),
    key: Optional[str] = Query(None),
) -> AuthContext:
    """Verify API key from Authorization header, x-goog-api-key header, or key query param."""
    api_key = None

    if credentials is not None:
        api_key = credentials.credentials
    elif x_goog_api_key:
        api_key = x_goog_api_key
    elif key:
        api_key = key

    if not api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    endpoint = request.url.path
    require_assignment = endpoint in {
        "/v1/chat/completions",
    } or endpoint.endswith(":generateContent") or endpoint.endswith(":streamGenerateContent") or (
        endpoint == "/v1/projects" and request.method.upper() == "POST"
    )
    if api_key_manager is None:
        if not AuthManager.verify_api_key(api_key):
            raise HTTPException(status_code=401, detail="Invalid API key")
        return AuthContext(
            key_id=None,
            key_label="legacy-global",
            is_legacy=True,
            allowed_accounts=set(),
            scopes={"*"},
            adobe_cloning_enabled=True,
            adobe_metadata_enabled=True,
            adobe_tracker_enabled=True,
        )

    try:
        context = await api_key_manager.authenticate(
            api_key,
            endpoint=endpoint,
            require_assignment=require_assignment,
        )
        await api_key_manager.db.insert_api_key_audit_log(
            api_key_id=context.key_id,
            endpoint=endpoint,
            account_id=None,
            status_code=200,
            detail="ok",
            ip=(request.client.host if request.client else ""),
            user_agent=request.headers.get("user-agent", ""),
        )
        return context
    except PermissionError as exc:
        await api_key_manager.db.insert_api_key_audit_log(
            api_key_id=None,
            endpoint=endpoint,
            account_id=None,
            status_code=403 if require_assignment else 401,
            detail=str(exc),
            ip=(request.client.host if request.client else ""),
            user_agent=request.headers.get("user-agent", ""),
        )
        if "accounts assigned" in str(exc).lower():
            raise HTTPException(status_code=403, detail=str(exc))
        raise HTTPException(status_code=401, detail=str(exc))
    except RuntimeError as exc:
        await api_key_manager.db.insert_api_key_audit_log(
            api_key_id=None,
            endpoint=endpoint,
            account_id=None,
            status_code=429,
            detail=str(exc),
            ip=(request.client.host if request.client else ""),
            user_agent=request.headers.get("user-agent", ""),
        )
        raise HTTPException(status_code=429, detail=str(exc))
