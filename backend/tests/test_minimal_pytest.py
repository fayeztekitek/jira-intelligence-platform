"""Minimal test to isolate the session-sharing issue."""
import sys, os, tempfile, uuid
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import select
from storage.database import Base
from storage.models import DimProject

_db_file = os.path.join(tempfile.gettempdir(), f"test_min_{uuid.uuid4().hex[:8]}.sqlite")
TEST_ENGINE = create_async_engine(f"sqlite+aiosqlite:///{_db_file}", echo=True)


@pytest.mark.asyncio
async def test_minimal_pytest():
    """Create tables, seed with session A, query with session B."""
    async with TEST_ENGINE.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Seed data in session A
    async with AsyncSession(TEST_ENGINE, expire_on_commit=False) as sess_a:
        sess_a.add(DimProject(id="P1", name="Test Proj", is_active=True))
        await sess_a.commit()
        print("Session A committed")

    # Query in session B (simulating route handler)
    async with AsyncSession(TEST_ENGINE) as sess_b:
        result = await sess_b.execute(select(DimProject))
        rows = result.scalars().all()
        print(f"Session B found {len(rows)} rows")
        assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"
        assert rows[0].id == "P1"

    # Cleanup
    async with TEST_ENGINE.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await TEST_ENGINE.dispose()
    os.unlink(_db_file)
