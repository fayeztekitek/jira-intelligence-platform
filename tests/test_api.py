"""
Integration tests for the auth flow end-to-end.

Tests cover:
- Login -> token -> protected endpoint
- Token expiry handling
- Invalid token rejection
- API key header authentication
- Admin vs non-admin access
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from jose import jwt

from api.auth import ALGORITHM, UserSession, USER_STORE, create_access_token


class TestAuthFlowIntegration:
    """Full auth flow: login, token usage, expiry, re-auth."""

    async def test_full_auth_flow(self, client: AsyncClient, admin_key: str):
        # Step 1: Login with API key -> get JWT
        login_resp = await client.post("/api/auth/login", json={"api_key": admin_key})
        assert login_resp.status_code == 200
        body = login_resp.json()
        token = body["access_token"]
        assert body["user_id"] == "admin"
        assert body["role"] == "admin"

        # Step 2: Use JWT to access protected endpoint
        protected_resp = await client.get(
            "/api/protected",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert protected_resp.status_code == 200
        assert protected_resp.json()["user_id"] == "admin"

        # Step 3: Verify identity via /me
        me_resp = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_resp.status_code == 200
        assert me_resp.json()["user_id"] == "admin"

    async def test_expired_token_rejected(self, client: AsyncClient):
        """Create an already-expired token and verify 401."""
        from api.auth import settings as auth_settings

        payload = {
            "sub": "admin",
            "role": "admin",
            "projects": [],
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
            "iat": datetime.now(timezone.utc) - timedelta(hours=2),
            "jti": "expired-token-test",
        }
        expired_token = jwt.encode(
            payload, auth_settings.app_secret_key, algorithm=ALGORITHM
        )

        resp = await client.get(
            "/api/protected",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert resp.status_code == 401

    async def test_invalid_signature_rejected(self, client: AsyncClient):
        """Token signed with wrong secret -> 401."""
        wrong_token = jwt.encode(
            {"sub": "admin", "role": "admin", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
            "wrong-secret-key",
            algorithm=ALGORITHM,
        )

        resp = await client.get(
            "/api/protected",
            headers={"Authorization": f"Bearer {wrong_token}"},
        )
        assert resp.status_code == 401

    async def test_malformed_token_rejected(self, client: AsyncClient):
        resp = await client.get(
            "/api/protected",
            headers={"Authorization": "Bearer not-a-valid-token"},
        )
        assert resp.status_code == 401

    async def test_empty_token_rejected(self, client: AsyncClient):
        resp = await client.get(
            "/api/protected",
            headers={"Authorization": "Bearer "},
        )
        assert resp.status_code == 401

    async def test_missing_auth_header(self, client: AsyncClient):
        resp = await client.get("/api/protected")
        assert resp.status_code == 401

    async def test_wrong_auth_scheme(self, client: AsyncClient, admin_key: str):
        """Using Basic auth instead of Bearer -> 401."""
        import base64

        encoded = base64.b64encode(f"admin:{admin_key}".encode()).decode()
        resp = await client.get(
            "/api/protected",
            headers={"Authorization": f"Basic {encoded}"},
        )
        assert resp.status_code == 401

    async def test_x_api_key_alternative(self, client: AsyncClient, admin_key: str):
        """X-API-Key header must work as alternative auth method."""
        resp = await client.get(
            "/api/protected",
            headers={"X-API-Key": admin_key},
        )
        assert resp.status_code == 200

    async def test_wrong_api_key_rejected(self, client: AsyncClient):
        resp = await client.get(
            "/api/protected",
            headers={"X-API-Key": "invalid-key-12345"},
        )
        assert resp.status_code == 401


class TestAdminEndpointIntegration:
    async def test_admin_token_admin_endpoint(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/admin-only", headers=auth_headers)
        assert resp.status_code == 200

    async def test_non_admin_token_admin_endpoint(self, client: AsyncClient):
        """A viewer token must be rejected by admin-only endpoints."""
        viewer_key = "viewer-for-admin-test"
        USER_STORE[viewer_key] = UserSession(
            user_id="viewer1", role="viewer", projects=[]
        )
        login_resp = await client.post("/api/auth/login", json={"api_key": viewer_key})
        token = login_resp.json()["access_token"]

        resp = await client.get(
            "/api/admin-only",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
        assert "Admin access required" in resp.json()["detail"]

    async def test_admin_endpoint_no_auth(self, client: AsyncClient):
        resp = await client.get("/api/admin-only")
        assert resp.status_code == 401


class TestConcurrentUsers:
    """Multiple users with different roles should not interfere."""

    async def test_two_users_independent_sessions(self, client: AsyncClient, admin_key: str):
        admin_token_resp = await client.post("/api/auth/login", json={"api_key": admin_key})
        admin_token = admin_token_resp.json()["access_token"]

        viewer_key = "viewer2"
        USER_STORE[viewer_key] = UserSession(
            user_id="viewer2", role="viewer", projects=[]
        )
        viewer_token_resp = await client.post("/api/auth/login", json={"api_key": viewer_key})
        viewer_token = viewer_token_resp.json()["access_token"]

        # Admin can access admin endpoint
        r1 = await client.get("/api/admin-only", headers={"Authorization": f"Bearer {admin_token}"})
        assert r1.status_code == 200

        # Viewer cannot access admin endpoint
        r2 = await client.get("/api/admin-only", headers={"Authorization": f"Bearer {viewer_token}"})
        assert r2.status_code == 403

        # Both can access protected endpoint
        r3 = await client.get("/api/protected", headers={"Authorization": f"Bearer {admin_token}"})
        assert r3.status_code == 200
        r4 = await client.get("/api/protected", headers={"Authorization": f"Bearer {viewer_token}"})
        assert r4.status_code == 200
