"""
tests/test_burndown.py — Unit tests for SprintBurndownRepository.

Run: pytest tests/test_burndown.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select

from storage.repositories import SprintBurndownRepository
from storage.models import Base, DimSprint, FactIssue, FactTransition


def _utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture(scope="function")
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def seed_data(db_session):
    """Seed sprint + issues + resolution transitions."""
    today = date.today()

    sprint = DimSprint(
        id=201, board_id=1, name="Sprint Burndown", state="closed",
        start_date=_utc(today - timedelta(days=14)),
        end_date=_utc(today - timedelta(days=0)),
        complete_date=_utc(today - timedelta(days=0)),
        goal="Burndown test sprint",
    )
    db_session.add(sprint)

    # 3 issues committed: 5, 3, 4 = 12 total
    points = [5.0, 3.0, 4.0]
    resolved_days = [10, 5, 12]  # resolved days before sprint end
    for i, (sp, rd) in enumerate(zip(points, resolved_days)):
        jira_key = f"BD-{i}"
        db_session.add(FactIssue(
            jira_id=f"bd-{i}", jira_key=jira_key,
            project_key="PROJ",
            summary=f"Burndown issue {i}",
            issue_type="Story",
            status="Done",
            status_category="Done",
            story_points=sp,
            sprint_ids=json.dumps([201]),
            created_date=_utc(today - timedelta(days=15)),
            resolved_date=_utc(today - timedelta(days=rd)),
        ))
        # Resolution transition
        db_session.add(FactTransition(
            jira_key=jira_key,
            changelog_id=f"chg-bd-resolve-{i}",
            field="status",
            from_string="In Progress",
            to_string="Done",
            changed_at=_utc(today - timedelta(days=rd)),
        ))

    # One extra issue that's NOT done (not resolved)
    db_session.add(FactIssue(
        jira_id="bd-3", jira_key="BD-3",
        project_key="PROJ",
        summary="Burndown unresolved",
        issue_type="Story",
        status="In Progress",
        status_category="In Progress",
        story_points=2.0,
        sprint_ids=json.dumps([201]),
        created_date=_utc(today - timedelta(days=12)),
        resolved_date=None,
    ))

    await db_session.commit()


class TestSprintBurndown:

    async def test_burndown_structure(self, db_session):
        repo = SprintBurndownRepository(db_session)
        data = await repo.get_burndown(201)
        assert data is not None
        assert len(data) == 15  # 14 days + 1 = 15 entries
        first = data[0]
        last = data[-1]
        assert "date" in first
        assert "remaining_points" in first
        assert "ideal_points" in first
        # Day 0: remaining = 14.0 (5+3+4+2), ideal = 14.0
        assert first["remaining_points"] == 14.0
        assert first["ideal_points"] == 14.0

    async def test_burndown_last_day(self, db_session):
        repo = SprintBurndownRepository(db_session)
        data = await repo.get_burndown(201)
        last = data[-1]
        # Day 14: remaining = 2.0 (BD-3 still open)
        assert last["remaining_points"] == 2.0
        assert last["ideal_points"] == 0.0

    async def test_burndown_intermediate(self, db_session):
        repo = SprintBurndownRepository(db_session)
        data = await repo.get_burndown(201)
        # BD-2 resolved at day 2 (4 SP), BD-0 at day 4 (5 SP), BD-1 at day 9 (3 SP)
        entry_day2 = data[2]      # after BD-2 resolved: 14-4 = 10
        assert entry_day2["remaining_points"] == 10.0
        entry_day5 = data[5]      # after BD-2 + BD-0: 14-4-5 = 5
        assert entry_day5["remaining_points"] == 5.0
        entry_day10 = data[10]    # after all resolved: remaining = 2.0 (BD-3)
        assert entry_day10["remaining_points"] == 2.0

    async def test_burndown_nonexistent_sprint(self, db_session):
        repo = SprintBurndownRepository(db_session)
        data = await repo.get_burndown(999)
        assert data is None

    async def test_burndown_empty_sprint(self, db_session):
        sprint2 = DimSprint(
            id=202, board_id=1, name="Empty Sprint", state="future",
            start_date=_utc(date.today() + timedelta(days=7)),
            end_date=_utc(date.today() + timedelta(days=21)),
            complete_date=None,
        )
        db_session.add(sprint2)
        await db_session.commit()

        repo = SprintBurndownRepository(db_session)
        data = await repo.get_burndown(202)
        assert data == []  # no issues → empty list

    async def test_ideal_line_linear(self, db_session):
        repo = SprintBurndownRepository(db_session)
        data = await repo.get_burndown(201)
        # Ideal line should be strictly decreasing
        ideals = [entry["ideal_points"] for entry in data]
        for i in range(1, len(ideals)):
            assert ideals[i] < ideals[i-1]
        assert ideals[0] == 14.0
        assert ideals[-1] == 0.0

    async def test_remaining_never_below_zero(self, db_session):
        """Even if more resolved than committed, remaining shouldn't go negative."""
        sprint3 = DimSprint(
            id=203, board_id=1, name="Over-resolved Sprint", state="closed",
            start_date=_utc(date.today() - timedelta(days=7)),
            end_date=_utc(date.today()),
            complete_date=_utc(date.today()),
        )
        db_session.add(sprint3)
        db_session.add(FactIssue(
            jira_id="br-over", jira_key="BR-OVER",
            project_key="PROJ",
            summary="Over-resolved issue",
            issue_type="Story",
            status="Done",
            status_category="Done",
            story_points=5.0,
            sprint_ids=json.dumps([203]),
            created_date=_utc(date.today() - timedelta(days=8)),
            resolved_date=_utc(date.today() - timedelta(days=3)),
        ))
        # Extra resolution that shouldn't double-count
        db_session.add(FactTransition(
            jira_key="BR-OVER",
            changelog_id="chg-br-resolve-1",
            field="status",
            from_string="In Progress",
            to_string="Done",
            changed_at=_utc(date.today() - timedelta(days=3)),
        ))
        db_session.add(FactTransition(
            jira_key="BR-OVER",
            changelog_id="chg-br-resolve-2",
            field="status",
            from_string="Done",
            to_string="In Progress",
            changed_at=_utc(date.today() - timedelta(days=2)),
        ))
        db_session.add(FactTransition(
            jira_key="BR-OVER",
            changelog_id="chg-br-resolve-3",
            field="status",
            from_string="In Progress",
            to_string="Done",
            changed_at=_utc(date.today() - timedelta(days=1)),
        ))
        await db_session.commit()

        repo = SprintBurndownRepository(db_session)
        data = await repo.get_burndown(203)
        assert data is not None
        for entry in data:
            assert entry["remaining_points"] >= 0.0
