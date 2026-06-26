"""
tests/test_sprint_kpis.py — Unit tests for SprintAnalyzer.

Run: pytest tests/test_sprint_kpis.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select

from kpi_engine.sprint import SprintAnalyzer, SprintSummary
from storage.models import Base, DimSprint, FactIssue, FactTransition


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    """Seed DimSprint and FactIssue rows."""
    today = date.today()

    # Sprint 1 (closed, ended 15 days ago)
    s1 = DimSprint(
        id=101, board_id=1, name="Sprint 1", state="closed",
        start_date=_utc(today - timedelta(days=30)),
        end_date=_utc(today - timedelta(days=15)),
        complete_date=_utc(today - timedelta(days=14)),
        goal="First sprint",
    )
    db_session.add(s1)

    # Sprint 2 (closed, ended 5 days ago)
    s2 = DimSprint(
        id=102, board_id=1, name="Sprint 2", state="closed",
        start_date=_utc(today - timedelta(days=14)),
        end_date=_utc(today - timedelta(days=5)),
        complete_date=_utc(today - timedelta(days=4)),
        goal="Second sprint",
    )
    db_session.add(s2)

    # Sprint 3 (active)
    s3 = DimSprint(
        id=103, board_id=1, name="Sprint 3", state="active",
        start_date=_utc(today - timedelta(days=4)),
        end_date=_utc(today + timedelta(days=10)),
        complete_date=None,
        goal="Active sprint",
    )
    db_session.add(s3)

    # Issues for Sprint 1 (101):
    # 3 issues, all Done, resolved before end_date
    for i in range(3):
        db_session.add(FactIssue(
            jira_id=f"sp1-{i}", jira_key=f"SP1-{i}",
            project_key="PROJ",
            summary=f"Sprint 1 issue {i}",
            issue_type="Story",
            status="Done",
            status_category="Done",
            story_points=5.0,
            sprint_ids=json.dumps([101]),
            created_date=_utc(today - timedelta(days=28)),
            resolved_date=_utc(today - timedelta(days=16)),
        ))

    # Issues for Sprint 2 (102):
    # 2 Done, 1 In Progress (carry-over)
    for i in range(2):
        db_session.add(FactIssue(
            jira_id=f"sp2-{i}", jira_key=f"SP2-{i}",
            project_key="PROJ",
            summary=f"Sprint 2 issue {i}",
            issue_type="Story",
            status="Done",
            status_category="Done",
            story_points=8.0,
            sprint_ids=json.dumps([102]),
            created_date=_utc(today - timedelta(days=12)),
            resolved_date=_utc(today - timedelta(days=6)),
        ))
    # Carry-over issue (created before sprint 2, still open)
    db_session.add(FactIssue(
        jira_id="sp2-carry", jira_key="SP2-CARRY",
        project_key="PROJ",
        summary="Carry-over from sprint 1",
        issue_type="Story",
        status="In Progress",
        status_category="In Progress",
        story_points=3.0,
        sprint_ids=json.dumps([101, 102]),
        created_date=_utc(today - timedelta(days=28)),
        resolved_date=None,
    ))

    # Issues for Sprint 3 (103):
    # 1 Done early, 1 In Progress
    db_session.add(FactIssue(
        jira_id="sp3-0", jira_key="SP3-0",
        project_key="PROJ",
        summary="Sprint 3 done early",
        issue_type="Story",
        status="Done",
        status_category="Done",
        story_points=5.0,
        sprint_ids=json.dumps([103]),
        created_date=_utc(today - timedelta(days=3)),
        resolved_date=_utc(today - timedelta(days=1)),
    ))
    db_session.add(FactIssue(
        jira_id="sp3-1", jira_key="SP3-1",
        project_key="PROJ",
        summary="Sprint 3 in progress",
        issue_type="Story",
        status="In Progress",
        status_category="In Progress",
        story_points=3.0,
        sprint_ids=json.dumps([103]),
        created_date=_utc(today - timedelta(days=3)),
        resolved_date=None,
    ))

    # --- Scope change transitions for Sprint 3 ---
    # SP3-1 was added to Sprint 3 after it started (scope increase)
    db_session.add(FactTransition(
        jira_key="SP3-1",
        changelog_id="chg-scope-add-s3",
        field="Sprint",
        from_string="",
        to_string="Sprint 3",
        changed_at=_utc(today - timedelta(days=2)),
    ))
    # SP1-0 was removed from Sprint 1 after it started (scope decrease)
    db_session.add(FactTransition(
        jira_key="SP1-0",
        changelog_id="chg-scope-rem-s1",
        field="Sprint",
        from_string="Sprint 1",
        to_string="",
        changed_at=_utc(today - timedelta(days=22)),
    ))

    await db_session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSprintAnalyzer:

    async def test_analyze_returns_all_sprints(self, db_session):
        analyzer = SprintAnalyzer("PROJ")
        results = await analyzer.analyze(db_session)
        assert len(results) == 3

    async def test_sprint_1_velocity(self, db_session):
        analyzer = SprintAnalyzer("PROJ")
        results = await analyzer.analyze(db_session)
        s1 = next(r for r in results if r.sprint_id == 101)
        # SP1-0..2 (3*5=15) + SP2-CARRY (3) = 18 committed
        assert s1.total_committed == 18.0
        # Only the 3 SP1 issues resolved = 15 completed
        assert s1.total_completed == 15.0
        assert s1.completed_count == 3
        assert s1.predictability is not None
        assert 0.8 < s1.predictability < 0.9  # 15/18 = 0.8333

    async def test_sprint_2_completed_and_carry(self, db_session):
        analyzer = SprintAnalyzer("PROJ")
        results = await analyzer.analyze(db_session)
        s2 = next(r for r in results if r.sprint_id == 102)
        # SP2-0,1 (2*8=16) + SP2-CARRY (3) = 19 committed
        assert s2.total_committed == 19.0
        # Only the 2 SP2 issues resolved = 16
        assert s2.total_completed == 16.0
        assert s2.completed_count == 2
        # SP2-CARRY existed before sprint 102 started
        assert s2.carry_over == 3.0
        assert s2.carry_over_count == 1
        assert s2.predictability is not None
        assert s2.predictability < 1.0

    async def test_sprint_3_active(self, db_session):
        analyzer = SprintAnalyzer("PROJ")
        results = await analyzer.analyze(db_session)
        s3 = next(r for r in results if r.sprint_id == 103)
        assert s3.total_committed == 8.0  # SP3-0 (5) + SP3-1 (3) = 8
        assert s3.total_completed == 5.0  # only SP3-0 done
        assert s3.completed_count == 1

    async def test_analyze_single_sprint(self, db_session):
        analyzer = SprintAnalyzer("PROJ")
        result = await analyzer.analyze_sprint(db_session, 101)
        assert result is not None
        assert result.sprint_id == 101
        assert result.total_committed == 18.0

    async def test_analyze_nonexistent_sprint(self, db_session):
        analyzer = SprintAnalyzer("PROJ")
        result = await analyzer.analyze_sprint(db_session, 999)
        assert result is None

    async def test_to_dict(self):
        summary = SprintSummary(
            sprint_id=1, sprint_name="S1", board_id=1, state="closed",
            start_date=None, end_date=None, complete_date=None,
            total_committed=10.0, total_completed=8.0,
            predictability=0.8,
        )
        d = summary.to_dict()
        assert d["sprint_id"] == 1
        assert d["predictability"] == 0.8
        assert d["state"] == "closed"

    async def test_sprint3_scope_added(self, db_session):
        analyzer = SprintAnalyzer("PROJ")
        results = await analyzer.analyze(db_session)
        s3 = next(r for r in results if r.sprint_id == 103)
        # SP3-1 (3 SP) was added after sprint 3 started
        assert s3.scope_added == 3.0

    async def test_sprint1_scope_removed(self, db_session):
        analyzer = SprintAnalyzer("PROJ")
        results = await analyzer.analyze(db_session)
        s1 = next(r for r in results if r.sprint_id == 101)
        # SP1-0 (5 SP) was removed after sprint 1 started
        assert s1.scope_removed == 5.0

    async def test_parse_sprint_ids(self):
        assert SprintAnalyzer._parse_sprint_ids("[101, 102]") == [101, 102]
        assert SprintAnalyzer._parse_sprint_ids("[]") == []
        assert SprintAnalyzer._parse_sprint_ids(None) == []
        assert SprintAnalyzer._parse_sprint_ids("invalid") == []

    # ─── Edge case tests ─────────────────────────────────────────────────

    async def test_predictability_100(self, db_session):
        """Sprint where all committed points are completed = 1.0."""
        today = date.today()
        sprint = DimSprint(
            id=401, board_id=1, name="Perfect Sprint", state="closed",
            start_date=_utc(today - timedelta(days=10)),
            end_date=_utc(today - timedelta(days=1)),
            complete_date=_utc(today - timedelta(days=1)),
        )
        db_session.add(sprint)
        for i in range(3):
            db_session.add(FactIssue(
                jira_id=f"perfect-{i}", jira_key=f"PERF-{i}",
                project_key="PROJ",
                summary=f"Perfect {i}",
                issue_type="Story",
                status="Done", status_category="Done",
                story_points=4.0,
                sprint_ids=json.dumps([401]),
                created_date=_utc(today - timedelta(days=8)),
                resolved_date=_utc(today - timedelta(days=2)),
            ))
        await db_session.commit()

        analyzer = SprintAnalyzer("PROJ")
        result = await analyzer.analyze_sprint(db_session, 401)
        assert result is not None
        assert result.total_committed == 12.0
        assert result.total_completed == 12.0
        assert result.predictability == 1.0

    async def test_predictability_50(self, db_session):
        """Sprint where half the points are completed = 0.5."""
        today = date.today()
        sprint = DimSprint(
            id=402, board_id=1, name="Half Sprint", state="closed",
            start_date=_utc(today - timedelta(days=10)),
            end_date=_utc(today - timedelta(days=1)),
            complete_date=_utc(today - timedelta(days=1)),
        )
        db_session.add(sprint)
        # 2 issues, 5 SP each = 10 committed
        for i in range(2):
            db_session.add(FactIssue(
                jira_id=f"half-{i}", jira_key=f"HALF-{i}",
                project_key="PROJ",
                summary=f"Half {i}",
                issue_type="Story",
                status="Done" if i == 0 else "In Progress",
                status_category="Done" if i == 0 else "In Progress",
                story_points=5.0,
                sprint_ids=json.dumps([402]),
                created_date=_utc(today - timedelta(days=8)),
                resolved_date=_utc(today - timedelta(days=2)) if i == 0 else None,
            ))
        await db_session.commit()

        analyzer = SprintAnalyzer("PROJ")
        result = await analyzer.analyze_sprint(db_session, 402)
        assert result is not None
        assert result.total_committed == 10.0
        assert result.total_completed == 5.0
        assert result.predictability == 0.5

    async def test_predictability_null(self, db_session):
        """Sprint with no completed points = predictability None."""
        today = date.today()
        sprint = DimSprint(
            id=403, board_id=1, name="Zero Sprint", state="active",
            start_date=_utc(today - timedelta(days=2)),
            end_date=_utc(today + timedelta(days=12)),
            complete_date=None,
        )
        db_session.add(sprint)
        db_session.add(FactIssue(
            jira_id="zero-0", jira_key="ZERO-0",
            project_key="PROJ",
            summary="Not done",
            issue_type="Story",
            status="To Do", status_category="To Do",
            story_points=3.0,
            sprint_ids=json.dumps([403]),
            created_date=_utc(today - timedelta(days=1)),
            resolved_date=None,
        ))
        await db_session.commit()

        analyzer = SprintAnalyzer("PROJ")
        result = await analyzer.analyze_sprint(db_session, 403)
        assert result is not None
        assert result.total_committed == 3.0
        assert result.total_completed == 0.0
        # 0/3 = 0.0 (not None — calculation yields valid float)
        assert result.predictability == 0.0

    async def test_no_sprint_data(self, db_session):
        """Project with no sprints at all should return empty list."""
        analyzer = SprintAnalyzer("NOPROJ")
        results = await analyzer.analyze(db_session)
        assert len(results) == 0

    async def test_overlapping_sprints(self, db_session):
        """Issue in overlapping sprints counts committed for both, completed for the sprint it resolved in."""
        today = date.today()
        start_a = today - timedelta(days=20)
        end_a = today - timedelta(days=6)
        start_b = today - timedelta(days=4)
        end_b = today
        s_a = DimSprint(
            id=404, board_id=1, name="Sprint A", state="closed",
            start_date=_utc(start_a), end_date=_utc(end_a),
        )
        s_b = DimSprint(
            id=405, board_id=1, name="Sprint B", state="closed",
            start_date=_utc(start_b), end_date=_utc(end_b),
        )
        db_session.add_all([s_a, s_b])

        resolved = end_a + timedelta(days=1)  # resolved 1 day after sprint A ends
        db_session.add(FactIssue(
            jira_id="overlap-0", jira_key="OVERLAP-0",
            project_key="PROJ",
            summary="In both sprints",
            issue_type="Story",
            status="Done", status_category="Done",
            story_points=5.0,
            sprint_ids=json.dumps([404, 405]),
            created_date=_utc(today - timedelta(days=22)),
            resolved_date=_utc(resolved),
        ))
        await db_session.commit()

        analyzer = SprintAnalyzer("PROJ")
        results = await analyzer.analyze(db_session)
        s404 = next((r for r in results if r.sprint_id == 404), None)
        s405 = next((r for r in results if r.sprint_id == 405), None)
        assert s404 is not None
        assert s405 is not None
        assert s404.total_committed == 5.0
        assert s405.total_committed == 5.0
        # Resolved after sprint A ended → not completed in A
        assert s404.total_completed == 0.0
        # Resolved before sprint B ends → completed in B (resolved=end_a+1, sprint B end=today)
        assert s405.total_completed == 5.0

    async def test_sprint_without_story_points(self, db_session):
        """Sprint with issues having no story_points should still count."""
        today = date.today()
        sprint = DimSprint(
            id=406, board_id=1, name="No Points", state="closed",
            start_date=_utc(today - timedelta(days=10)),
            end_date=_utc(today - timedelta(days=1)),
        )
        db_session.add(sprint)
        for i in range(4):
            db_session.add(FactIssue(
                jira_id=f"nosp-{i}", jira_key=f"NOSP-{i}",
                project_key="PROJ",
                summary=f"No SP {i}",
                issue_type="Task",
                status="Done" if i < 3 else "To Do",
                status_category="Done" if i < 3 else "To Do",
                story_points=None,
                sprint_ids=json.dumps([406]),
                created_date=_utc(today - timedelta(days=8)),
                resolved_date=_utc(today - timedelta(days=2)) if i < 3 else None,
            ))
        await db_session.commit()

        analyzer = SprintAnalyzer("PROJ")
        result = await analyzer.analyze_sprint(db_session, 406)
        assert result is not None
        assert result.total_committed == 0.0  # no SP means 0
        assert result.total_completed == 0.0
        assert result.issues_count == 4
        assert result.completed_count == 3
        assert result.predictability is None  # committed=0

    async def test_sprint_without_dates(self, db_session):
        """Sprint with no start/end dates should still produce a summary."""
        today = date.today()
        sprint = DimSprint(
            id=407, board_id=1, name="Dateless", state="future",
            start_date=None, end_date=None, complete_date=None,
        )
        db_session.add(sprint)
        db_session.add(FactIssue(
            jira_id="nodate-0", jira_key="NODATE-0",
            project_key="PROJ",
            summary="Dateless issue",
            issue_type="Story",
            status="To Do", status_category="To Do",
            story_points=5.0,
            sprint_ids=json.dumps([407]),
            created_date=_utc(today - timedelta(days=1)),
            resolved_date=None,
        ))
        await db_session.commit()

        analyzer = SprintAnalyzer("PROJ")
        result = await analyzer.analyze_sprint(db_session, 407)
        assert result is not None
        assert result.total_committed == 5.0
        assert result.total_completed == 0.0
        # 0/5 = 0.0
        assert result.predictability == 0.0
