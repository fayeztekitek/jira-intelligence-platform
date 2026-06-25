"""
api/auth.py — Authentication and authorization module.

Provides:
- JWT token creation and validation
- API key authentication
- Route protection dependency
- Role-based access control (RBAC)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Header, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from pydantic import BaseModel

from config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api/auth", tags=["auth"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 480  # 8 hours
security = HTTPBearer(auto_error=False)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user_id: str
    role: str


class LoginRequest(BaseModel):
    api_key: str


class UserSession(BaseModel):
    user_id: str
    role: Literal["admin", "viewer", "analyst"]
    projects: list[str]  # empty = all projects


# ---------------------------------------------------------------------------
# Internal user store (MVP — replace with DB + RBAC in P3)
# ---------------------------------------------------------------------------

_ADMIN_API_KEY = None


def _get_admin_key() -> str:
    global _ADMIN_API_KEY
    if _ADMIN_API_KEY is None:
        _ADMIN_API_KEY = settings.app_secret_key + "_admin_key_"
        _ADMIN_API_KEY = uuid.uuid5(uuid.NAMESPACE_DNS, _ADMIN_API_KEY).hex
    return _ADMIN_API_KEY


USER_STORE: dict[str, UserSession] = {}


def _init_default_user() -> None:
    """Create a default admin user from the configured API key."""
    api_key = _get_admin_key()
    if api_key not in USER_STORE:
        USER_STORE[api_key] = UserSession(
            user_id="admin",
            role="admin",
            projects=[],
        )


_init_default_user()

# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def create_access_token(user_id: str, role: str, projects: list[str]) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": user_id,
        "role": role,
        "projects": projects,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.app_secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> UserSession | None:
    try:
        payload = jwt.decode(token, settings.app_secret_key, algorithms=[ALGORITHM])
        return UserSession(
            user_id=payload.get("sub", "unknown"),
            role=payload.get("role", "viewer"),
            projects=payload.get("projects", []),
        )
    except JWTError as e:
        logger.warning("jwt_decode_failed", error=str(e))
        return None

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/login", response_model=TokenResponse)
async def login(login_req: LoginRequest) -> TokenResponse:
    """
    Authenticate with an API key and receive a JWT token.
    In MVP: the admin API key is generated from APP_SECRET_KEY.
    """
    user = USER_STORE.get(login_req.api_key)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    token = create_access_token(user.user_id, user.role, user.projects)
    logger.info("user_logged_in", user_id=user.user_id, role=user.role)

    return TokenResponse(
        access_token=token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user_id=user.user_id,
        role=user.role,
    )


@router.get("/me")
async def get_current_user_info(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> dict:
    user = _resolve_user(credentials, x_api_key)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return {
        "user_id": user.user_id,
        "role": user.role,
        "projects": user.projects,
    }

# ---------------------------------------------------------------------------
# Dependency for route protection
# ---------------------------------------------------------------------------


def _resolve_user(
    credentials: HTTPAuthorizationCredentials | None,
    x_api_key: str | None,
) -> UserSession | None:
    # Try API key first
    if x_api_key:
        user = USER_STORE.get(x_api_key)
        if user:
            return user

    # Then try JWT
    if credentials:
        token = credentials.credentials
        user = decode_access_token(token)
        if user:
            return user

    return None


async def require_auth(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> UserSession:
    user = _resolve_user(credentials, x_api_key)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide Authorization: Bearer <token> or X-API-Key header.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def require_admin(user: Annotated[UserSession, Depends(require_auth)]) -> UserSession:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
