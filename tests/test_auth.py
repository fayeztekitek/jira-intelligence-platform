from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from jose import jwt

from api.auth import (
    ALGORITHM,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    _get_admin_key,
    USER_STORE,
    create_access_token,
    decode_access_token,
    UserSession,
)


# ---------------------------------------------------------------------------
# Unit tests — pure functions
# ---------------------------------------------------------------------------


class TestAdminKey:
    def test_deterministic(self):
        assert _get_admin_key() == _get_admin_key()

    def test_hex_format(self):
        key = _get_admin_key()
        assert len(key) == 32
        assert all(c in "0123456789abcdef" for c in key)

    def test_differs_for_different_secret(self):
        key1 = _get_admin_key()
        with (
            patch("api.auth.settings") as mock_settings,
            patch("api.auth._ADMIN_API_KEY", None),
        ):
            mock_settings.app_secret_key = "different-secret"
            key2 = _get_admin_key()
        assert key1 != key2


class TestTokenRoundTrip:
    def test_create_and_decode(self):
        token = create_access_token("admin", "admin", [])
        session = decode_access_token(token)
        assert session is not None
        assert session.user_id == "admin"
        assert session.role == "admin"
        assert session.projects == []

    def test_non_admin_role(self):
        token = create_access_token("alice", "viewer", ["PROJ1"])
        session = decode_access_token(token)
        assert session is not None
        assert session.user_id == "alice"
        assert session.role == "viewer"
        assert session.projects == ["PROJ1"]


class TestTokenEdgeCases:
    def test_decode_nonsense(self):
        assert decode_access_token("not.a.token") is None

    def test_decode_empty(self):
        assert decode_access_token("") is None

    def test_decode_tampered(self):
        token = create_access_token("admin", "admin", [])
        parts = token.split(".")
        parts[1] = "eyJzdWIiOiJoYWNrZXIifQ"  # tampered payload
        tampered = ".".join(parts)
        assert decode_access_token(tampered) is None

    def test_decode_wrong_algorithm(self):
        from jose import jwt as jose_jwt

        token = jose_jwt.encode(
            {"sub": "admin"},
            "some-other-secret",
            algorithm="HS256",
        )
        result = decode_access_token(token)
        assert result is None

    def test_expired_token(self):
        """Token with past expiry should be rejected."""
        from api.auth import settings as auth_settings

        payload = {
            "sub": "admin",
            "role": "admin",
            "projects": [],
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
            "iat": datetime.now(timezone.utc) - timedelta(hours=2),
            "jti": "test-jti",
        }
        token = jwt.encode(payload, auth_settings.app_secret_key, algorithm=ALGORITHM)
        assert decode_access_token(token) is None


# ---------------------------------------------------------------------------
# API tests — /api/auth/*
# ---------------------------------------------------------------------------


class TestLoginEndpoint:
    async def test_login_success(self, client: AsyncClient, admin_key: str):
        resp = await client.post("/api/auth/login", json={"api_key": admin_key})
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"
        assert body["user_id"] == "admin"
        assert body["role"] == "admin"
        assert body["expires_in"] == ACCESS_TOKEN_EXPIRE_MINUTES * 60

    async def test_login_invalid_key(self, client: AsyncClient):
        resp = await client.post("/api/auth/login", json={"api_key": "wrong-key-12345"})
        assert resp.status_code == 401
        assert "Invalid API key" in resp.json()["detail"]

    async def test_login_empty_key(self, client: AsyncClient):
        resp = await client.post("/api/auth/login", json={"api_key": ""})
        assert resp.status_code == 401

    async def test_login_missing_body(self, client: AsyncClient):
        resp = await client.post("/api/auth/login", json={})
        assert resp.status_code == 422  # validation error

    async def test_login_then_use_token(self, client: AsyncClient, admin_key: str):
        """End-to-end: login -> get token -> call protected endpoint."""
        login_resp = await client.post("/api/auth/login", json={"api_key": admin_key})
        token = login_resp.json()["access_token"]

        resp = await client.get(
            "/api/protected",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["user_id"] == "admin"


class TestMeEndpoint:
    async def test_me_authenticated(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/auth/me", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_id"] == "admin"
        assert body["role"] == "admin"

    async def test_me_no_auth(self, client: AsyncClient):
        resp = await client.get("/api/auth/me")
        assert resp.status_code == 401
        assert "Invalid or expired token" in resp.json()["detail"]

    async def test_me_with_api_key_header(self, client: AsyncClient, admin_key: str):
        resp = await client.get("/api/auth/me", headers={"X-API-Key": admin_key})
        assert resp.status_code == 200
        assert resp.json()["user_id"] == "admin"

    async def test_me_with_bad_api_key(self, client: AsyncClient):
        resp = await client.get("/api/auth/me", headers={"X-API-Key": "bad-key"})
        assert resp.status_code == 401


class TestAdminOnlyEndpoint:
    async def test_admin_access(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/admin-only", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["message"] == "admin ok"

    async def test_non_admin_denied(self, client: AsyncClient, admin_key: str):
        """Create a viewer token and verify 403 on admin endpoint."""
        from api.auth import create_access_token, USER_STORE, UserSession

        viewer_key = "viewer-test-key-abc"
        USER_STORE[viewer_key] = UserSession(
            user_id="viewer1", role="viewer", projects=[]
        )
        resp = await client.post("/api/auth/login", json={"api_key": viewer_key})
        token = resp.json()["access_token"]

        resp2 = await client.get(
            "/api/admin-only",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp2.status_code == 403
        assert "Admin access required" in resp2.json()["detail"]

    async def test_admin_requires_auth(self, client: AsyncClient):
        resp = await client.get("/api/admin-only")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Auth header variations
# ---------------------------------------------------------------------------


class TestAuthMethods:
    async def test_bearer_token(self, client: AsyncClient, admin_token: str):
        resp = await client.get(
            "/api/protected",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200

    async def test_api_key_header(self, client: AsyncClient, admin_key: str):
        resp = await client.get(
            "/api/protected",
            headers={"X-API-Key": admin_key},
        )
        assert resp.status_code == 200

    async def test_no_auth(self, client: AsyncClient):
        resp = await client.get("/api/protected")
        assert resp.status_code == 401
