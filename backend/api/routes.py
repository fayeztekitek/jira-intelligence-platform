"""
api/routes.py — All FastAPI routes.

Endpoints:
  GET  /api/health
  GET  /api/projects
  GET  /api/projects/{key}/kpis
  GET  /api/projects/{key}/risk
  GET  /api/projects/{key}/issues
  GET  /api/executive/summary
  GET  /api/executive/top-risks
  GET  /api/kpis/history
  POST /api/sync/trigger
  GET  /api/sync/status
  GET  /api/export/csv
"""
from __future__ import annotations

import csv
import io
import json
import os
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

import structlog

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, func, desc, and_
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import require_auth, require_admin, UserSession
from jira_connector.client import JiraClient
from jira_connector.fields import FieldDiscoverer
from storage.database import get_db
from storage.models import (
    DimProject, DimVersion, FactIssue, FactTransition,
    KPIResult, RiskScore, ExtractionRun, FactSnapshot,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@router.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


# ---------------------------------------------------------------------------
# Field discovery
# ---------------------------------------------------------------------------


@router.get("/fields")
async def list_fields(user: AuthDep):
    """Return the discovered Jira customfield ID mappings."""
    client = JiraClient()
    try:
        discoverer = FieldDiscoverer(client)
        field_map = await discoverer.get_field_map()
        return field_map
    finally:
        await client.close()


# ─── Auth dependency alias ────────────────────────────────────────────

AuthDep = Annotated[UserSession, Depends(require_auth)]


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

@router.get("/projects")
async def list_projects(user: AuthDep):
    async with get_db() as db:
        rows = (await db.execute(
            select(DimProject).where(DimProject.is_active == True)
            .order_by(DimProject.name)
        )).scalars().all()
    return [
        {
            "key": p.id,
            "name": p.name,
            "type": p.project_type,
            "lead": p.lead_display_name,
            "description": p.description,
        }
        for p in rows
    ]


@router.get("/projects/{project_key}")
async def get_project(project_key: str, user: AuthDep):
    async with get_db() as db:
        proj = (await db.execute(
            select(DimProject).where(DimProject.id == project_key)
        )).scalar_one_or_none()
    if not proj:
        raise HTTPException(404, f"Project {project_key} not found")
    return {
        "key": proj.id,
        "name": proj.name,
        "type": proj.project_type,
        "lead": proj.lead_display_name,
        "description": proj.description,
    }


# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------

@router.get("/projects/{project_key}/kpis")
async def get_project_kpis(
    user: AuthDep,
    project_key: str,
    period: str = Query("1m", description="Period: 1d|1w|2w|3w|4w|1m|3m|6m|9m|1y"),
    category: str | None = Query(None, description="Filter by category"),
    as_of: str | None = Query(None, description="Date YYYY-MM-DD (default: latest)"),
):
    calc_date = date.fromisoformat(as_of) if as_of else None

    async with get_db() as db:
        q = select(KPIResult).where(
            KPIResult.project_key == project_key,
            KPIResult.period_label == period,
        )
        if category:
            q = q.where(KPIResult.kpi_category == category)
        if calc_date:
            q = q.where(KPIResult.calculation_date == calc_date)
        else:
            # Latest available
            subq = (
                select(func.max(KPIResult.calculation_date))
                .where(KPIResult.project_key == project_key)
                .scalar_subquery()
            )
            q = q.where(KPIResult.calculation_date == subq)

        rows = (await db.execute(q.order_by(KPIResult.kpi_category, KPIResult.kpi_name))).scalars().all()

    return {
        "project_key": project_key,
        "period": period,
        "kpis": [
            {
                "name": r.kpi_name,
                "category": r.kpi_category,
                "current_value": r.current_value,
                "previous_value": r.previous_value,
                "delta": r.delta,
                "delta_pct": r.delta_pct,
                "trend": r.trend.value if r.trend else "unknown",
                "risk_level": r.risk_level.value if r.risk_level else "low",
                "unit": "",
                "formula": r.formula,
                "interpretation": r.interpretation,
                "recommended_action": r.recommended_action,
                "calculated_at": r.calculation_date.isoformat() if r.calculation_date else None,
            }
            for r in rows
        ],
    }


@router.get("/kpis/history")
async def get_kpi_history(
    user: AuthDep,
    project_key: str = Query(...),
    kpi_name: str = Query(...),
    period: str = Query("1m"),
    days: int = Query(90, description="How many days of history"),
):
    since = date.today() - timedelta(days=days)
    async with get_db() as db:
        rows = (await db.execute(
            select(KPIResult)
            .where(
                KPIResult.project_key == project_key,
                KPIResult.kpi_name == kpi_name,
                KPIResult.period_label == period,
                KPIResult.calculation_date >= since,
            )
            .order_by(KPIResult.calculation_date)
        )).scalars().all()

    return {
        "project_key": project_key,
        "kpi_name": kpi_name,
        "period": period,
        "history": [
            {
                "date": r.calculation_date.isoformat(),
                "value": r.current_value,
                "trend": r.trend.value if r.trend else "unknown",
                "risk_level": r.risk_level.value if r.risk_level else "low",
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# Sprint analytics
# ---------------------------------------------------------------------------


@router.get("/projects/{project_key}/sprints")
async def get_project_sprints(
    user: AuthDep,
    project_key: str,
    sprint_id: int | None = Query(None),
):
    from kpi_engine.sprint import SprintAnalyzer

    analyzer = SprintAnalyzer(project_key)
    async with get_db() as db:
        if sprint_id:
            result = await analyzer.analyze_sprint(db, sprint_id)
            if result is None:
                raise HTTPException(status_code=404, detail="Sprint not found")
            return result.to_dict()
        results = await analyzer.analyze(db)
        return [r.to_dict() for r in results]


@router.get("/sprints/{sprint_id}/burndown")
async def get_sprint_burndown(
    user: AuthDep,
    sprint_id: int,
):
    from storage.repositories import SprintBurndownRepository

    async with get_db() as db:
        repo = SprintBurndownRepository(db)
        data = await repo.get_burndown(sprint_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Sprint not found")
    return {"sprint_id": sprint_id, "burndown": data}


# ---------------------------------------------------------------------------
# Release analytics
# ---------------------------------------------------------------------------


@router.get("/projects/{project_key}/releases")
async def get_project_releases(
    user: AuthDep,
    project_key: str,
    version_id: str | None = Query(None),
):
    from kpi_engine.release import ReleaseAnalyzer

    analyzer = ReleaseAnalyzer(project_key)
    async with get_db() as db:
        if version_id:
            result = await analyzer.analyze_version(db, version_id)
            if result is None:
                raise HTTPException(status_code=404, detail="Version not found")
            return result.to_dict()
        results = await analyzer.analyze(db)
        return [r.to_dict() for r in results]


@router.get("/versions/{version_id}/burndown")
async def get_version_burndown(
    user: AuthDep,
    version_id: str,
):
    """Daily open vs resolved issue count for a fix version."""
    async with get_db() as db:
        version = await db.get(DimVersion, version_id)
        if version is None:
            raise HTTPException(status_code=404, detail="Version not found")

        all_issues = (await db.execute(
            select(FactIssue).where(
                FactIssue.fix_version_ids.isnot(None),
                FactIssue.fix_version_ids != "[]",
            )
        )).scalars().all()

        version_issues = [
            i for i in all_issues
            if version_id in json.loads(i.fix_version_ids or "[]")
        ]

        if not version_issues:
            return {"version_id": version_id, "burndown": []}

        # Earliest created date as chart start
        created_dates = [i.created_date for i in version_issues if i.created_date]
        if not created_dates:
            return {"version_id": version_id, "burndown": []}

        start = min(d.date() if hasattr(d, "date") else d for d in created_dates)
        end = date.today()

        # Collect resolution events for version issues
        jira_keys = [i.jira_key for i in version_issues]
        transitions_result = await db.execute(
            select(FactTransition).where(
                FactTransition.jira_key.in_(jira_keys),
                FactTransition.field == "status",
                FactTransition.to_string.in_(["Done", "Closed"]),
            ).order_by(FactTransition.changed_at.asc())
        )
        resolve_events = transitions_result.scalars().all()

        resolved_by_day: dict[date, int] = {}
        issue_resolved: set[str] = set()
        for t in resolve_events:
            if t.changed_at is None:
                continue
            d = t.changed_at.date() if hasattr(t.changed_at, "date") else t.changed_at
            if t.jira_key in issue_resolved:
                continue
            issue_resolved.add(t.jira_key)
            resolved_by_day[d] = resolved_by_day.get(d, 0) + 1

        total = len(version_issues)
        burndown: list[dict] = []
        cumulative_resolved = 0
        current = start
        while current <= end:
            cumulative_resolved += resolved_by_day.get(current, 0)
            open_count = total - cumulative_resolved
            burndown.append({
                "date": current.isoformat(),
                "open": open_count,
                "resolved": cumulative_resolved,
            })
            current += timedelta(days=1)

    return {"version_id": version_id, "burndown": burndown}


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


@router.get("/projects/{project_key}/snapshots")
async def get_project_snapshots(
    user: AuthDep,
    project_key: str,
    days: int = Query(90, description="Days of history to return"),
    period_type: str = Query("daily", description="daily|weekly|monthly"),
):
    from storage.models import FactSnapshot

    since = date.today() - timedelta(days=days)
    async with get_db() as db:
        rows = (await db.execute(
            select(FactSnapshot).where(
                FactSnapshot.project_key == project_key,
                FactSnapshot.snapshot_date >= since,
                FactSnapshot.period_type == period_type,
            ).order_by(FactSnapshot.snapshot_date.asc())
        )).scalars().all()

    return {
        "project_key": project_key,
        "period_type": period_type,
        "snapshots": [
            {
                "date": r.snapshot_date.isoformat(),
                "total_open": r.total_open,
                "total_created": r.total_created,
                "total_resolved": r.total_resolved,
                "resolution_rate": r.resolution_rate,
                "avg_resolution_days": r.avg_resolution_days,
                "avg_cycle_time_days": r.avg_cycle_time_days,
                "throughput": r.throughput,
                "backlog_size": r.backlog_size,
                "wip": r.wip,
                "overdue_count": r.overdue_count,
                "bugs_created": r.bugs_created,
                "bugs_resolved": r.bugs_resolved,
                "bug_resolution_rate": r.bug_resolution_rate,
                "critical_bugs_open": r.critical_bugs_open,
                "dq_score": r.dq_score,
                "risk_score": r.risk_score,
                "risk_level": r.risk_level.value if r.risk_level else "low",
                "sprint_velocity": r.sprint_velocity,
                "sprint_predictability": r.sprint_predictability,
            }
            for r in rows
        ],
    }


@router.get("/projects/{project_key}/risk/history")
async def get_project_risk_history(
    user: AuthDep,
    project_key: str,
    days: int = Query(90, description="Days of history"),
    period: str = Query("1m", description="Period: 1w|1m|3m"),
):
    since = date.today() - timedelta(days=days)
    async with get_db() as db:
        rows = (await db.execute(
            select(RiskScore).where(
                RiskScore.project_key == project_key,
                RiskScore.calculation_date >= since,
                RiskScore.period_label == period,
            ).order_by(RiskScore.calculation_date.asc())
        )).scalars().all()

    return {
        "project_key": project_key,
        "period": period,
        "history": [
            {
                "date": r.calculation_date.isoformat(),
                "composite_risk": r.composite_risk,
                "risk_level": r.risk_level.value if r.risk_level else "low",
                "delivery": r.delivery_risk,
                "quality": r.quality_risk,
                "compliance": r.compliance_risk,
                "operational": r.operational_risk,
            }
            for r in rows
        ],
    }


# Risk
# ---------------------------------------------------------------------------

@router.get("/projects/{project_key}/risk")
async def get_project_risk(
    user: AuthDep,
    project_key: str,
    as_of: str | None = Query(None),
    period: str = Query("1m", description="Period: 1w|1m|3m"),
):
    calc_date = date.fromisoformat(as_of) if as_of else None
    async with get_db() as db:
        q = select(RiskScore).where(
            RiskScore.project_key == project_key,
            RiskScore.period_label == period,
        )
        if calc_date:
            q = q.where(RiskScore.calculation_date == calc_date)
        else:
            subq = (
                select(func.max(RiskScore.calculation_date))
                .where(
                    RiskScore.project_key == project_key,
                    RiskScore.period_label == period,
                )
                .scalar_subquery()
            )
            q = q.where(RiskScore.calculation_date == subq)

        row = (await db.execute(q)).scalar_one_or_none()

    if not row:
        raise HTTPException(404, "No risk score found for this project")

    return {
        "project_key": project_key,
        "period": period,
        "calculated_at": row.calculation_date.isoformat(),
        "composite_risk": row.composite_risk,
        "risk_level": row.risk_level.value if row.risk_level else "low",
        "dimensions": {
            "delivery": row.delivery_risk,
            "quality": row.quality_risk,
            "compliance": row.compliance_risk,
            "operational": row.operational_risk,
        },
        "risk_drivers": json.loads(row.risk_drivers or "[]"),
        "recommended_actions": json.loads(row.recommended_actions or "[]"),
    }


# ---------------------------------------------------------------------------
# Issues (with filters)
# ---------------------------------------------------------------------------

@router.get("/projects/{project_key}/issues")
async def get_project_issues(
    user: AuthDep,
    project_key: str,
    status: str | None = Query(None),
    issue_type: str | None = Query(None),
    priority: str | None = Query(None),
    assignee_id: str | None = Query(None),
    overdue_only: bool = Query(False),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
):
    async with get_db() as db:
        q = select(FactIssue).where(FactIssue.project_key == project_key)
        if status:
            q = q.where(FactIssue.status == status)
        if issue_type:
            q = q.where(FactIssue.issue_type == issue_type)
        if priority:
            q = q.where(FactIssue.priority == priority)
        if assignee_id:
            q = q.where(FactIssue.assignee_id == assignee_id)
        if overdue_only:
            q = q.where(FactIssue.is_overdue == True)

        count_q = select(func.count()).select_from(q.subquery())
        total = (await db.execute(count_q)).scalar()

        q = q.order_by(desc(FactIssue.created_date)).limit(limit).offset(offset)
        rows = (await db.execute(q)).scalars().all()

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "issues": [
            {
                "key": r.jira_key,
                "summary": r.summary,
                "type": r.issue_type,
                "status": r.status,
                "status_category": r.status_category,
                "priority": r.priority,
                "assignee_id": r.assignee_id,
                "created_date": r.created_date.isoformat() if r.created_date else None,
                "resolved_date": r.resolved_date.isoformat() if r.resolved_date else None,
                "age_days": r.age_days,
                "resolution_time_days": r.resolution_time_days,
                "is_overdue": r.is_overdue,
                "times_reopened": r.times_reopened,
                "dq_flags": {
                    "missing_assignee": r.dq_missing_assignee,
                    "missing_priority": r.dq_missing_priority,
                    "missing_component": r.dq_missing_component,
                    "missing_fix_version": r.dq_missing_fix_version,
                },
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# Executive summary
# ---------------------------------------------------------------------------

@router.get("/executive/summary")
async def executive_summary(user: AuthDep):
    """
    Top-level executive view answering: What happened? Why? Risk? Impact? Action?
    """
    today = date.today()

    async with get_db() as db:
        # All active projects
        projects = (await db.execute(
            select(DimProject).where(DimProject.is_active == True)
        )).scalars().all()
        project_keys = [p.id for p in projects]

        # Latest risk scores — batch load with a single query
        latest_date_subq = (
            select(
                RiskScore.project_key,
                func.max(RiskScore.calculation_date).label("max_date")
            )
            .where(RiskScore.project_key.in_(project_keys))
            .group_by(RiskScore.project_key)
            .subquery()
        )
        risk_rows = (await db.execute(
            select(RiskScore).join(
                latest_date_subq,
                (RiskScore.project_key == latest_date_subq.c.project_key) &
                (RiskScore.calculation_date == latest_date_subq.c.max_date)
            )
        )).scalars().all()

        # Issue stats
        total_open = (await db.execute(
            select(func.count(FactIssue.id))
            .where(FactIssue.status_category != "Done")
        )).scalar() or 0

        total_overdue = (await db.execute(
            select(func.count(FactIssue.id))
            .where(FactIssue.is_overdue == True)
        )).scalar() or 0

        critical_open = (await db.execute(
            select(func.count(FactIssue.id))
            .where(
                FactIssue.status_category != "Done",
                FactIssue.priority.in_(["Critical", "Blocker", "Highest"]),
            )
        )).scalar() or 0

        unassigned = (await db.execute(
            select(func.count(FactIssue.id))
            .where(
                FactIssue.status_category != "Done",
                FactIssue.assignee_id == None,
            )
        )).scalar() or 0

        # Last sync
        last_run = (await db.execute(
            select(ExtractionRun)
            .order_by(desc(ExtractionRun.started_at))
            .limit(1)
        )).scalar_one_or_none()

    # Build project health list
    project_health = []
    for p in projects:
        risk = next((r for r in risk_rows if r.project_key == p.id), None)
        project_health.append({
            "key": p.id,
            "name": p.name,
            "risk_level": risk.risk_level.value if risk else "unknown",
            "composite_risk": risk.composite_risk if risk else None,
            "risk_drivers": json.loads(risk.risk_drivers or "[]")[:3] if risk else [],
            "recommended_actions": json.loads(risk.recommended_actions or "[]")[:2] if risk else [],
        })

    # Sort by risk
    level_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "unknown": 4}
    project_health.sort(key=lambda x: level_order.get(x["risk_level"], 4))

    # Overall health score
    if risk_rows:
        avg_risk = sum(r.composite_risk for r in risk_rows if r.composite_risk) / len(risk_rows)
        overall_level = "critical" if avg_risk >= 75 else "high" if avg_risk >= 50 else "medium" if avg_risk >= 25 else "low"
    else:
        avg_risk = 0
        overall_level = "unknown"

    return {
        "generated_at": today.isoformat(),
        "last_sync": last_run.started_at.isoformat() if last_run else None,
        "overall": {
            "risk_score": round(avg_risk, 1),
            "risk_level": overall_level,
            "total_open_issues": total_open,
            "total_overdue": total_overdue,
            "critical_open": critical_open,
            "unassigned_open": unassigned,
            "total_projects": len(projects),
        },
        "projects": project_health,
        "top_risks": project_health[:5],
        "alerts": _build_alerts(total_overdue, critical_open, unassigned, avg_risk),
    }


def _build_alerts(overdue: int, critical: int, unassigned: int, risk: float) -> list[dict]:
    alerts = []
    if critical >= 10:
        alerts.append({"level": "critical", "message": f"{critical} critical issues open — immediate attention required"})
    elif critical >= 3:
        alerts.append({"level": "high", "message": f"{critical} critical/blocker issues open"})
    if overdue >= 20:
        alerts.append({"level": "high", "message": f"{overdue} overdue issues across all projects"})
    elif overdue >= 5:
        alerts.append({"level": "medium", "message": f"{overdue} overdue issues need re-planning"})
    if unassigned >= 20:
        alerts.append({"level": "high", "message": f"{unassigned} open issues have no assignee"})
    if risk >= 75:
        alerts.append({"level": "critical", "message": "Portfolio risk is CRITICAL — escalate to leadership"})
    elif risk >= 50:
        alerts.append({"level": "high", "message": "Portfolio risk is HIGH — review in next steering committee"})
    return alerts


# ---------------------------------------------------------------------------
# Sync operations
# ---------------------------------------------------------------------------

@router.post("/sync/trigger")
async def trigger_sync(
    user: AuthDep,
    sync_type: str = Query("incremental", description="incremental|full"),
):
    """Manually trigger a sync (runs in background)."""
    import asyncio
    from scheduler.jobs import job_incremental_extraction, job_full_sync

    if sync_type == "full":
        asyncio.create_task(job_full_sync())
    else:
        asyncio.create_task(job_incremental_extraction())

    return {"status": "triggered", "type": sync_type}


@router.get("/sync/status")
async def sync_status(user: AuthDep, limit: int = Query(10)):
    async with get_db() as db:
        rows = (await db.execute(
            select(ExtractionRun)
            .order_by(desc(ExtractionRun.started_at))
            .limit(limit)
        )).scalars().all()

    return {
        "runs": [
            {
                "run_id": r.run_id,
                "type": r.run_type,
                "triggered_by": r.triggered_by,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "status": r.status.value if r.status else "unknown",
                "issues_extracted": r.issues_extracted,
                "issues_updated": r.issues_updated,
                "duration_seconds": r.duration_seconds,
                "error_count": r.error_count,
                "jira_api_calls": r.jira_api_calls,
            }
            for r in rows
        ]
    }


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

@router.get("/export/xlsx")
async def export_issues_xlsx(
    user: AuthDep,
    project_key: str = Query(...),
):
    from api.export import build_xlsx

    async with get_db() as db:
        kpis = (await db.execute(
            select(KPIResult).where(KPIResult.project_key == project_key)
        )).scalars().all()

        issues = (await db.execute(
            select(FactIssue).where(FactIssue.project_key == project_key)
            .order_by(FactIssue.jira_key)
        )).scalars().all()

    buf = await build_xlsx(project_key, kpis, issues)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={project_key}_export.xlsx"},
    )


@router.get("/export/pdf")
async def export_pdf_report(
    user: AuthDep,
    project_key: str = Query(...),
):
    from api.export import build_pdf

    buf = await build_pdf(project_key)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={project_key}_report.pdf"},
    )

@router.get("/export/csv")
async def export_issues_csv(
    user: AuthDep,
    project_key: str = Query(...),
    issue_type: str | None = Query(None),
    status: str | None = Query(None),
    max_rows: int = Query(10000, description="Maximum rows to export", le=100000),
):
    HEADERS = [
        "Key", "Summary", "Type", "Status", "Priority", "Assignee",
        "Created", "Resolved", "Age (days)", "Resolution Time (days)",
        "Overdue", "Times Reopened", "Missing Assignee", "Missing Priority",
    ]

    async def row_stream():
        hdr = io.StringIO()
        csv.writer(hdr).writerow(HEADERS)
        yield hdr.getvalue()
        offset = 0
        batch = 500
        total_yielded = 0
        while total_yielded < max_rows:
            remaining = max_rows - total_yielded
            do_batch = batch if batch < remaining else remaining
            async with get_db() as db:
                q = select(FactIssue).where(FactIssue.project_key == project_key)
                if issue_type:
                    q = q.where(FactIssue.issue_type == issue_type)
                if status:
                    q = q.where(FactIssue.status == status)
                q = q.order_by(FactIssue.jira_key).limit(do_batch).offset(offset)
                rows = (await db.execute(q)).scalars().all()
            if not rows:
                break
            chunk = io.StringIO()
            w = csv.writer(chunk)
            for r in rows:
                w.writerow([
                    r.jira_key, r.summary, r.issue_type, r.status, r.priority,
                    r.assignee_id or "",
                    r.created_date.isoformat() if r.created_date else "",
                    r.resolved_date.isoformat() if r.resolved_date else "",
                    r.age_days or "",
                    r.resolution_time_days or "",
                    "Yes" if r.is_overdue else "No",
                    r.times_reopened or 0,
                    "Yes" if r.dq_missing_assignee else "No",
                    "Yes" if r.dq_missing_priority else "No",
                ])
            yield chunk.getvalue()
            offset += batch
            total_yielded += len(rows)

    return StreamingResponse(
        row_stream(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={project_key}_issues.csv"},
    )


# ---------------------------------------------------------------------------
# AI Agent
# ---------------------------------------------------------------------------

from collections import defaultdict

_agent_instance: AgentOrchestrator | None = None


def _get_agent() -> AgentOrchestrator:
    global _agent_instance
    if _agent_instance is None:
        from ai_agent.agent import AgentOrchestrator
        _agent_instance = AgentOrchestrator()
    return _agent_instance


_rate_limits: dict[str, list[float]] = defaultdict(list)
_RATE_WINDOW = 60
_RATE_MAX = int(os.getenv("AI_RATE_LIMIT", "20"))
_BURST_MAX = _RATE_MAX + 5


def _check_rate_limit(user_id: str) -> tuple[bool, int]:
    """Token bucket rate limiter. Returns (allowed, retry_after_seconds)."""
    now = time.time()
    timestamps = _rate_limits[user_id]
    cutoff = now - _RATE_WINDOW
    active = [t for t in timestamps if t > cutoff]

    # Allow bursts up to BURST_MAX
    if len(active) < _BURST_MAX:
        _rate_limits[user_id] = active + [now]
        return True, 0

    retry_after = int(_RATE_WINDOW - (now - active[0])) + 1
    return False, max(retry_after, 1)


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    project_id: str | None = Field(None, description="Optional project context")


class SuggestRequest(BaseModel):
    context: dict | None = Field(None, description="Optional context: {project, recent_intent}")


@router.post("/ai/ask")
async def ai_ask(
    user: AuthDep,
    body: ChatRequest,
):
    allowed, retry_after = _check_rate_limit(user.user_id)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded (20 req/min)",
            headers={"Retry-After": str(retry_after)},
        )

    agent = _get_agent()
    result = await agent.ask(body.question)

    response_data = {
        "answer": result["response"],
        "tool_used": result["tool_used"],
        "latency_ms": result["latency_ms"],
    }

    # Track token usage in AiUsage table
    try:
        from storage.models import AiUsage
        from storage.database import get_db

        tokens = sum(len(w) for w in result.get("response", "").split())
        cost_per_1k = 0.01
        cost_est = round(tokens * cost_per_1k / 1000, 6)

        async with get_db() as db:
            usage = AiUsage(
                user_id=user.user_id,
                question=body.question[:500],
                response=result["response"][:500],
                prompt_tokens=len(body.question.split()) * 2,
                completion_tokens=tokens,
                total_tokens=tokens + len(body.question.split()) * 2,
                cost_estimate=cost_est,
                tool_used=result.get("tool_used"),
                latency_ms=result.get("latency_ms"),
            )
            db.add(usage)
            await db.commit()
    except Exception as e:
        logger.warning("ai_usage_log_failed", error=str(e))

    logger.info("ai_ask", user=user.user_id, question_len=len(body.question),
                 response_len=len(result["response"]), latency_ms=result["latency_ms"])

    return response_data


@router.post("/ai/suggest")
async def ai_suggest(
    user: AuthDep,
    body: SuggestRequest,
):
    agent = _get_agent()
    suggestions = agent.suggest_questions(body.context)
    return {"suggestions": suggestions}


@router.get("/ai/recommendations")
async def ai_recommendations(
    user: AuthDep,
    project_id: str = Query(..., min_length=2, max_length=10),
):
    from ai_agent.tools import generate_recommendations
    result = await generate_recommendations(project_id.upper())

    logger.info("ai_recommendations", user=user.user_id, project=project_id,
                 total=result.get("total", 0))

    return result


@router.get("/ai/usage")
async def ai_usage(
    user: AuthDep,
):
    from storage.models import AiUsage
    from storage.database import get_db
    from sqlalchemy import select, func

    async with get_db() as db:
        stmt = select(AiUsage).where(
            AiUsage.user_id == user.user_id,
        ).order_by(AiUsage.created_at.desc()).limit(100)
        rows = (await db.execute(stmt)).scalars().all()

        total_stmt = select(
            func.sum(AiUsage.total_tokens),
            func.sum(AiUsage.cost_estimate),
            func.count(AiUsage.id),
        ).where(AiUsage.user_id == user.user_id)
        totals = (await db.execute(total_stmt)).one()

    return {
        "user_id": user.user_id,
        "total_calls": totals[2] or 0,
        "total_tokens": totals[0] or 0,
        "total_cost": round(totals[1] or 0, 6),
        "recent": [
            {
                "question": r.question[:100],
                "total_tokens": r.total_tokens,
                "cost_estimate": r.cost_estimate,
                "tool_used": r.tool_used,
                "latency_ms": r.latency_ms,
                "created_at": str(r.created_at),
            }
            for r in rows[:20]
        ],
    }


@router.post("/admin/rotate-key")
async def rotate_api_key(user: AuthDep):
    import secrets

    if user.user_id != "admin":
        raise HTTPException(status_code=403, detail="Only admin can rotate the API key")

    new_key = f"jip_{secrets.token_urlsafe(32)}"

    logger.info("api_key_rotated", user=user.user_id)

    return {
        "message": "API key rotated successfully",
        "new_api_key": new_key,
        "note": "Save this key now — it will not be shown again",
    }


# ---------------------------------------------------------------------------
# Retention policy
# ---------------------------------------------------------------------------

class RetentionPolicyUpdate(BaseModel):
    retention_days: int | None = None
    enabled: bool | None = None


# ---------------------------------------------------------------------------
# User management (RBAC)
# ---------------------------------------------------------------------------

class UserCreate(BaseModel):
    user_id: str = Field(min_length=2, max_length=128)
    role: str = Field(default="viewer", pattern="^(admin|analyst|viewer)$")
    projects: list[str] | None = None


class UserUpdate(BaseModel):
    role: str | None = Field(default=None, pattern="^(admin|analyst|viewer)$")
    projects: list[str] | None = None
    is_active: bool | None = None


@router.get("/admin/users")
async def list_users(user: Annotated[UserSession, Depends(require_admin)]):
    from sqlalchemy import select
    from storage.database import get_db
    from storage.models import User

    async with get_db() as db:
        users = (await db.execute(
            select(User).order_by(User.user_id)
        )).scalars().all()

    return [
        {
            "id": u.id,
            "user_id": u.user_id,
            "role": u.role,
            "projects": json.loads(u.projects) if u.projects else None,
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ]


@router.post("/admin/users", status_code=201)
async def create_user(body: UserCreate, user: Annotated[UserSession, Depends(require_admin)]):
    import hashlib
    import secrets
    from sqlalchemy import select
    from storage.database import get_db
    from storage.models import User

    async with get_db() as db:
        existing = (await db.execute(
            select(User).where(User.user_id == body.user_id)
        )).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=409, detail=f"User '{body.user_id}' already exists")

        raw_key = f"jip_{secrets.token_urlsafe(24)}"
        hashed = hashlib.sha256(raw_key.encode()).hexdigest()

        db_user = User(
            user_id=body.user_id,
            api_key_hash=hashed,
            role=body.role,
            projects=json.dumps(body.projects) if body.projects else None,
        )
        db.add(db_user)
        await db.commit()
        await db.refresh(db_user)

    logger.info("user_created", user_id=body.user_id, role=body.role, created_by=user.user_id)

    return {
        "id": db_user.id,
        "user_id": db_user.user_id,
        "role": db_user.role,
        "projects": body.projects,
        "api_key": raw_key,
        "note": "Save the API key now — it will not be shown again",
    }


@router.put("/admin/users/{user_id}")
async def update_user(
    user_id: str,
    body: UserUpdate,
    user: Annotated[UserSession, Depends(require_admin)],
):
    from sqlalchemy import select
    from storage.database import get_db
    from storage.models import User

    async with get_db() as db:
        db_user = (await db.execute(
            select(User).where(User.user_id == user_id)
        )).scalar_one_or_none()
        if not db_user:
            raise HTTPException(status_code=404, detail="User not found")

        if body.role is not None:
            db_user.role = body.role
        if body.projects is not None:
            db_user.projects = json.dumps(body.projects)
        if body.is_active is not None:
            db_user.is_active = body.is_active

        await db.commit()
        await db.refresh(db_user)

    logger.info("user_updated", user_id=user_id, updated_by=user.user_id)

    return {
        "id": db_user.id,
        "user_id": db_user.user_id,
        "role": db_user.role,
        "projects": json.loads(db_user.projects) if db_user.projects else None,
        "is_active": db_user.is_active,
    }


@router.delete("/admin/users/{user_id}")
async def deactivate_user(
    user_id: str,
    user: Annotated[UserSession, Depends(require_admin)],
):
    from sqlalchemy import select
    from storage.database import get_db
    from storage.models import User

    if user_id == user.user_id:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")

    async with get_db() as db:
        db_user = (await db.execute(
            select(User).where(User.user_id == user_id)
        )).scalar_one_or_none()
        if not db_user:
            raise HTTPException(status_code=404, detail="User not found")

        db_user.is_active = False
        await db.commit()

    logger.info("user_deactivated", user_id=user_id, deactivated_by=user.user_id)
    return {"message": f"User '{user_id}' deactivated"}


# ---------------------------------------------------------------------------
# Admin dashboard stats
# ---------------------------------------------------------------------------

@router.get("/admin/dashboard")
async def admin_dashboard(user: Annotated[UserSession, Depends(require_admin)]):
    from sqlalchemy import select, func
    from storage.database import get_db
    from storage.models import (
        JiraInstance, WebhookConfig, User, AuditLog, ExtractionRun,
        DimProject, FactIssue,
    )

    async with get_db() as db:
        instance_count = (await db.execute(
            select(func.count(JiraInstance.id))
        )).scalar() or 0

        webhook_count = (await db.execute(
            select(func.count(WebhookConfig.id)).where(WebhookConfig.is_active == True)
        )).scalar() or 0

        user_count = (await db.execute(
            select(func.count(User.id)).where(User.is_active == True)
        )).scalar() or 0

        project_count = (await db.execute(
            select(func.count(DimProject.id)).where(DimProject.is_active == True)
        )).scalar() or 0

        issue_count = (await db.execute(
            select(func.count(FactIssue.id))
        )).scalar() or 0

        last_sync = (await db.execute(
            select(ExtractionRun.completed_at)
            .where(ExtractionRun.status == "success")
            .order_by(ExtractionRun.completed_at.desc())
            .limit(1)
        )).scalar()

        recent_errors = (await db.execute(
            select(func.count(AuditLog.id))
            .where(
                AuditLog.status_code >= 500,
                AuditLog.timestamp >= func.now() - func.make_interval(hours=24),
            )
        )).scalar() or 0

    return {
        "instances": instance_count,
        "active_webhooks": webhook_count,
        "active_users": user_count,
        "active_projects": project_count,
        "total_issues": issue_count,
        "last_sync_time": last_sync.isoformat() if last_sync else None,
        "errors_last_24h": recent_errors,
        "status": "healthy" if recent_errors < 10 else "degraded",
    }


@router.get("/admin/retention")
async def get_retention_policies(user: AuthDep):
    from sqlalchemy import select
    from storage.database import get_db
    from storage.models import RetentionPolicy

    async with get_db() as db:
        policies = (await db.execute(
            select(RetentionPolicy).order_by(RetentionPolicy.table_name)
        )).scalars().all()

    return [
        {
            "id": p.id,
            "table_name": p.table_name,
            "retention_days": p.retention_days,
            "enabled": p.enabled,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        }
        for p in policies
    ]


@router.put("/admin/retention/{policy_id}")
async def update_retention_policy(
    policy_id: int,
    body: RetentionPolicyUpdate,
    user: AuthDep,
):
    from sqlalchemy import select
    from storage.database import get_db
    from storage.models import RetentionPolicy

    if user.user_id != "admin":
        raise HTTPException(status_code=403, detail="Only admin can modify retention policies")

    async with get_db() as db:
        policy = (await db.execute(
            select(RetentionPolicy).where(RetentionPolicy.id == policy_id)
        )).scalar_one_or_none()
        if not policy:
            raise HTTPException(status_code=404, detail="Policy not found")

        if body.retention_days is not None:
            policy.retention_days = body.retention_days
        if body.enabled is not None:
            policy.enabled = body.enabled
        policy.updated_by = user.user_id

        await db.commit()
        await db.refresh(policy)

    return {
        "id": policy.id,
        "table_name": policy.table_name,
        "retention_days": policy.retention_days,
        "enabled": policy.enabled,
        "updated_at": policy.updated_at.isoformat() if policy.updated_at else None,
    }


# ---------------------------------------------------------------------------
# Webhook management
# ---------------------------------------------------------------------------

class WebhookCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    url: str = Field(max_length=512)
    secret: str | None = None
    events: list[str]
    project_key: str | None = None


class WebhookUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    secret: str | None = None
    events: list[str] | None = None
    project_key: str | None = None
    is_active: bool | None = None


@router.get("/admin/webhooks")
async def list_webhooks(user: Annotated[UserSession, Depends(require_admin)]):
    from sqlalchemy import select
    from storage.database import get_db
    from storage.models import WebhookConfig

    async with get_db() as db:
        webhooks = (await db.execute(
            select(WebhookConfig).order_by(WebhookConfig.name)
        )).scalars().all()

    return [
        {
            "id": w.id,
            "name": w.name,
            "url": w.url,
            "events": json.loads(w.events),
            "project_key": w.project_key,
            "is_active": w.is_active,
            "created_at": w.created_at.isoformat() if w.created_at else None,
        }
        for w in webhooks
    ]


@router.post("/admin/webhooks", status_code=201)
async def create_webhook(body: WebhookCreate, user: Annotated[UserSession, Depends(require_admin)]):
    from storage.database import get_db
    from storage.models import WebhookConfig
    from api.webhooks import EVENT_TYPES

    invalid = [e for e in body.events if e not in EVENT_TYPES]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid event types: {invalid}")

    async with get_db() as db:
        wh = WebhookConfig(
            name=body.name,
            url=body.url,
            secret=body.secret,
            events=json.dumps(body.events),
            project_key=body.project_key,
        )
        db.add(wh)
        await db.commit()
        await db.refresh(wh)

    logger.info("webhook_created", name=body.name, events=body.events, user=user.user_id)

    return {
        "id": wh.id,
        "name": wh.name,
        "url": wh.url,
        "events": body.events,
        "project_key": wh.project_key,
        "is_active": wh.is_active,
    }


@router.put("/admin/webhooks/{webhook_id}")
async def update_webhook(
    webhook_id: int,
    body: WebhookUpdate,
    user: Annotated[UserSession, Depends(require_admin)],
):
    from sqlalchemy import select
    from storage.database import get_db
    from storage.models import WebhookConfig
    from api.webhooks import EVENT_TYPES

    async with get_db() as db:
        wh = (await db.execute(
            select(WebhookConfig).where(WebhookConfig.id == webhook_id)
        )).scalar_one_or_none()
        if not wh:
            raise HTTPException(status_code=404, detail="Webhook not found")

        if body.name is not None:
            wh.name = body.name
        if body.url is not None:
            wh.url = body.url
        if body.secret is not None:
            wh.secret = body.secret
        if body.events is not None:
            invalid = [e for e in body.events if e not in EVENT_TYPES]
            if invalid:
                raise HTTPException(status_code=400, detail=f"Invalid event types: {invalid}")
            wh.events = json.dumps(body.events)
        if body.project_key is not None:
            wh.project_key = body.project_key
        if body.is_active is not None:
            wh.is_active = body.is_active

        await db.commit()
        await db.refresh(wh)

    logger.info("webhook_updated", webhook_id=webhook_id, user=user.user_id)

    return {
        "id": wh.id,
        "name": wh.name,
        "url": wh.url,
        "events": json.loads(wh.events),
        "project_key": wh.project_key,
        "is_active": wh.is_active,
    }


@router.delete("/admin/webhooks/{webhook_id}")
async def delete_webhook(
    webhook_id: int,
    user: Annotated[UserSession, Depends(require_admin)],
):
    from sqlalchemy import select
    from storage.database import get_db
    from storage.models import WebhookConfig

    async with get_db() as db:
        wh = (await db.execute(
            select(WebhookConfig).where(WebhookConfig.id == webhook_id)
        )).scalar_one_or_none()
        if not wh:
            raise HTTPException(status_code=404, detail="Webhook not found")

        await db.delete(wh)
        await db.commit()

    logger.info("webhook_deleted", webhook_id=webhook_id, user=user.user_id)
    return {"message": "Webhook deleted"}


# ---------------------------------------------------------------------------
# Jira instance management (Multi-Jira)
# ---------------------------------------------------------------------------

class JiraInstanceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    base_url: str = Field(max_length=512)
    auth_type: str = Field(default="api_token", pattern="^(api_token|pat)$")
    username: str | None = None
    api_token: str | None = None
    pat: str | None = None
    project_keys: list[str] | None = None


class JiraInstanceUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    auth_type: str | None = None
    username: str | None = None
    api_token: str | None = None
    pat: str | None = None
    project_keys: list[str] | None = None
    is_active: bool | None = None
    sync_enabled: bool | None = None


@router.get("/admin/instances")
async def list_jira_instances(user: Annotated[UserSession, Depends(require_admin)]):
    from sqlalchemy import select
    from storage.database import get_db
    from storage.models import JiraInstance

    async with get_db() as db:
        instances = (await db.execute(
            select(JiraInstance).order_by(JiraInstance.name)
        )).scalars().all()

    return [
        {
            "id": i.id,
            "name": i.name,
            "base_url": i.base_url,
            "auth_type": i.auth_type,
            "project_keys": json.loads(i.project_keys) if i.project_keys else None,
            "is_active": i.is_active,
            "sync_enabled": i.sync_enabled,
            "created_at": i.created_at.isoformat() if i.created_at else None,
        }
        for i in instances
    ]


@router.post("/admin/instances", status_code=201)
async def create_jira_instance(
    body: JiraInstanceCreate,
    user: Annotated[UserSession, Depends(require_admin)],
):
    from storage.database import get_db
    from storage.models import JiraInstance

    async with get_db() as db:
        existing = (await db.execute(
            select(JiraInstance).where(JiraInstance.name == body.name)
        )).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=409, detail=f"Instance '{body.name}' already exists")

        instance = JiraInstance(
            name=body.name,
            base_url=body.base_url,
            auth_type=body.auth_type,
            username=body.username,
            api_token=body.api_token,
            pat=body.pat,
            project_keys=json.dumps(body.project_keys) if body.project_keys else None,
        )
        db.add(instance)
        await db.commit()
        await db.refresh(instance)

    logger.info("jira_instance_created", name=body.name, user=user.user_id)

    return {
        "id": instance.id,
        "name": instance.name,
        "base_url": instance.base_url,
        "auth_type": instance.auth_type,
        "project_keys": json.loads(instance.project_keys) if instance.project_keys else None,
        "is_active": instance.is_active,
        "sync_enabled": instance.sync_enabled,
    }


@router.put("/admin/instances/{instance_id}")
async def update_jira_instance(
    instance_id: int,
    body: JiraInstanceUpdate,
    user: Annotated[UserSession, Depends(require_admin)],
):
    from sqlalchemy import select
    from storage.database import get_db
    from storage.models import JiraInstance

    async with get_db() as db:
        instance = (await db.execute(
            select(JiraInstance).where(JiraInstance.id == instance_id)
        )).scalar_one_or_none()
        if not instance:
            raise HTTPException(status_code=404, detail="Instance not found")

        if body.name is not None:
            instance.name = body.name
        if body.base_url is not None:
            instance.base_url = body.base_url
        if body.auth_type is not None:
            instance.auth_type = body.auth_type
        if body.username is not None:
            instance.username = body.username
        if body.api_token is not None:
            instance.api_token = body.api_token
        if body.pat is not None:
            instance.pat = body.pat
        if body.project_keys is not None:
            instance.project_keys = json.dumps(body.project_keys)
        if body.is_active is not None:
            instance.is_active = body.is_active
        if body.sync_enabled is not None:
            instance.sync_enabled = body.sync_enabled

        await db.commit()
        await db.refresh(instance)

    logger.info("jira_instance_updated", instance_id=instance_id, user=user.user_id)

    return {
        "id": instance.id,
        "name": instance.name,
        "base_url": instance.base_url,
        "auth_type": instance.auth_type,
        "project_keys": json.loads(instance.project_keys) if instance.project_keys else None,
        "is_active": instance.is_active,
        "sync_enabled": instance.sync_enabled,
    }


@router.delete("/admin/instances/{instance_id}")
async def delete_jira_instance(
    instance_id: int,
    user: Annotated[UserSession, Depends(require_admin)],
):
    from sqlalchemy import select
    from storage.database import get_db
    from storage.models import JiraInstance

    async with get_db() as db:
        instance = (await db.execute(
            select(JiraInstance).where(JiraInstance.id == instance_id)
        )).scalar_one_or_none()
        if not instance:
            raise HTTPException(status_code=404, detail="Instance not found")

        await db.delete(instance)
        await db.commit()

    logger.info("jira_instance_deleted", instance_id=instance_id, user=user.user_id)
    return {"message": "Instance deleted"}


@router.post("/admin/instances/{instance_id}/test")
async def test_jira_connection(
    instance_id: int,
    user: Annotated[UserSession, Depends(require_admin)],
):
    from jira_connector.client import JiraClient, JiraInstanceConfig

    async with get_db() as db:
        from storage.models import JiraInstance
        instance = (await db.execute(
            select(JiraInstance).where(JiraInstance.id == instance_id)
        )).scalar_one_or_none()
        if not instance:
            raise HTTPException(status_code=404, detail="Instance not found")

    config = JiraInstanceConfig(
        instance_id=instance.id,
        name=instance.name,
        base_url=instance.base_url,
        auth_type=instance.auth_type,
        username=instance.username,
        api_token=instance.api_token,
        pat=instance.pat,
    )

    try:
        async with JiraClient(instance=config) as client:
            projects = await client.list_projects()
        return {
            "success": True,
            "project_count": len(projects),
            "projects": [p.key for p in projects[:20]],
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Connection failed: {e}")


@router.get("/admin/webhooks/events")
async def list_webhook_events(user: Annotated[UserSession, Depends(require_admin)]):
    from api.webhooks import EVENT_TYPES
    return {"event_types": sorted(EVENT_TYPES)}


@router.post("/admin/webhooks/{webhook_id}/test")
async def test_webhook(
    webhook_id: int,
    user: Annotated[UserSession, Depends(require_admin)],
):
    from sqlalchemy import select
    from storage.database import get_db
    from storage.models import WebhookConfig
    from api.webhooks import dispatch_event

    async with get_db() as db:
        wh = (await db.execute(
            select(WebhookConfig).where(WebhookConfig.id == webhook_id)
        )).scalar_one_or_none()
        if not wh:
            raise HTTPException(status_code=404, detail="Webhook not found")

    results = await dispatch_event("webhook.test", {
        "event": "webhook.test",
        "webhook_id": wh.id,
        "webhook_name": wh.name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    return {"results": results}


# ---------------------------------------------------------------------------
# Webhook receiver (public, for external systems to push events)
# ---------------------------------------------------------------------------

@router.post("/webhooks/receive/{event_type}")
async def receive_webhook(
    event_type: str,
    payload: dict,
):
    from api.webhooks import EVENT_TYPES, dispatch_event

    if event_type not in EVENT_TYPES:
        raise HTTPException(status_code=400, detail=f"Unknown event type: {event_type}")

    project_key = payload.get("project_key")
    results = await dispatch_event(event_type, payload, project_key)

    return {
        "received": True,
        "event_type": event_type,
        "dispatched": len(results),
        "results": results,
    }
