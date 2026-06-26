"""
ai_agent/tools.py — Tool registry for the AI agent.

Each tool is registered with the @tool decorator and provides:
- A name and description for intent matching
- A Pydantic params_model for parameter validation
- An async handler function

Tools connect to the database directly (not via API routes).
"""

from __future__ import annotations

import json
import logging
from datetime import date
from functools import wraps
from typing import Any, Callable

from pydantic import BaseModel, Field
from sqlalchemy import select, func, and_

from storage.database import get_db
from storage.models import (
    KPIResult, RiskScore, FactIssue, DimProject,
    ExtractionRun, FactSnapshot, DimVersion,
)
from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Tool metadata store
# ---------------------------------------------------------------------------

_TOOL_REGISTRY: dict[str, dict] = {}


def tool(name: str, description: str, params_model: type[BaseModel]):
    """Decorator that registers a callable tool in the global registry."""
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(**kwargs):
            validated = params_model(**kwargs)
            try:
                return await func(**validated.model_dump())
            except Exception as e:
                logger.error("tool_error", tool=name, error=str(e))
                return {"error": str(e)}

        _TOOL_REGISTRY[name] = {
            "name": name,
            "description": description,
            "params_model": params_model,
            "handler": wrapper,
        }
        return wrapper

    return decorator


def list_tools() -> list[dict]:
    return [
        {"name": v["name"], "description": v["description"]}
        for v in _TOOL_REGISTRY.values()
    ]


async def call_tool(name: str, **kwargs) -> Any:
    meta = _TOOL_REGISTRY.get(name)
    if not meta:
        return {"error": f"Unknown tool: {name}"}
    return await meta["handler"](**kwargs)


# ---------------------------------------------------------------------------
# Shared parameter models
# ---------------------------------------------------------------------------

class ProjectKeyParam(BaseModel):
    project_key: str = Field(..., description="Jira project key (e.g., CORE, MOBILE)")


class ProjectKeysParam(BaseModel):
    project_keys: str = Field(..., description="Comma-separated project keys")


class SearchIssuesParam(BaseModel):
    project_key: str = Field(..., description="Jira project key")
    issue_type: str | None = Field(None, description="Type filter: bug, story, task, epic")
    status: str | None = Field(None, description="Status filter: To Do, In Progress, Done, etc.")
    limit: int = Field(20, description="Max results", ge=1, le=200)


class TrendParam(BaseModel):
    project_key: str = Field(..., description="Jira project key")
    days: int = Field(90, description="Lookback period in days", ge=1, le=365)
    kpi_name: str | None = Field(None, description="Optional KPI name filter")


# ---------------------------------------------------------------------------
# Recommendation engine
# ---------------------------------------------------------------------------

RECOMMENDATION_RULES: list[dict] = [
    {"name": "blocked_issues", "label": "Blocked Issues Needing Triage",
     "description": "Multiple issues are blocked and need attention",
     "impact_area": "delivery_risk", "priority": "high",
     "kpi_check": "blocked_count", "threshold": 5, "direction": "above"},
    {"name": "predictability", "label": "Sprint Planning Accuracy",
     "description": "Sprint predictability is below target",
     "impact_area": "process_optimization", "priority": "medium",
     "kpi_check": "predictability", "threshold": 0.7, "direction": "below"},
    {"name": "bug_rate", "label": "High Bug Injection Rate",
     "description": "Bug rate exceeds healthy threshold",
     "impact_area": "quality_improvement", "priority": "high",
     "kpi_check": "bug_rate", "threshold": 0.3, "direction": "above"},
    {"name": "overdue_ratio", "label": "Overdue Issue Backlog",
     "description": "A significant portion of issues are overdue",
     "impact_area": "delivery_risk", "priority": "high",
     "kpi_check": "overdue_ratio", "threshold": 0.25, "direction": "above"},
    {"name": "aging_critical", "label": "Aging Critical Issues",
     "description": "Critical issues have been unresolved for too long",
     "impact_area": "delivery_risk", "priority": "high",
     "kpi_check": "aging_critical", "threshold": 7, "direction": "above"},
    {"name": "scope_change", "label": "Frequent Scope Changes",
     "description": "Sprint scope changes are affecting delivery predictability",
     "impact_area": "process_optimization", "priority": "medium",
     "kpi_check": "scope_change_rate", "threshold": 0.3, "direction": "above"},
    {"name": "velocity_drop", "label": "Velocity Decline",
     "description": "Team velocity has dropped compared to previous period",
     "impact_area": "capacity", "priority": "medium",
     "kpi_check": "velocity_change", "threshold": -0.1, "direction": "below"},
    {"name": "composite_risk", "label": "Elevated Composite Risk",
     "description": "Overall risk score is above acceptable threshold",
     "impact_area": "delivery_risk", "priority": "high",
     "kpi_check": "composite_risk", "threshold": 0.6, "direction": "above"},
]


async def _scan_kpis(project_key: str) -> list[dict]:
    """Scan KPI results for anomalies based on recommendation rules."""
    recommendations = []
    async with get_db() as db:
        stmt = select(KPIResult).where(
            KPIResult.project_key == project_key,
            KPIResult.period_label == "1m",
        ).order_by(KPIResult.calculation_date.desc()).limit(50)
        kpis = (await db.execute(stmt)).scalars().all()

    kpi_map = {k.kpi_name: k for k in kpis}

    for rule in RECOMMENDATION_RULES:
        kpi = kpi_map.get(rule["kpi_check"])
        if not kpi or kpi.current_value is None:
            continue
        val = kpi.current_value
        threshold = rule["threshold"]
        triggered = (
            (val > threshold if rule["direction"] == "above" else val < threshold)
        )
        if triggered:
            recommendations.append({
                "title": rule["label"],
                "description": f"{rule['description']}: current {rule['kpi_check']}={val:.2f}, threshold={threshold}",
                "priority": rule["priority"],
                "impact_area": rule["impact_area"],
                "metric": rule["kpi_check"],
                "current_value": val,
                "threshold": threshold,
            })
    return recommendations


async def _scan_risk(project_key: str) -> list[dict]:
    """Scan risk data for additional recommendations."""
    recommendations = []
    async with get_db() as db:
        risk = (await db.execute(
            select(RiskScore).where(
                RiskScore.project_key == project_key,
                RiskScore.period_label == "1m",
            ).order_by(RiskScore.calculation_date.desc())
        )).scalars().first()

    if risk:
        if risk.delivery_risk and risk.delivery_risk >= 0.7:
            recommendations.append({
                "title": "High Delivery Risk",
                "description": f"Delivery risk score is {risk.delivery_risk:.2f}. Review blockers and overdue items.",
                "priority": "high",
                "impact_area": "delivery_risk",
                "metric": "delivery_risk",
                "current_value": risk.delivery_risk,
                "threshold": 0.7,
            })
        if risk.quality_risk and risk.quality_risk >= 0.6:
            recommendations.append({
                "title": "Quality Risk Requires Attention",
                "description": f"Quality risk score is {risk.quality_risk:.2f}. Consider code review and testing improvements.",
                "priority": "medium",
                "impact_area": "quality_improvement",
                "metric": "quality_risk",
                "current_value": risk.quality_risk,
                "threshold": 0.6,
            })
        if risk.recommended_actions:
            actions = json.loads(risk.recommended_actions) if isinstance(risk.recommended_actions, str) else risk.recommended_actions
            for action in actions[:3]:
                if isinstance(action, str):
                    recommendations.append({
                        "title": action[:80],
                        "description": action,
                        "priority": "medium",
                        "impact_area": "delivery_risk",
                    })
    return recommendations


async def _scan_issues(project_key: str) -> list[dict]:
    """Scan recent issues for blockers and aging items."""
    recommendations = []
    async with get_db() as db:
        blocked = (await db.execute(
            select(func.count(FactIssue.id)).where(
                FactIssue.project_key == project_key,
                FactIssue.status == "Blocked",
            )
        )).scalar() or 0
        overdue = (await db.execute(
            select(func.count(FactIssue.id)).where(
                FactIssue.project_key == project_key,
                FactIssue.due_date < date.today(),
                FactIssue.status.notin_(["Done", "Cancelled"]),
            )
        )).scalar() or 0
        critical_open = (await db.execute(
            select(func.count(FactIssue.id)).where(
                FactIssue.project_key == project_key,
                FactIssue.priority.in_(["Highest", "Critical"]),
                FactIssue.status.notin_(["Done", "Cancelled"]),
            )
        )).scalar() or 0

    if blocked >= 5:
        recommendations.append({
            "title": f"{blocked} Blocked Issues Need Triage",
            "description": f"There are {blocked} blocked issues in {project_key} requiring immediate attention.",
            "priority": "high",
            "impact_area": "delivery_risk",
            "metric": "blocked_count",
            "current_value": blocked,
            "threshold": 5,
        })
    if overdue >= 10:
        recommendations.append({
            "title": f"{overdue} Overdue Issues",
            "description": f"{overdue} issues are past their due date in {project_key}.",
            "priority": "high",
            "impact_area": "delivery_risk",
            "metric": "overdue_count",
            "current_value": overdue,
            "threshold": 10,
        })
    if critical_open >= 5:
        recommendations.append({
            "title": f"{critical_open} Critical Issues Unresolved",
            "description": f"{critical_open} critical/highest priority issues are still open in {project_key}.",
            "priority": "high",
            "impact_area": "quality_improvement",
            "metric": "critical_open",
            "current_value": critical_open,
            "threshold": 5,
        })
    return recommendations


async def generate_recommendations(project_key: str) -> dict:
    """Generate 3-5 actionable recommendations for a project."""
    kpi_recs = await _scan_kpis(project_key)
    risk_recs = await _scan_risk(project_key)
    issue_recs = await _scan_issues(project_key)

    all_recs = kpi_recs + risk_recs + issue_recs

    priority_order = {"high": 0, "medium": 1, "low": 2}
    all_recs.sort(key=lambda r: priority_order.get(r.get("priority", "low"), 3))

    seen = set()
    unique = []
    for r in all_recs:
        key = r.get("title", "")
        if key not in seen:
            seen.add(key)
            unique.append(r)

    return {
        "project_key": project_key,
        "recommendations": unique[:8],
        "total": len(unique),
    }


# ---------------------------------------------------------------------------
# Tool 1: get_project_kpis
# ---------------------------------------------------------------------------

@tool(
    name="get_project_kpis",
    description="Retrieve all KPIs for a project (delivery, quality, risk, DQ, team metrics).",
    params_model=ProjectKeyParam,
)
async def get_project_kpis(project_key: str) -> dict:
    async with get_db() as db:
        stmt = select(KPIResult).where(
            KPIResult.project_key == project_key,
        ).order_by(KPIResult.calculation_date.desc()).limit(30)
        rows = (await db.execute(stmt)).scalars().all()
    kpis = [
        {
            "name": r.kpi_name,
            "current_value": r.current_value,
            "previous_value": r.previous_value,
            "trend": r.trend,
            "period_label": r.period_label,
            "calculation_date": str(r.calculation_date),
        }
        for r in rows
    ]
    return {"project_key": project_key, "kpis": kpis, "total": len(kpis)}


# ---------------------------------------------------------------------------
# Tool 2: get_risk_scores
# ---------------------------------------------------------------------------

@tool(
    name="get_risk_scores",
    description="Get risk scores and risk dimensions for a project.",
    params_model=ProjectKeyParam,
)
async def get_risk_scores(project_key: str) -> dict:
    async with get_db() as db:
        risk = (await db.execute(
            select(RiskScore).where(
                RiskScore.project_key == project_key,
                RiskScore.period_label == "1m",
            ).order_by(RiskScore.calculation_date.desc())
        )).scalars().first()
    if not risk:
        return {"project_key": project_key, "error": "No risk data"}
    drivers = json.loads(risk.risk_drivers) if risk.risk_drivers else []
    actions = json.loads(risk.recommended_actions) if risk.recommended_actions else []
    return {
        "project_key": project_key,
        "composite_risk": risk.composite_risk,
        "risk_level": risk.risk_level.value if risk.risk_level else "unknown",
        "delivery_risk": risk.delivery_risk,
        "quality_risk": risk.quality_risk,
        "compliance_risk": risk.compliance_risk,
        "operational_risk": risk.operational_risk,
        "risk_drivers": drivers,
        "recommended_actions": actions,
        "period_label": risk.period_label,
        "calculation_date": str(risk.calculation_date),
    }


# ---------------------------------------------------------------------------
# Tool 3: search_issues
# ---------------------------------------------------------------------------


@tool(
    name="search_issues",
    description="Search issues in a project with optional type and status filters.",
    params_model=SearchIssuesParam,
)
async def search_issues(
    project_key: str,
    issue_type: str | None = None,
    status: str | None = None,
    limit: int = 20,
) -> dict:
    async with get_db() as db:
        stmt = select(FactIssue).where(FactIssue.project_key == project_key)
        if issue_type:
            stmt = stmt.where(FactIssue.issue_type == issue_type.lower())
        if status:
            stmt = stmt.where(FactIssue.status == status)
        stmt = stmt.order_by(FactIssue.updated_at.desc()).limit(limit)
        rows = (await db.execute(stmt)).scalars().all()
    issues = [
        {
            "key": r.issue_key,
            "summary": r.summary[:120],
            "status": r.status,
            "priority": r.priority or "N/A",
            "issue_type": r.issue_type,
            "assignee": r.assignee or "Unassigned",
            "created": str(r.created_at),
        }
        for r in rows
    ]
    return {"project_key": project_key, "issues": issues, "total": len(issues)}


# ---------------------------------------------------------------------------
# Tool 4: get_exec_summary
# ---------------------------------------------------------------------------

EXECUTIVE_METRICS = [
    "open_issues", "resolved_last_30d", "overdue_ratio",
    "avg_lead_time", "bug_rate", "blocked_count",
]


@tool(
    name="get_exec_summary",
    description="Get a high-level executive summary for a project.",
    params_model=ProjectKeyParam,
)
async def get_exec_summary(project_key: str) -> dict:
    async with get_db() as db:
        kpi_stmt = select(KPIResult).where(
            KPIResult.project_key == project_key,
            KPIResult.period_label == "1m",
        )
        kpis = (await db.execute(kpi_stmt)).scalars().all()
        risk = (await db.execute(
            select(RiskScore).where(
                RiskScore.project_key == project_key,
                RiskScore.period_label == "1m",
            ).order_by(RiskScore.calculation_date.desc())
        )).scalars().first()
        total = (await db.execute(
            select(func.count(FactIssue.id)).where(FactIssue.project_key == project_key)
        )).scalar() or 0

    kpi_map = {k.kpi_name: k.current_value for k in kpis}
    metrics = {m: kpi_map.get(m) for m in EXECUTIVE_METRICS}

    return {
        "project_key": project_key,
        "total_issues": total,
        "metrics": metrics,
        "risk_level": risk.risk_level.value if risk and risk.risk_level else "unknown",
        "composite_risk": risk.composite_risk if risk else None,
        "generated_at": str(date.today()),
    }


# ---------------------------------------------------------------------------
# Tool 5: compare_projects
# ---------------------------------------------------------------------------


@tool(
    name="compare_projects",
    description="Compare KPIs and risk scores of two or more projects side-by-side.",
    params_model=ProjectKeysParam,
)
async def compare_projects(project_keys: str) -> dict:
    keys = [k.strip().upper() for k in project_keys.split(",") if k.strip()]
    if not keys:
        return {"error": "At least one project key required"}

    kpis_result = {}
    risks_result = {}

    async with get_db() as db:
        for pk in keys:
            krows = (await db.execute(
                select(KPIResult).where(
                    KPIResult.project_key == pk,
                    KPIResult.period_label == "1m",
                ).order_by(KPIResult.calculation_date.desc()).limit(15)
            )).scalars().all()
            kpis_result[pk] = {r.kpi_name: r.current_value for r in krows}

            rrow = (await db.execute(
                select(RiskScore).where(
                    RiskScore.project_key == pk,
                    RiskScore.period_label == "1m",
                ).order_by(RiskScore.calculation_date.desc())
            )).scalars().first()
            if rrow:
                risks_result[pk] = {
                    "composite_risk": rrow.composite_risk,
                    "risk_level": rrow.risk_level.value if rrow.risk_level else "unknown",
                    "delivery_risk": rrow.delivery_risk,
                    "quality_risk": rrow.quality_risk,
                }
            else:
                risks_result[pk] = {}

    return {"projects": keys, "kpis": kpis_result, "risks": risks_result}


# ---------------------------------------------------------------------------
# Tool 6: get_sprint_analysis
# ---------------------------------------------------------------------------


@tool(
    name="get_sprint_analysis",
    description="Analyze sprints for a project: velocity, burndown, scope change.",
    params_model=ProjectKeyParam,
)
async def get_sprint_analysis(project_key: str) -> dict:
    async with get_db() as db:
        from storage.models import DimSprint, FactIssue

        stmt = select(
            DimSprint.sprint_id,
            DimSprint.name,
            DimSprint.state,
            DimSprint.start_date,
            DimSprint.end_date,
            DimSprint.total_committed,
            DimSprint.total_completed,
            DimSprint.total_added,
            DimSprint.total_removed,
        ).where(
            DimSprint.project_key == project_key,
        ).order_by(DimSprint.start_date.desc()).limit(10)
        rows = (await db.execute(stmt)).all()

    sprints = []
    for r in rows:
        vel = (r.total_completed or 0) / max(r.total_committed or 1, 1)
        pred = min(vel * 100, 100.0)
        sprints.append({
            "sprint_id": r.sprint_id,
            "name": r.name,
            "state": r.state,
            "start_date": str(r.start_date) if r.start_date else None,
            "end_date": str(r.end_date) if r.end_date else None,
            "total_committed": r.total_committed or 0,
            "total_completed": r.total_completed or 0,
            "total_added": r.total_added or 0,
            "total_removed": r.total_removed or 0,
            "velocity": round(vel, 2),
            "predictability": round(pred, 1),
        })

    return {"project_key": project_key, "sprints": sprints, "total": len(sprints)}


# ---------------------------------------------------------------------------
# Tool 7: get_trend
# ---------------------------------------------------------------------------


@tool(
    name="get_trend",
    description="Get historical trend data for KPIs of a project.",
    params_model=TrendParam,
)
async def get_trend(project_key: str, days: int = 90, kpi_name: str | None = None) -> dict:
    async with get_db() as db:
        stmt = select(KPIResult).where(
            KPIResult.project_key == project_key,
        )
        if kpi_name:
            stmt = stmt.where(KPIResult.kpi_name == kpi_name)
        stmt = stmt.order_by(KPIResult.calculation_date.desc()).limit(365)
        rows = (await db.execute(stmt)).scalars().all()

    data_points = [
        {
            "date": str(r.calculation_date),
            "kpi_name": r.kpi_name,
            "current_value": r.current_value,
            "trend": r.trend,
            "period_label": r.period_label,
        }
        for r in rows
    ]

    name = kpi_name or "multiple"
    return {
        "project_key": project_key,
        "kpi_name": name,
        "days": days,
        "data_points": data_points,
        "total": len(data_points),
    }


# ---------------------------------------------------------------------------
# Tool 8: get_recommendations
# ---------------------------------------------------------------------------


@tool(
    name="get_recommendations",
    description="Auto-generated action items based on current risk and KPI data.",
    params_model=ProjectKeyParam,
)
async def get_recommendations(project_key: str) -> dict:
    return await generate_recommendations(project_key)
