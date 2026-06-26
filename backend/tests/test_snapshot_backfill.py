"""
tests/test_snapshot_backfill.py — Unit tests for snapshot pipeline.

Run: pytest tests/test_snapshot_backfill.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select, func

from storage.models import Base, FactSnapshot, RiskLevel, DimProject, FactIssue
from ingestion.snapshot_writer import SnapshotWriter


@pytest_asyncio.fixture(scope="function")
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


class TestFactSnapshot:

    async def test_unique_constraint(self, db_session):
        """Same project+date+period should not allow duplicates."""
        for _ in range(2):
            db_session.add(FactSnapshot(
                project_key="PROJ", snapshot_date=date.today(),
                period_type="daily",
                total_open=10,
            ))
        with pytest.raises(Exception):
            await db_session.commit()
        await db_session.rollback()

    async def test_snapshot_fields(self, db_session):
        """All FactSnapshot fields should round-trip correctly."""
        snap = FactSnapshot(
            project_key="PROJ",
            snapshot_date=date(2026, 6, 1),
            period_type="weekly",
            total_open=50,
            total_created=20,
            total_resolved=15,
            resolution_rate=75.0,
            avg_resolution_days=4.5,
            avg_cycle_time_days=3.2,
            throughput=12,
            backlog_size=35,
            wip=8,
            overdue_count=5,
            bugs_created=10,
            bugs_resolved=7,
            bug_resolution_rate=70.0,
            reopened_count=3,
            critical_bugs_open=2,
            dq_missing_assignee=4,
            dq_score=82.0,
            risk_score=35.0,
            risk_level=RiskLevel.MEDIUM,
            sprint_velocity=25.0,
            sprint_predictability=0.85,
        )
        db_session.add(snap)
        await db_session.commit()

        loaded = await db_session.get(FactSnapshot, snap.id)
        assert loaded.total_open == 50
        assert loaded.total_created == 20
        assert loaded.total_resolved == 15
        assert loaded.resolution_rate == 75.0
        assert loaded.avg_resolution_days == 4.5
        assert loaded.avg_cycle_time_days == 3.2
        assert loaded.throughput == 12
        assert loaded.backlog_size == 35
        assert loaded.wip == 8
        assert loaded.overdue_count == 5
        assert loaded.bugs_created == 10
        assert loaded.bugs_resolved == 7
        assert loaded.bug_resolution_rate == 70.0
        assert loaded.reopened_count == 3
        assert loaded.critical_bugs_open == 2
        assert loaded.dq_missing_assignee == 4
        assert loaded.dq_score == 82.0
        assert loaded.risk_score == 35.0
        assert loaded.risk_level == RiskLevel.MEDIUM
        assert loaded.sprint_velocity == 25.0
        assert loaded.sprint_predictability == 0.85

    async def test_query_snapshots_by_date_range(self, db_session):
        """Snapshots should be filterable by date range."""
        for day_offset in range(90):
            d = date(2026, 1, 1) + timedelta(days=day_offset)
            db_session.add(FactSnapshot(
                project_key="PROJ", snapshot_date=d, period_type="daily",
            ))
        await db_session.commit()

        since = date(2026, 1, 31)
        until = date(2026, 2, 5)
        result = await db_session.execute(
            select(FactSnapshot).where(
                FactSnapshot.project_key == "PROJ",
                FactSnapshot.snapshot_date >= since,
                FactSnapshot.snapshot_date <= until,
                FactSnapshot.period_type == "daily",
            ).order_by(FactSnapshot.snapshot_date.asc())
        )
        rows = result.scalars().all()
        assert len(rows) == 6  # Jan 31 - Feb 5

    async def test_multiple_period_types(self, db_session):
        """Multiple period types should coexist for the same date."""
        for pt in ["daily", "weekly", "monthly"]:
            db_session.add(FactSnapshot(
                project_key="PROJ", snapshot_date=date.today(),
                period_type=pt,
            ))
        await db_session.commit()

        for pt in ["daily", "weekly", "monthly"]:
            snap = (await db_session.execute(
                select(FactSnapshot).where(
                    FactSnapshot.project_key == "PROJ",
                    FactSnapshot.snapshot_date == date.today(),
                    FactSnapshot.period_type == pt,
                )
            )).scalar_one_or_none()
            assert snap is not None, f"Missing period_type={pt}"
