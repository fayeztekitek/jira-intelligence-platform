"""
ingestion/snapshot_writer.py

Persists calculated KPIs and risk scores to:
  - KPIResult table (one row per project/kpi/period/date)
  - RiskScore table (one row per project/date)
  - FactSnapshot table (aggregate summary per project/date/period)

Also handles snapshot retention cleanup.
"""
from __future__ import annotations

from datetime import date, timedelta

import structlog
from sqlalchemy import select, delete

from storage.database import get_db
from storage.models import KPIResult, RiskScore, FactSnapshot, RiskLevel, TrendDirection
from kpi_engine.calculator import ProjectKPIs
from risk_engine.scorer import RiskScoreResult

logger = structlog.get_logger(__name__)


_RISK_LEVEL_MAP = {
    "low": RiskLevel.LOW,
    "medium": RiskLevel.MEDIUM,
    "high": RiskLevel.HIGH,
    "critical": RiskLevel.CRITICAL,
}

_TREND_MAP = {
    "improving": TrendDirection.IMPROVING,
    "stable": TrendDirection.STABLE,
    "degrading": TrendDirection.DEGRADING,
    "unknown": TrendDirection.UNKNOWN,
}


class SnapshotWriter:

    async def write_kpis(self, kpis: ProjectKPIs) -> int:
        """
        Upsert all KPI values for a project.
        Returns number of rows written.
        """
        written = 0
        async with get_db() as db:
            for kpi in kpis.kpis:
                # Check if row exists
                result = await db.execute(
                    select(KPIResult).where(
                        KPIResult.project_key == kpis.project_key,
                        KPIResult.kpi_name == kpi.name,
                        KPIResult.calculation_date == kpis.calculated_at,
                        KPIResult.period_label == kpi.period_label,
                    )
                )
                existing = result.scalar_one_or_none()

                row_data = {
                    "project_key": kpis.project_key,
                    "kpi_name": kpi.name,
                    "kpi_category": kpi.category,
                    "calculation_date": kpis.calculated_at,
                    "period_label": kpi.period_label,
                    "current_value": kpi.current_value,
                    "previous_value": kpi.previous_value,
                    "delta": kpi.delta,
                    "delta_pct": kpi.delta_pct,
                    "trend": _TREND_MAP.get(kpi.trend, TrendDirection.UNKNOWN),
                    "risk_level": _RISK_LEVEL_MAP.get(kpi.risk_level, RiskLevel.LOW),
                    "formula": kpi.formula,
                    "interpretation": kpi.interpretation,
                    "recommended_action": kpi.recommended_action,
                }

                if existing:
                    for k, v in row_data.items():
                        setattr(existing, k, v)
                else:
                    db.add(KPIResult(**row_data))
                    written += 1

        logger.info("kpis_written", project=kpis.project_key, count=written)
        return written

    async def write_risk_score(self, risk: RiskScoreResult) -> None:
        """Upsert risk score for a project/date/period."""
        import json
        async with get_db() as db:
            result = await db.execute(
                select(RiskScore).where(
                    RiskScore.project_key == risk.project_key,
                    RiskScore.calculation_date == risk.calculated_at,
                    RiskScore.period_label == risk.period_label,
                )
            )
            existing = result.scalar_one_or_none()

            row_data = {
                "project_key": risk.project_key,
                "calculation_date": risk.calculated_at,
                "period_label": risk.period_label,
                "delivery_risk": risk.delivery.trend_adjusted,
                "quality_risk": risk.quality.trend_adjusted,
                "compliance_risk": risk.compliance.trend_adjusted,
                "operational_risk": risk.operational.trend_adjusted,
                "composite_risk": risk.composite_score,
                "risk_level": _RISK_LEVEL_MAP.get(risk.risk_level, RiskLevel.LOW),
                "risk_drivers": json.dumps(risk.risk_drivers),
                "recommended_actions": json.dumps(risk.recommended_actions),
            }

            if existing:
                for k, v in row_data.items():
                    setattr(existing, k, v)
            else:
                db.add(RiskScore(**row_data))

        logger.info("risk_score_written", project=risk.project_key,
                    score=risk.composite_score, level=risk.risk_level)

    async def write_snapshot(
        self,
        project_key: str,
        snapshot_date: date,
        period_type: str,
        kpis: ProjectKPIs,
        risk: RiskScoreResult | None = None,
        sprint_velocity: float | None = None,
        sprint_predictability: float | None = None,
        spillover_rate: float | None = None,
    ) -> None:
        """Write aggregate FactSnapshot row with optional sprint KPIs."""

        def get_val(name: str, period: str = "1m") -> float | None:
            kv = kpis.by_name(name, period)
            return kv.current_value if kv else None

        p = period_type  # use period_type as period label for lookup

        async with get_db() as db:
            result = await db.execute(
                select(FactSnapshot).where(
                    FactSnapshot.project_key == project_key,
                    FactSnapshot.snapshot_date == snapshot_date,
                    FactSnapshot.period_type == period_type,
                )
            )
            existing = result.scalar_one_or_none()

            row_data = {
                "project_key": project_key,
                "snapshot_date": snapshot_date,
                "period_type": period_type,
                "total_open": get_val("backlog_size", p) or 0,
                "total_created": get_val("issues_created", p) or 0,
                "total_resolved": get_val("issues_resolved", p) or 0,
                "resolution_rate": get_val("resolution_rate", p) or 0.0,
                "avg_resolution_days": get_val("avg_resolution_days", p),
                "median_resolution_days": get_val("median_resolution_days", p),
                "avg_cycle_time_days": get_val("avg_cycle_time_days", p),
                "throughput": get_val("throughput", p) or 0,
                "backlog_size": get_val("backlog_size", p) or 0,
                "wip": get_val("wip", p) or 0,
                "overdue_count": get_val("overdue_count", p) or 0,
                "bugs_created": get_val("bugs_created", p) or 0,
                "bugs_resolved": get_val("bugs_resolved", p) or 0,
                "bug_resolution_rate": get_val("bug_resolution_rate", p) or 0.0,
                "reopened_count": get_val("reopened_count", p) or 0,
                "reopen_rate": get_val("reopen_rate", p) or 0.0,
                "critical_bugs_open": get_val("critical_bugs_open", p) or 0,
                "high_bugs_open": get_val("high_bugs_open", p) or 0,
                "dq_missing_assignee": get_val("missing_assignee", p) or 0,
                "dq_missing_priority": get_val("missing_priority", p) or 0,
                "dq_missing_component": get_val("missing_component", p) or 0,
                "dq_missing_fix_version": get_val("missing_fix_version", p) or 0,
                "dq_score": get_val("dq_score", p) or 100.0,
                "risk_score": risk.composite_score if risk else 0.0,
                "risk_level": _RISK_LEVEL_MAP.get(risk.risk_level, RiskLevel.LOW) if risk else RiskLevel.LOW,
                "sprint_velocity": sprint_velocity,
                "sprint_predictability": sprint_predictability,
                "spillover_rate": spillover_rate,
            }

            if existing:
                for k, v in row_data.items():
                    setattr(existing, k, v)
            else:
                db.add(FactSnapshot(**row_data))

    async def purge_old_snapshots(self, retention_days: int = 400) -> int:
        """Delete snapshots older than retention_days from all historical tables."""
        cutoff = date.today() - timedelta(days=retention_days)
        total = 0
        async with get_db() as db:
            for table in [KPIResult, FactSnapshot, RiskScore]:
                result = await db.execute(
                    delete(table).where(table.calculation_date < cutoff)  # type: ignore[attr-defined]
                )
                total += result.rowcount
        logger.info("snapshots_purged", cutoff=cutoff, deleted=total)
        return total
