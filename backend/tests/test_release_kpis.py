"""
tests/test_release_kpis.py — Unit tests for ReleaseAnalyzer.

Run: pytest tests/test_release_kpis.py -v
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

from kpi_engine.release import ReleaseAnalyzer, VersionSummary
from storage.models import Base, DimVersion, FactIssue


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
    """Seed DimVersion and FactIssue rows."""
    today = date.today()

    # Version 201 — released, past release_date
    v1 = DimVersion(
        id="PROJ-V201", project_key="PROJ", name="v2.0.1",
        release_date=today - timedelta(days=30),
        is_released=True,
    )
    db_session.add(v1)

    # Version 202 — not yet released, future release_date
    v2 = DimVersion(
        id="PROJ-V202", project_key="PROJ", name="v2.0.2",
        release_date=today + timedelta(days=14),
        is_released=False,
    )
    db_session.add(v2)

    # Version 203 — released, no release_date (historical)
    v3 = DimVersion(
        id="PROJ-V203", project_key="PROJ", name="v2.0.3",
        release_date=None,
        is_released=True,
    )
    db_session.add(v3)

    # Issues for v201 (past release): 3 resolved + 1 unresolved (delayed) + 1 scope increase
    for i in range(3):
        db_session.add(FactIssue(
            jira_id=f"v201-ok-{i}", jira_key=f"V201-OK-{i}",
            project_key="PROJ",
            summary=f"v201 resolved {i}",
            issue_type="Story",
            status="Done",
            status_category="Done",
            story_points=5.0,
            fix_version_ids=json.dumps(["PROJ-V201"]),
            created_date=_utc(today - timedelta(days=60)),
            resolved_date=_utc(today - timedelta(days=35)),
        ))
    # Delayed: created before release date but not resolved
    db_session.add(FactIssue(
        jira_id="v201-delayed", jira_key="V201-DELAY",
        project_key="PROJ",
        summary="v201 delayed issue",
        issue_type="Bug",
        status="In Progress",
        status_category="In Progress",
        story_points=3.0,
        fix_version_ids=json.dumps(["PROJ-V201"]),
        created_date=_utc(today - timedelta(days=45)),
        resolved_date=None,
    ))
    # Scope increase: created after release date
    db_session.add(FactIssue(
        jira_id="v201-scope", jira_key="V201-SCOPE",
        project_key="PROJ",
        summary="v201 scope increase",
        issue_type="Story",
        status="To Do",
        status_category="To Do",
        story_points=8.0,
        fix_version_ids=json.dumps(["PROJ-V201"]),
        created_date=_utc(today - timedelta(days=20)),
        resolved_date=None,
    ))

    # Issues for v202 (future release): 2 resolved ahead, 1 in progress
    for i in range(2):
        db_session.add(FactIssue(
            jira_id=f"v202-ok-{i}", jira_key=f"V202-OK-{i}",
            project_key="PROJ",
            summary=f"v202 resolved early {i}",
            issue_type="Story",
            status="Done",
            status_category="Done",
            story_points=3.0,
            fix_version_ids=json.dumps(["PROJ-V202"]),
            created_date=_utc(today - timedelta(days=10)),
            resolved_date=_utc(today - timedelta(days=5)),
        ))
    db_session.add(FactIssue(
        jira_id="v202-wip", jira_key="V202-WIP",
        project_key="PROJ",
        summary="v202 in progress",
        issue_type="Task",
        status="In Progress",
        status_category="In Progress",
        story_points=4.0,
        fix_version_ids=json.dumps(["PROJ-V202"]),
        created_date=_utc(today - timedelta(days=8)),
        resolved_date=None,
    ))

    # Issues for v203 (no release_date): 2 resolved, 1 open (no delay since no release_date)
    for i in range(2):
        db_session.add(FactIssue(
            jira_id=f"v203-ok-{i}", jira_key=f"V203-OK-{i}",
            project_key="PROJ",
            summary=f"v203 done {i}",
            issue_type="Story",
            status="Done",
            status_category="Done",
            story_points=2.0,
            fix_version_ids=json.dumps(["PROJ-V203"]),
            created_date=_utc(today - timedelta(days=90)),
            resolved_date=_utc(today - timedelta(days=60)),
        ))
    db_session.add(FactIssue(
        jira_id="v203-open", jira_key="V203-OPEN",
        project_key="PROJ",
        summary="v203 still open",
        issue_type="Story",
        status="To Do",
        status_category="To Do",
        story_points=5.0,
        fix_version_ids=json.dumps(["PROJ-V203"]),
        created_date=_utc(today - timedelta(days=85)),
        resolved_date=None,
    ))

    await db_session.commit()


class TestReleaseAnalyzer:

    async def test_analyze_returns_all_versions(self, db_session):
        analyzer = ReleaseAnalyzer("PROJ")
        results = await analyzer.analyze(db_session)
        assert len(results) == 3

    async def test_v201_completion_and_delays(self, db_session):
        analyzer = ReleaseAnalyzer("PROJ")
        results = await analyzer.analyze(db_session)
        v201 = next(r for r in results if r.version_id == "PROJ-V201")
        assert v201.version_name == "v2.0.1"
        assert v201.is_released is True
        assert v201.total_issues == 5
        assert v201.resolved_issues == 3
        assert v201.unresolved_issues == 2
        assert v201.completion_pct == 60.0  # 3/5
        assert v201.delayed_issues == 2     # both unresolved issues past release date
        assert v201.scope_increase == 1     # V201-SCOPE created after release date

    async def test_v201_readiness(self, db_session):
        analyzer = ReleaseAnalyzer("PROJ")
        results = await analyzer.analyze(db_session)
        v201 = next(r for r in results if r.version_id == "PROJ-V201")
        assert v201.readiness_score is not None
        # completion=60%, delay_penalty=40% (2/5=0.4), readiness=60-40=20
        assert v201.readiness_score == 20.0
        assert v201.is_overdue is True

    async def test_v202_future_release(self, db_session):
        analyzer = ReleaseAnalyzer("PROJ")
        results = await analyzer.analyze(db_session)
        v202 = next(r for r in results if r.version_id == "PROJ-V202")
        assert v202.total_issues == 3
        assert v202.resolved_issues == 2
        assert v202.unresolved_issues == 1
        assert v202.completion_pct == 66.7  # 2/3 = 66.7
        assert v202.delayed_issues == 0     # future release → no delayed issues
        assert v202.scope_increase == 0     # all issues created before release date
        assert v202.is_released is False
        assert v202.is_overdue is False
        assert v202.readiness_score == 66.7  # no delay penalty

    async def test_v203_no_release_date(self, db_session):
        analyzer = ReleaseAnalyzer("PROJ")
        results = await analyzer.analyze(db_session)
        v203 = next(r for r in results if r.version_id == "PROJ-V203")
        assert v203.total_issues == 3
        assert v203.resolved_issues == 2
        assert v203.completion_pct == 66.7
        # No release_date, so no scope_increase or delayed_issues
        assert v203.scope_increase == 0
        assert v203.delayed_issues == 0
        # readiness = completion (no delay penalty)
        assert v203.readiness_score == 66.7

    async def test_analyze_single_version(self, db_session):
        analyzer = ReleaseAnalyzer("PROJ")
        result = await analyzer.analyze_version(db_session, "PROJ-V201")
        assert result is not None
        assert result.version_name == "v2.0.1"
        assert result.total_issues == 5

    async def test_analyze_nonexistent_version(self, db_session):
        analyzer = ReleaseAnalyzer("PROJ")
        result = await analyzer.analyze_version(db_session, "PROJ-NOPE")
        assert result is None

    async def test_to_dict(self):
        summary = VersionSummary(
            version_id="V1", version_name="v1.0", project_key="PROJ",
            release_date=date(2026, 6, 1), is_released=True, is_overdue=False,
            total_issues=10, resolved_issues=7, unresolved_issues=3,
            completion_pct=70.0, scope_increase=1, delayed_issues=2,
            readiness_score=50.0, total_story_points=20.0, completed_story_points=14.0,
        )
        d = summary.to_dict()
        assert d["version_id"] == "V1"
        assert d["completion_pct"] == 70.0
        assert d["readiness_score"] == 50.0

    async def test_parse_version_ids(self):
        assert ReleaseAnalyzer._parse_version_ids('["V1", "V2"]') == ["V1", "V2"]
        assert ReleaseAnalyzer._parse_version_ids("[]") == []
        assert ReleaseAnalyzer._parse_version_ids(None) == []
        assert ReleaseAnalyzer._parse_version_ids("invalid") == []

    async def test_v201_story_points(self, db_session):
        analyzer = ReleaseAnalyzer("PROJ")
        results = await analyzer.analyze(db_session)
        v201 = next(r for r in results if r.version_id == "PROJ-V201")
        # 3*5 (resolved) + 3 (delayed) + 8 (scope) = 26
        assert v201.total_story_points == 26.0
        # resolved SP: 3*5 = 15
        assert v201.completed_story_points == 15.0

    async def test_no_matching_project(self, db_session):
        analyzer = ReleaseAnalyzer("OTHER")
        results = await analyzer.analyze(db_session)
        assert len(results) == 0

    async def test_empty_version_handled(self, db_session):
        """A version with no issues should still appear with zero counts."""
        db_session.add(DimVersion(
            id="PROJ-V204", project_key="PROJ", name="v2.0.4",
            release_date=date.today(), is_released=False,
        ))
        await db_session.commit()
        analyzer = ReleaseAnalyzer("PROJ")
        results = await analyzer.analyze(db_session)
        v204 = next((r for r in results if r.version_id == "PROJ-V204"), None)
        assert v204 is not None
        assert v204.total_issues == 0
        assert v204.completion_pct is None
        assert v204.readiness_score is None
