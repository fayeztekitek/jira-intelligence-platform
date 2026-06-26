"""
Integration tests for all API endpoints with seeded test data.
"""
import sys, os, tempfile, uuid
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from storage.database import get_db, Base
from storage.models import (
    DimProject, DimSprint, DimVersion, FactIssue, FactTransition,
    KPIResult, RiskScore, ExtractionRun,
    RiskLevel, TrendDirection, RunStatus,
)
from api.routes import router
from api.auth import _get_admin_key, _init_default_user, USER_STORE, router as auth_router

# ---------------------------------------------------------------------------
# File-based SQLite engine
# ---------------------------------------------------------------------------
_db_file = os.path.join(tempfile.gettempdir(), f"test_int_{uuid.uuid4().hex[:8]}.sqlite")
TEST_ENGINE = create_async_engine(f"sqlite+aiosqlite:///{_db_file}", echo=False)

def _utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# One module-scoped fixture: create tables → seed → yield app → drop tables
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="module")
async def app_with_db():
    print(f"\n[app_with_db] START - DB file exists: {os.path.exists(_db_file)}")

    # 0. Patch get_db so route handlers use TEST_ENGINE instead of production engine
    import api.routes as rts

    @asynccontextmanager
    async def _test_get_db():
        async with AsyncSession(TEST_ENGINE, expire_on_commit=False) as session:
            yield session

    rts.get_db = _test_get_db

    # 1. create tables
    async with TEST_ENGINE.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 2. seed data via a fresh session (the only session that writes)
    async with AsyncSession(TEST_ENGINE, expire_on_commit=False) as db:
        today = date.today()

        for tbl in reversed(Base.metadata.sorted_tables):
            await db.execute(tbl.delete())
        await db.commit()

        db.add(DimProject(id="CORE", name="Core Platform", is_active=True))
        db.add(DimProject(id="MOBILE", name="Mobile App", is_active=True))
        db.add(DimProject(id="INACTIVE", name="Old Project", is_active=False))
        db.add(DimSprint(id=1, name="Sprint 1", state="closed",
                          start_date=_utc(today - timedelta(days=30)),
                          end_date=_utc(today - timedelta(days=16))))
        db.add(DimSprint(id=2, name="Sprint 2", state="active",
                          start_date=_utc(today - timedelta(days=15)),
                          end_date=_utc(today + timedelta(days=5))))
        db.add(DimVersion(id="V1", project_key="CORE", name="v1.0",
                           release_date=today - timedelta(days=10), is_released=True))
        db.add(DimVersion(id="V2", project_key="CORE", name="v2.0",
                           release_date=today + timedelta(days=30), is_released=False))
        await db.flush()

        for row in [
            ("CORE-1","CORE","Login bug","Bug","In Progress","In Progress",
             "Critical","u001",10,None,True,False,3,2,5.0,20,'["V1"]','[]'),
            ("CORE-2","CORE","Homepage","Story","Done","Done",
             "Medium","u002",5,_utc(today-timedelta(days=2)),False,False,0,0,3.0,30,'["V1","V2"]','[]'),
            ("CORE-3","CORE","Footer fix","Bug","To Do","To Do",
             "Major",None,3,None,False,True,0,0,None,8,'[]','["blocked"]'),
            ("CORE-4","CORE","Auth module","Task","Done","Done",
             "High","u001",20,_utc(today-timedelta(days=5)),False,False,1,0,8.0,25,'["V2"]','[]'),
            ("MOBILE-1","MOBILE","App crash","Bug","In Progress","In Progress",
             "Blocker","u003",7,None,True,False,2,5,None,15,'["V1"]','[]'),
        ]:
            jid,pk,summary,itype,status,scat,pri,assignee,age,resolved,overdue,dq,reopened,status_age,sp,created_offset,vids,labels = row
            db.add(FactIssue(jira_id=jid, jira_key=jid, project_key=pk,
                summary=summary, issue_type=itype, status=status,
                status_category=scat, priority=pri, assignee_id=assignee,
                created_date=_utc(today-timedelta(days=created_offset)),
                resolved_date=resolved, age_days=age, resolution_time_days=sp,
                times_reopened=reopened, is_overdue=overdue,
                dq_missing_assignee=dq, current_status_age_days=status_age,
                fix_version_ids=vids, sprint_ids='[1,2]', story_points=sp,
                labels=labels))

        db.add(FactTransition(jira_key="CORE-2", changelog_id="cl-1",
            field="status", from_string="To Do", to_string="Done",
            changed_at=_utc(today-timedelta(days=3))))
        db.add(FactTransition(jira_key="CORE-4", changelog_id="cl-2",
            field="status", from_string="In Progress", to_string="Done",
            changed_at=_utc(today-timedelta(days=6))))

        for cat,name,val in [
            ("delivery","issues_created",15), ("delivery","issues_resolved",8),
            ("delivery","resolution_rate",53.3), ("delivery","overdue_count",2),
            ("quality","bugs_created",5), ("quality","critical_bugs_open",2),
            ("quality","reopen_rate",12.5), ("risk","critical_open",2),
            ("risk","unassigned_open",1), ("risk","blocked_critical_open",1),
            ("risk","aging_critical_open",1), ("risk","sla_at_risk",1),
            ("data_quality","dq_score",85.0), ("team","active_contributors",3),
        ]:
            db.add(KPIResult(project_key="CORE", kpi_name=name, kpi_category=cat,
                period_label="1m", calculation_date=today,
                current_value=val, previous_value=val*0.9,
                trend=TrendDirection.STABLE, risk_level=RiskLevel.LOW))

        db.add(RiskScore(project_key="CORE", calculation_date=today, period_label="1m",
            delivery_risk=30.0, quality_risk=45.0, compliance_risk=20.0,
            operational_risk=25.0, composite_risk=32.5, risk_level=RiskLevel.MEDIUM,
            risk_drivers=json.dumps(["2 critical bugs open","1 unassigned"]),
            recommended_actions=json.dumps(["Assign open issues","Triage critical bugs"])))

        db.add(ExtractionRun(run_id="run-1", run_type="incremental", triggered_by="scheduler",
            status=RunStatus.SUCCESS,
            started_at=datetime.now(timezone.utc)-timedelta(hours=2),
            completed_at=datetime.now(timezone.utc)-timedelta(hours=1,minutes=55),
            projects_processed=2, issues_extracted=5, duration_seconds=300))

        await db.commit()

    # 3. build FastAPI app (no dep override needed — routes call get_db() directly)
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(router)
    yield app

    # 4. teardown
    async with TEST_ENGINE.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    del rts.get_db


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(autouse=True)
def reset_auth():
    import api.auth as auth_mod
    auth_mod._ADMIN_API_KEY = None
    auth_mod.USER_STORE.clear()
    auth_mod._init_default_user()


@pytest_asyncio.fixture(scope="module")
def admin_key() -> str:
    return _get_admin_key()


@pytest_asyncio.fixture(scope="module")
async def client(app_with_db) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app_with_db)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture(scope="module")
async def auth_headers(client: AsyncClient) -> dict[str, str]:
    resp = await client.post("/api/auth/login", json={"api_key": _get_admin_key()})
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ===================================================================
# Tests
# ===================================================================

class TestHealth:
    async def test_health(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/health", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    async def test_health_no_auth(self, client: AsyncClient):
        resp = await client.get("/api/health")
        assert resp.status_code == 200  # health is public


class TestProjects:
    async def test_list_projects(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/projects", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        keys = [p["key"] for p in data]
        assert "CORE" in keys
        assert "MOBILE" in keys
        assert "INACTIVE" not in keys  # filtered by is_active

    async def test_get_project(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/projects/CORE", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "Core Platform"

    async def test_get_project_not_found(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/projects/DOES_NOT_EXIST", headers=auth_headers)
        assert resp.status_code == 404


class TestKPIs:
    async def test_get_kpis(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/projects/CORE/kpis?period=1m", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["project_key"] == "CORE"
        assert len(body["kpis"]) >= 10
        names = [k["name"] for k in body["kpis"]]
        assert "issues_created" in names
        assert "critical_open" in names

    async def test_get_kpis_by_category(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/projects/CORE/kpis?period=1m&category=quality", headers=auth_headers)
        assert resp.status_code == 200
        for k in resp.json()["kpis"]:
            assert k["category"] == "quality"

    async def test_get_kpis_no_data(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/projects/MOBILE/kpis?period=1m", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["kpis"] == []

    async def test_kpis_no_auth(self, client: AsyncClient):
        resp = await client.get("/api/projects/CORE/kpis?period=1m")
        assert resp.status_code == 401

    async def test_kpi_history(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/kpis/history?project_key=CORE&kpi_name=issues_created&days=30", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["kpi_name"] == "issues_created"
        assert len(body["history"]) >= 1
        assert "date" in body["history"][0]
        assert "value" in body["history"][0]


class TestRisk:
    async def test_get_risk(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/projects/CORE/risk", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "composite_risk" in body
        assert "dimensions" in body
        assert "delivery" in body["dimensions"]

    async def test_get_risk_with_period(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/projects/CORE/risk?period=1m", headers=auth_headers)
        assert resp.status_code == 200

    async def test_get_risk_not_found(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/projects/MOBILE/risk", headers=auth_headers)
        assert resp.status_code == 404

    async def test_risk_history(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/projects/CORE/risk/history?days=90", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "history" in body
        if body["history"]:
            entry = body["history"][0]
            assert "date" in entry
            assert "composite_risk" in entry
            assert "delivery" in entry

    async def test_risk_history_empty(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/projects/MOBILE/risk/history", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["history"] == []


class TestIssues:
    async def test_list_issues(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/projects/CORE/issues", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 3
        assert len(body["issues"]) >= 3

    async def test_issues_filter_by_type(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/projects/CORE/issues?issue_type=Bug", headers=auth_headers)
        assert resp.status_code == 200
        for i in resp.json()["issues"]:
            assert i["type"] == "Bug"

    async def test_issues_filter_overdue(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/projects/CORE/issues?overdue_only=true", headers=auth_headers)
        assert resp.status_code == 200
        for i in resp.json()["issues"]:
            assert i["is_overdue"] is True


class TestSprints:
    async def test_list_sprints(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/projects/CORE/sprints", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    async def test_sprint_burndown(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/sprints/1/burndown", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "burndown" in body
        if body["burndown"]:
            assert "remaining_points" in body["burndown"][0]
            assert "ideal_points" in body["burndown"][0]

    async def test_sprint_burndown_not_found(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/sprints/999/burndown", headers=auth_headers)
        assert resp.status_code == 404


class TestReleases:
    async def test_list_releases(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/projects/CORE/releases", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) >= 1
        names = [r["version_name"] for r in resp.json()]
        assert "v1.0" in names

    async def test_release_burndown(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/versions/V1/burndown", headers=auth_headers)
        assert resp.status_code == 200
        assert "burndown" in resp.json()

    async def test_release_burndown_not_found(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/versions/DOES_NOT_EXIST/burndown", headers=auth_headers)
        assert resp.status_code == 404


class TestSnapshots:
    async def test_list_snapshots(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/projects/CORE/snapshots?days=30", headers=auth_headers)
        assert resp.status_code == 200
        assert "snapshots" in resp.json()


class TestExecutive:
    async def test_executive_summary(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/executive/summary", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "overall" in body
        assert "projects" in body
        assert "alerts" in body
        assert body["overall"]["total_projects"] >= 1


class TestExport:
    async def test_export_csv(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/export/csv?project_key=CORE", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/csv; charset=utf-8"
        assert resp.headers["content-disposition"].startswith("attachment")
        text = resp.text
        assert text.startswith("Key")  # header row
        assert "CORE-1" in text

    async def test_export_csv_max_rows(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/export/csv?project_key=CORE&max_rows=1", headers=auth_headers)
        assert resp.status_code == 200
        lines = resp.text.strip().split("\n")
        assert len(lines) == 2  # header + 1 row

    async def test_export_xlsx(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/export/xlsx?project_key=CORE", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert len(resp.content) > 100  # valid xlsx

    async def test_export_pdf(self, client: AsyncClient, auth_headers: dict):
        try:
            from weasyprint import HTML  # noqa: F401
        except OSError:
            pytest.skip("WeasyPrint native libraries not available")
        resp = await client.get("/api/export/pdf?project_key=CORE", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content.startswith(b"%PDF")

    async def test_export_no_auth(self, client: AsyncClient):
        resp = await client.get("/api/export/csv?project_key=CORE")
        assert resp.status_code == 401


class TestSync:
    async def test_sync_status(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/sync/status?limit=5", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["runs"]) >= 1
        assert body["runs"][0]["run_id"] == "run-1"

    async def test_trigger_sync(self, client: AsyncClient, auth_headers: dict):
        resp = await client.post("/api/sync/trigger?sync_type=incremental", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "triggered"


class TestErrorCases:
    async def test_404_unknown_endpoint(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/nonexistent", headers=auth_headers)
        assert resp.status_code == 404

    async def test_422_invalid_param(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get("/api/projects/CORE/issues?limit=999999", headers=auth_headers)
        assert resp.status_code == 422
