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
from datetime import date, timedelta
from typing import Any

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, desc, and_
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import require_auth, UserSession
from jira_connector.client import JiraClient
from jira_connector.fields import FieldDiscoverer
from storage.database import get_db
from storage.models import (
    DimProject, FactIssue, KPIResult, RiskScore, ExtractionRun, FactSnapshot
)

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
            async with get_db() as db:
                q = select(FactIssue).where(FactIssue.project_key == project_key)
                if issue_type:
                    q = q.where(FactIssue.issue_type == issue_type)
                if status:
                    q = q.where(FactIssue.status == status)
                q = q.order_by(FactIssue.jira_key).limit(batch).offset(offset)
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
