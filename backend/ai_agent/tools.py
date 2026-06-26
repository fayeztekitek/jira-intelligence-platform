"""
ai_agent/tools.py — Tool registry for the AI agent.

8 tools that the agent can call to answer user questions about Jira data.
Each tool is a decorated async function with a Pydantic parameter schema.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta, datetime
from functools import wraps
from typing import Any, Callable

from pydantic import BaseModel, Field
from sqlalchemy import select, func, and_, Integer

from storage.database import get_db
from storage.models import (
    DimProject, FactIssue, KPIResult, RiskScore, ExtractionRun, FactSnapshot,
)
from kpi_engine.sprint import SprintAnalyzer
from kpi_engine.release import ReleaseAnalyzer

TOOL_TIMEOUT = 30

_tool_registry: dict[str, dict[str, Any]] = {}


def tool(name: str, description: str, params_model: type[BaseModel]):
    """Decorator that registers an async function as an agent tool."""
    def decorator(func: Callable) -> Callable:
        schema = params_model.model_json_schema()
        _tool_registry[name] = {
            "name": name,
            "description": description,
            "parameters": schema,
            "fn": func,
        }

        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await asyncio.wait_for(func(*args, **kwargs), timeout=TOOL_TIMEOUT)
            except asyncio.TimeoutError:
                return {"error": f"Tool '{name}' timed out after {TOOL_TIMEOUT}s"}
            except Exception as e:
                return {"error": f"Tool '{name}' failed: {str(e)}"}
        return wrapper
    return decorator


def list_tools() -> list[dict]:
    """Return metadata for all registered tools (without the handler)."""
    return [
        {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}
        for t in _tool_registry.values()
    ]


async def call_tool(name: str, **kwargs) -> Any:
    """Execute a tool by name with validated kwargs."""
    entry = _tool_registry.get(name)
    if not entry:
        return {"error": f"Unknown tool '{name}'"}
    return await entry["fn"](**kwargs)


# ---------------------------------------------------------------------------
# Parameter models
# ---------------------------------------------------------------------------

class ProjectKeyParam(BaseModel):
    project_key: str = Field(..., description="Jira project key (e.g. CORE)")

class ProjectKeyOptionalParam(BaseModel):
    project_key: str = Field("CORE", description="Jira project key (default: CORE)")

class SearchIssuesParam(BaseModel):
    project_key: str = Field(..., description="Jira project key")
    issue_type: str | None = Field(None, description="Filter by issue type: Bug, Story, Task, Epic")
    status: str | None = Field(None, description="Filter by status: To Do, In Progress, Done")
    priority: str | None = Field(None, description="Filter by priority: Critical, High, Medium, Low")
    limit: int = Field(20, description="Max results", ge=1, le=100)

class CompareProjectsParam(BaseModel):
    project_keys: str = Field(..., description="Comma-separated project keys (e.g. CORE,MOBILE)")

class TrendParam(BaseModel):
    project_key: str = Field(..., description="Jira project key")
    kpi_name: str = Field(..., description="KPI name (e.g. issues_created, resolution_rate)")
    days: int = Field(90, description="Lookback days", ge=1, le=365)


# ---------------------------------------------------------------------------
# Tool 1: get_project_kpis
# ---------------------------------------------------------------------------

@tool(
    name="get_project_kpis",
    description="Get delivery, quality, risk, and data-quality KPIs for a project.",
    params_model=ProjectKeyParam,
)
async def get_project_kpis(project_key: str) -> dict:
    async with get_db() as db:
        rows = (await db.execute(
            select(KPIResult).where(
                KPIResult.project_key == project_key,
                KPIResult.period_label == "1m",
            ).order_by(KPIResult.calculation_date.desc())
        )).scalars().all()
    latest_date = None
    kpis = []
    seen = set()
    for r in rows:
        if r.kpi_name not in seen:
            kpis.append(r.to_dict() if hasattr(r, "to_dict") else {
                "name": r.kpi_name, "category": r.kpi_category,
                "current_value": r.current_value, "previous_value": r.previous_value,
                "trend": r.trend.value if r.trend else None,
                "risk_level": r.risk_level.value if r.risk_level else None,
            })
            seen.add(r.kpi_name)
        if latest_date is None:
            latest_date = r.calculation_date.isoformat()
    return {"project_key": project_key, "kpis": kpis, "as_of": latest_date}


# ---------------------------------------------------------------------------
# Tool 2: get_risk_scores
# ---------------------------------------------------------------------------

@tool(
    name="get_risk_scores",
    description="Get composite and dimension risk scores for a project.",
    params_model=ProjectKeyParam,
)
async def get_risk_scores(project_key: str) -> dict:
    async with get_db() as db:
        row = (await db.execute(
            select(RiskScore).where(
                RiskScore.project_key == project_key,
                RiskScore.period_label == "1m",
            ).order_by(RiskScore.calculation_date.desc())
        )).scalars().first()
    if not row:
        return {"project_key": project_key, "error": "No risk data found"}
    return {
        "project_key": project_key,
        "composite_risk": row.composite_risk,
        "risk_level": row.risk_level.value if row.risk_level else "unknown",
        "delivery_risk": row.delivery_risk,
        "quality_risk": row.quality_risk,
        "compliance_risk": row.compliance_risk,
        "operational_risk": row.operational_risk,
        "risk_drivers": json.loads(row.risk_drivers) if row.risk_drivers else [],
        "recommended_actions": json.loads(row.recommended_actions) if row.recommended_actions else [],
        "as_of": row.calculation_date.isoformat(),
    }


# ---------------------------------------------------------------------------
# Tool 3: search_issues
# ---------------------------------------------------------------------------

@tool(
    name="search_issues",
    description="Search issues in a project with optional filters.",
    params_model=SearchIssuesParam,
)
async def search_issues(project_key: str, issue_type: str | None = None,
                        status: str | None = None, priority: str | None = None,
                        limit: int = 20) -> dict:
    async with get_db() as db:
        q = select(FactIssue).where(FactIssue.project_key == project_key)
        if issue_type:
            q = q.where(FactIssue.issue_type == issue_type)
        if status:
            q = q.where(FactIssue.status == status)
        if priority:
            q = q.where(FactIssue.priority == priority)
        q = q.order_by(FactIssue.created_date.desc()).limit(limit)
        rows = (await db.execute(q)).scalars().all()
    return {
        "project_key": project_key,
        "total": len(rows),
        "issues": [
            {
                "key": r.jira_key,
                "summary": r.summary,
                "type": r.issue_type,
                "status": r.status,
                "priority": r.priority,
                "assignee": r.assignee_id or "Unassigned",
                "created": r.created_date.isoformat() if r.created_date else None,
                "age_days": r.age_days,
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# Tool 4: get_exec_summary
# ---------------------------------------------------------------------------

@tool(
    name="get_exec_summary",
    description="Executive summary with headline KPIs across all projects.",
    params_model=ProjectKeyOptionalParam,
)
async def get_exec_summary(project_key: str = "CORE") -> dict:
    async with get_db() as db:
        projects = (await db.execute(
            select(DimProject).where(DimProject.is_active == True)
        )).scalars().all()

        risk_rows = (await db.execute(
            select(RiskScore).where(
                RiskScore.period_label == "1m",
            ).order_by(RiskScore.calculation_date.desc())
        )).scalars().all()

        issue_counts = (await db.execute(
            select(
                FactIssue.project_key,
                func.count().label("total"),
                func.sum(FactIssue.is_overdue.cast(Integer)).label("overdue"),
            ).group_by(FactIssue.project_key)
        )).all()

    latest_risks = {}
    for r in risk_rows:
        if r.project_key not in latest_risks:
            latest_risks[r.project_key] = r

    project_summaries = []
    total_open = 0
    total_overdue = 0
    for proj in projects:
        risk = latest_risks.get(proj.id)
        counts = next((ic for ic in issue_counts if ic[0] == proj.id), None)
        open_count = counts.total if counts else 0
        overdue_count = counts.overdue or 0 if counts else 0
        total_open += open_count
        total_overdue += overdue_count
        project_summaries.append({
            "key": proj.id,
            "name": proj.name,
            "risk_level": risk.risk_level.value if risk and risk.risk_level else "unknown",
            "composite_risk": risk.composite_risk if risk else None,
            "open_issues": open_count,
            "overdue": overdue_count,
        })

    return {
        "generated_at": date.today().isoformat(),
        "total_projects": len(projects),
        "overall": {
            "total_open_issues": total_open,
            "total_overdue": total_overdue,
        },
        "projects": project_summaries,
    }


# ---------------------------------------------------------------------------
# Tool 5: compare_projects
# ---------------------------------------------------------------------------

@tool(
    name="compare_projects",
    description="Side-by-side KPI comparison for multiple projects.",
    params_model=CompareProjectsParam,
)
async def compare_projects(project_keys: str) -> dict:
    keys = [k.strip() for k in project_keys.split(",") if k.strip()]
    async with get_db() as db:
        kpi_rows = (await db.execute(
            select(KPIResult).where(
                KPIResult.project_key.in_(keys),
                KPIResult.period_label == "1m",
            ).order_by(KPIResult.calculation_date.desc())
        )).scalars().all()

        risk_rows = (await db.execute(
            select(RiskScore).where(
                RiskScore.project_key.in_(keys),
                RiskScore.period_label == "1m",
            ).order_by(RiskScore.calculation_date.desc())
        )).scalars().all()

    latest_kpis: dict[str, dict] = {}
    for r in kpi_rows:
        if r.project_key not in latest_kpis:
            latest_kpis[r.project_key] = {}
        if r.kpi_name not in latest_kpis[r.project_key]:
            latest_kpis[r.project_key][r.kpi_name] = r.current_value

    latest_risks: dict[str, Any] = {}
    for r in risk_rows:
        if r.project_key not in latest_risks:
            latest_risks[r.project_key] = {
                "composite_risk": r.composite_risk,
                "risk_level": r.risk_level.value if r.risk_level else "unknown",
            }

    return {
        "projects": keys,
        "kpis": latest_kpis,
        "risks": latest_risks,
    }


# ---------------------------------------------------------------------------
# Tool 6: get_sprint_analysis
# ---------------------------------------------------------------------------

@tool(
    name="get_sprint_analysis",
    description="Sprint velocity, burndown, and scope change analysis.",
    params_model=ProjectKeyParam,
)
async def get_sprint_analysis(project_key: str) -> dict:
    analyzer = SprintAnalyzer(project_key)
    async with get_db() as db:
        sprints = await analyzer.analyze(db)
    if not sprints:
        return {"project_key": project_key, "sprints": [], "total": 0}
    return {
        "project_key": project_key,
        "total": len(sprints),
        "sprints": [
            {
                "id": s.sprint_id,
                "name": s.name,
                "state": s.state,
                "total_committed": s.total_committed,
                "total_completed": s.total_completed,
                "carry_over": s.carry_over,
                "scope_added": s.scope_added,
                "scope_removed": s.scope_removed,
                "velocity": s.velocity,
                "predictability": s.predictability,
            }
            for s in sprints
        ],
    }


# ---------------------------------------------------------------------------
# Tool 7: get_trend
# ---------------------------------------------------------------------------

@tool(
    name="get_trend",
    description="Time-series trend data for a specific KPI.",
    params_model=TrendParam,
)
async def get_trend(project_key: str, kpi_name: str, days: int = 90) -> dict:
    since = date.today() - timedelta(days=days)
    async with get_db() as db:
        rows = (await db.execute(
            select(KPIResult).where(
                KPIResult.project_key == project_key,
                KPIResult.kpi_name == kpi_name,
                KPIResult.calculation_date >= since,
                KPIResult.period_label == "1m",
            ).order_by(KPIResult.calculation_date.asc())
        )).scalars().all()
    return {
        "project_key": project_key,
        "kpi_name": kpi_name,
        "data_points": [
            {
                "date": r.calculation_date.isoformat(),
                "current_value": r.current_value,
                "previous_value": r.previous_value,
                "trend": r.trend.value if r.trend else None,
            }
            for r in rows
        ],
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
    async with get_db() as db:
        risk = (await db.execute(
            select(RiskScore).where(
                RiskScore.project_key == project_key,
                RiskScore.period_label == "1m",
            ).order_by(RiskScore.calculation_date.desc())
        )).scalars().first()
    recommendations = []
    if risk and risk.recommended_actions:
        recommendations = json.loads(risk.recommended_actions)
    return {
        "project_key": project_key,
        "recommendations": recommendations,
        "risk_level": risk.risk_level.value if risk and risk.risk_level else "unknown",
    }
