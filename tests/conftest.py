import os

os.environ["APP_ENV"] = "development"
os.environ["APP_SECRET_KEY"] = "test-secret-key-for-testing"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"

from collections.abc import AsyncGenerator
from typing import Annotated

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from api.auth import (
    USER_STORE,
    _ADMIN_API_KEY,
    _get_admin_key,
    _init_default_user,
    require_admin,
    require_auth,
    router as auth_router,
)
from api.auth import UserSession


# ---------------------------------------------------------------------------
# Test app factory (no DB dependencies)
# ---------------------------------------------------------------------------

def create_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(auth_router)

    @app.get("/api/protected")
    async def protected_endpoint(
        user: Annotated[UserSession, Depends(require_auth)],
    ):
        return {"message": "ok", "user_id": user.user_id, "role": user.role}

    @app.get("/api/admin-only")
    async def admin_endpoint(
        user: Annotated[UserSession, Depends(require_admin)],
    ):
        return {"message": "admin ok"}

    return app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_auth_globals():
    import api.auth as auth_mod

    auth_mod._ADMIN_API_KEY = None
    auth_mod.USER_STORE.clear()
    auth_mod._init_default_user()
    yield


@pytest.fixture
def admin_key() -> str:
    return _get_admin_key()


@pytest_asyncio.fixture
async def test_app() -> FastAPI:
    return create_test_app()


@pytest_asyncio.fixture
async def client(test_app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def admin_token(client: AsyncClient, admin_key: str) -> str:
    resp = await client.post("/api/auth/login", json={"api_key": admin_key})
    assert resp.status_code == 200
    return resp.json()["access_token"]


@pytest_asyncio.fixture
async def auth_headers(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}
