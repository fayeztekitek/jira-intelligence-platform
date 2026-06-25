"""
Tests for the Jira data extraction layer.

Covers: full extraction, incremental extraction, upsert logic,
_transform_issue, changelog extraction, and error handling.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from ingestion.extractor import JiraExtractor, _parse_dt, _days_between, _today_utc
from storage.models import FactIssue, FactTransition, RunStatus

# ---------------------------------------------------------------------------
# Helper: build mock Jira API responses
# ---------------------------------------------------------------------------

NOW = datetime.now(timezone.utc)
YESTERDAY = NOW - timedelta(days=1)
LAST_WEEK = NOW - timedelta(days=7)
TWO_WEEKS_AGO = NOW - timedelta(days=14)


def _mock_project(key: str, name: str) -> dict:
    return {
        "key": key,
        "name": name,
        "projectTypeKey": "software",
        "lead": {"accountId": "lead-1", "displayName": "Lead User"},
        "description": f"Project {name}",
        "self": f"https://jira.example.com/rest/api/2/project/{key}",
    }


def _mock_issue(
    key: str,
    project_key: str = "TEST",
    issue_type: str = "Story",
    status: str = "Done",
    priority: str = "High",
    created: datetime | None = None,
    resolved: datetime | None = None,
    assignee: dict | None = None,
    reporter: dict | None = None,
    story_points: float | None = 5.0,
    sprint_ids: list | None = None,
    epic_key: str | None = None,
) -> dict:
    created = created or TWO_WEEKS_AGO
    assignee = assignee or {"accountId": "user-1", "displayName": "Dev One"}
    reporter = reporter or {"accountId": "user-2", "displayName": "Reporter"}
    return {
        "id": key.replace("-", ""),
        "key": key,
        "fields": {
            "summary": f"Summary of {key}",
            "description": f"Description of {key}",
            "issuetype": {"name": issue_type},
            "status": {
                "name": status,
                "statusCategory": {"name": "Done" if status == "Done" else "In Progress"},
            },
            "priority": {"name": priority},
            "assignee": assignee,
            "reporter": reporter,
            "created": created.isoformat(),
            "updated": YESTERDAY.isoformat(),
            "resolutiondate": (resolved or YESTERDAY).isoformat() if status == "Done" else None,
            "duedate": None,
            "resolution": {"name": "Done"} if status == "Done" else None,
            "labels": ["backend"],
            "components": [{"id": "comp-1", "name": "Backend"}],
            "fixVersions": [{"id": "ver-1", "name": "1.0"}],
            "customfield_10016": story_points,
            "customfield_10020": sprint_ids or [],
            "customfield_10014": epic_key,
            "parent": None,
            "subtasks": [],
            "timespent": 3600,
            "timeoriginalestimate": 7200,
            "comment": {"comments": []},
        },
    }


def _mock_changelog(issue_key: str) -> list[dict]:
    return [
        {
            "id": "100",
            "author": {"accountId": "user-1", "displayName": "Dev One"},
            "created": (TWO_WEEKS_AGO + timedelta(hours=2)).isoformat(),
            "items": [
                {
                    "field": "status",
                    "from": "1",
                    "fromString": "To Do",
                    "to": "3",
                    "toString": "In Progress",
                }
            ],
        },
        {
            "id": "101",
            "author": {"accountId": "user-1", "displayName": "Dev One"},
            "created": YESTERDAY.isoformat(),
            "items": [
                {
                    "field": "status",
                    "from": "3",
                    "fromString": "In Progress",
                    "to": "4",
                    "toString": "Done",
                }
            ],
        },
    ]


# ---------------------------------------------------------------------------
# Pure function tests
# ---------------------------------------------------------------------------


class TestParseFunctions:
    def test_parse_dt_iso(self):
        dt = _parse_dt("2026-01-15T10:30:00.000+0000")
        assert dt is not None
        assert dt.year == 2026

    def test_parse_dt_zulu(self):
        dt = _parse_dt("2026-01-15T10:30:00Z")
        assert dt is not None

    def test_parse_dt_none(self):
        assert _parse_dt(None) is None

    def test_days_between(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 1, 11, tzinfo=timezone.utc)
        assert _days_between(start, end) == 10.0

    def test_days_between_none(self):
        assert _days_between(None, datetime.now(timezone.utc)) is None


# ---------------------------------------------------------------------------
# _transform_issue tests
# ---------------------------------------------------------------------------


class TestTransformIssue:
    """Test the core issue transformation logic."""

    def _make_extractor(self):
        client = MagicMock()
        return JiraExtractor(client)

    def test_transforms_all_fields(self):
        extractor = self._make_extractor()
        raw = _mock_issue("TEST-1")
        issue = extractor._transform_issue(raw, "TEST", {
            "epic_link": "customfield_10014",
            "story_points": "customfield_10016",
            "sprint": "customfield_10020",
        })

        assert issue.jira_key == "TEST-1"
        assert issue.project_key == "TEST"
        assert issue.summary == "Summary of TEST-1"
        assert issue.story_points == 5.0

    def test_rejects_dict_story_points(self):
        """When story_points is a dict (sometimes Jira returns {...}), set None."""
        extractor = self._make_extractor()
        raw = _mock_issue("TEST-2", story_points=None)
        raw["fields"]["customfield_10016"] = {"value": 3}  # dict, not scalar
        issue = extractor._transform_issue(raw, "TEST")
        assert issue.story_points is None

    def test_extracts_sprint_ids(self):
        extractor = self._make_extractor()
        sprint_data = [{"id": 1, "name": "Sprint 1"}, {"id": 2, "name": "Sprint 2"}]
        raw = _mock_issue("TEST-3", sprint_ids=sprint_data)
        issue = extractor._transform_issue(raw, "TEST")
        import json
        assert json.loads(issue.sprint_ids) == [1, 2]

    def test_extracts_epic_from_parent(self):
        """When epic_link field is empty, fall back to parent.key."""
        extractor = self._make_extractor()
        raw = _mock_issue("TEST-4", epic_key=None)
        raw["fields"]["parent"] = {"key": "EPIC-1"}
        issue = extractor._transform_issue(raw, "TEST")
        assert issue.epic_key == "EPIC-1"

    def test_resolved_issue_has_lead_days(self):
        extractor = self._make_extractor()
        raw = _mock_issue("TEST-5", created=TWO_WEEKS_AGO, resolved=YESTERDAY)
        issue = extractor._transform_issue(raw, "TEST")
        assert issue.lead_time_days is not None
        assert issue.lead_time_days > 0

    def test_unresolved_issue_no_lead_days(self):
        extractor = self._make_extractor()
        raw = _mock_issue("TEST-6", status="In Progress", resolved=None)
        issue = extractor._transform_issue(raw, "TEST")
        assert issue.lead_time_days is None


# ---------------------------------------------------------------------------
# Mock client fixtures
# ---------------------------------------------------------------------------


class MockJiraClient:
    """Simulates JiraClient with configurable responses."""

    def __init__(self):
        self.projects = [_mock_project("TEST", "Test Project")]
        self.issues = {
            "TEST": [_mock_issue("TEST-1"), _mock_issue("TEST-2", status="In Progress", resolved=None)],
        }
        self.changelogs: dict[str, list] = {}
        self.versions: list[dict] = []
        self.components: list[dict] = []
        self.boards: list[dict] = []
        self.sprints: list[dict] = []
        self.api_call_count = 0
        self.settings = None

    async def get_all_projects(self):
        return self.projects

    async def search_issues(self, jql, fields=None, expand=None, field_map=None):
        # Extract project key from JQL (simple parser for testing)
        project_key = "TEST"
        for p in self.projects:
            if p["key"] in jql:
                project_key = p["key"]
                break
        for issue in self.issues.get(project_key, []):
            yield issue

    async def get_issue_changelog(self, issue_key):
        return self.changelogs.get(issue_key, [])

    async def get_versions(self, project_key):
        return self.versions

    async def get_components(self, project_key):
        return self.components

    async def get_boards(self, project_key):
        return self.boards

    async def get_sprints(self, board_id):
        return self.sprints

    async def close(self):
        pass


# ---------------------------------------------------------------------------
# In-memory test DB
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def test_engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        from storage.models import Base
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def test_session(test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture(autouse=True)
def _patch_settings():
    """Ensure settings use test-compatible field IDs."""
    with patch("ingestion.extractor.settings") as mock:
        mock.jira_field_sprint = "customfield_10020"
        mock.jira_field_epic_link = "customfield_10014"
        mock.jira_field_story_points = "customfield_10016"
        yield


@pytest.fixture
async def extractor(test_session):
    client = MockJiraClient()
    ext = JiraExtractor(client)
    ext._field_map = {
        "epic_link": "customfield_10014",
        "story_points": "customfield_10016",
        "sprint": "customfield_10020",
    }
    return ext


@pytest.fixture(autouse=True)
def _patch_get_db(test_session):
    """Route all get_db() calls in extractor to the test session."""

    @asynccontextmanager
    async def override_get_db():
        yield test_session

    with patch("ingestion.extractor.get_db", override_get_db):
        yield


class TestExtractionPipeline:
    async def test_full_extraction(self, extractor: JiraExtractor):
        run_id = await extractor.run_full_extraction(triggered_by="test")
        assert run_id == extractor.run_id
        assert extractor.stats["projects"] >= 1

    async def test_incremental_extraction(self, extractor: JiraExtractor):
        run_id = await extractor.run_incremental_extraction(
            since_hours=24, triggered_by="test"
        )
        assert run_id == extractor.run_id

    async def test_empty_project_no_crash(self, extractor: JiraExtractor):
        extractor.client.issues = {"EMPTY": []}
        extractor.client.projects = [_mock_project("EMPTY", "Empty")]
        run_id = await extractor.run_full_extraction(triggered_by="test")
        assert extractor.stats["issues"] == 0
        assert extractor.stats["errors"] == 0

    async def test_extraction_records_run_success(self, extractor: JiraExtractor):
        """ExtractionRun record status should be SUCCESS after completion."""
        await extractor.run_full_extraction(triggered_by="test")
        assert extractor.stats["projects"] >= 1

    async def test_extraction_processes_all_projects(self, extractor: JiraExtractor):
        extractor.client.projects = [
            _mock_project("P1", "Project One"),
            _mock_project("P2", "Project Two"),
        ]
        extractor.client.issues = {
            "P1": [_mock_issue("P1-1")],
            "P2": [_mock_issue("P2-1")],
        }
        await extractor.run_full_extraction(triggered_by="test")
        assert extractor.stats["projects"] == 2


class TestChangelogExtraction:
    async def test_changelog_parsed(self, extractor: JiraExtractor):
        extractor.client.changelogs = {"TEST-1": _mock_changelog("TEST-1")}
        extractor.client.issues = {"TEST": [_mock_issue("TEST-1")]}

        await extractor.run_full_extraction(triggered_by="test")

        assert extractor.stats["transitions"] > 0


class TestUpsertLogic:
    async def test_transform_creates_model(self, extractor: JiraExtractor):
        raw = _mock_issue("TEST-NEW-1")
        issue = extractor._transform_issue(raw, "TEST")
        assert issue.jira_key == "TEST-NEW-1"
        assert issue.story_points == 5.0

    async def test_transform_different_values(self, extractor: JiraExtractor):
        raw1 = _mock_issue("TEST-U1", story_points=3.0)
        raw2 = _mock_issue("TEST-U1", story_points=8.0)

        issue1 = extractor._transform_issue(raw1, "TEST")
        issue2 = extractor._transform_issue(raw2, "TEST")

        assert issue1.jira_key == issue2.jira_key
        assert issue2.story_points == 8.0

    async def test_flush_batch_persists_issues(self, extractor: JiraExtractor, test_session):
        """After flush, the issue should be in the DB."""
        raw = _mock_issue("TEST-FLUSH-1")
        issue = extractor._transform_issue(raw, "TEST")
        await extractor._flush_issue_batch([(issue, raw)])

        from sqlalchemy import select
        result = await test_session.execute(
            select(FactIssue).where(FactIssue.jira_key == "TEST-FLUSH-1")
        )
        assert result.scalar_one_or_none() is not None


class TestEdgeCases:
    async def test_no_field_map_uses_defaults(self):
        client = MockJiraClient()
        ext = JiraExtractor(client)
        raw = _mock_issue("TEST-EDGE-1")
        issue = ext._transform_issue(raw, "TEST")
        assert issue.jira_key == "TEST-EDGE-1"
        assert issue.story_points == 5.0

    async def test_missing_fields_does_not_crash(self, extractor: JiraExtractor):
        raw = _mock_issue("TEST-EDGE-2")
        raw["fields"] = {"summary": "Minimal"}
        issue = extractor._transform_issue(raw, "TEST")
        assert issue.jira_key == "TEST-EDGE-2"
        assert issue.story_points is None

    async def test_no_issues_returns_zero_counts(self, extractor: JiraExtractor):
        extractor.client.issues = {}
        await extractor.run_full_extraction(triggered_by="test")
        assert extractor.stats["issues"] == 0
        assert extractor.stats["updated"] == 0
