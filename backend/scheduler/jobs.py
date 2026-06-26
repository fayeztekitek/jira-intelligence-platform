"""
scheduler/jobs.py

APScheduler job definitions.
Jobs:
  1. nightly_incremental  — runs daily, extracts last 25h of changes
  2. weekly_full_sync     — runs Sunday 01:00, full re-sync
  3. kpi_calculation      — runs daily after extraction, computes all KPIs
  4. snapshot_maintenance — weekly, purges old snapshots

Each job logs start/end to ExtractionRun for full audit trail.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import TYPE_CHECKING

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()


async def job_incremental_extraction() -> None:
    """Incremental extraction: issues updated in last 25 hours."""
    from jira_connector.client import JiraClient
    from ingestion.extractor import JiraExtractor

    logger.info("job_start", job="incremental_extraction")
    async with JiraClient() as client:
        extractor = JiraExtractor(client)
        run_id = await extractor.run_incremental_extraction(
            since_hours=25, triggered_by="scheduler"
        )
    logger.info("job_done", job="incremental_extraction", run_id=run_id)


async def job_full_sync() -> None:
    """Full sync: all projects, all issues (weekly)."""
    from jira_connector.client import JiraClient
    from ingestion.extractor import JiraExtractor

    logger.info("job_start", job="full_sync")
    async with JiraClient() as client:
        extractor = JiraExtractor(client)
        run_id = await extractor.run_full_extraction(triggered_by="scheduler_weekly")
    logger.info("job_done", job="full_sync", run_id=run_id)


async def job_calculate_kpis() -> None:
    """
    Load all issues from DB, calculate KPIs and risk scores,
    write results to KPIResult + RiskScore + FactSnapshot.
    """
    from sqlalchemy import select
    from storage.database import get_db
    from storage.models import DimProject, FactIssue
    from kpi_engine.calculator import KPICalculator, IssueRecord
    from risk_engine.scorer import RiskScorer
    from ingestion.snapshot_writer import SnapshotWriter

    logger.info("job_start", job="kpi_calculation")
    writer = SnapshotWriter()
    today = date.today()

    async with get_db() as db:
        projects = (await db.execute(
            select(DimProject).where(DimProject.is_active == True)
        )).scalars().all()

    for project in projects:
        try:
            async with get_db() as db:
                rows = (await db.execute(
                    select(FactIssue).where(FactIssue.project_key == project.id)
                )).scalars().all()

            issues = [_row_to_record(r) for r in rows]

            if not issues:
                logger.info("no_issues", project=project.id)
                continue

            # Calculate KPIs
            calc = KPICalculator(project.id, issues, as_of=today)
            kpis = calc.calculate_all()

            # Calculate risk for multiple periods
            risk_periods = ["1w", "1m", "3m"]
            risks = {}
            for rp in risk_periods:
                scorer = RiskScorer(kpis, reference_period=rp)
                risks[rp] = scorer.score()
            risk = risks["1m"]  # default for legacy consumers

            # Compute sprint KPIs
            sprint_velocity = None
            sprint_predictability = None
            spillover_rate = None
            try:
                from kpi_engine.sprint import SprintAnalyzer
                sprint_analyzer = SprintAnalyzer(project.id, as_of=today)
                async with get_db() as sprint_db:
                    sprint_results = await sprint_analyzer.analyze(sprint_db)
                if sprint_results:
                    completed = sum(s.total_completed for s in sprint_results)
                    committed = sum(s.total_committed for s in sprint_results)
                    carry = sum(s.carry_over for s in sprint_results)
                    sprint_velocity = round(completed, 1)
                    spillover_rate = round(carry / committed, 4) if committed > 0 else 0.0
                    # Use weighted average predictability
                    weighted_pct = sum(s.predictability * s.total_committed
                                       for s in sprint_results if s.predictability and s.total_committed)
                    total_p = sum(s.total_committed for s in sprint_results if s.predictability)
                    sprint_predictability = round(weighted_pct / total_p, 4) if total_p > 0 else None
            except Exception as e:
                logger.warning("sprint_kpis_failed", project=project.id, error=str(e))

            # Persist
            await writer.write_kpis(kpis)
            for rp in risk_periods:
                await writer.write_risk_score(risks[rp])

            for period_type in ["daily", "weekly", "monthly"]:
                await writer.write_snapshot(
                    project.id, today, period_type, kpis, risk,
                    sprint_velocity=sprint_velocity,
                    sprint_predictability=sprint_predictability,
                    spillover_rate=spillover_rate,
                )

            logger.info("project_kpis_done", project=project.id,
                        kpi_count=len(kpis.kpis), risk=risk.risk_level)

        except Exception as e:
            logger.error("kpi_job_failed", project=project.id, error=str(e))

    logger.info("job_done", job="kpi_calculation")


async def job_backfill_snapshots(days: int = 90) -> int:
    """
    Backfill historical snapshots for all projects, day by day.

    Runs KPI calculation with as_of = each past day and writes snapshots.
    Returns the number of snapshot rows written.
    """
    from sqlalchemy import select
    from storage.database import get_db
    from storage.models import DimProject, FactIssue
    from kpi_engine.calculator import KPICalculator
    from kpi_engine.calculator import IssueRecord
    from risk_engine.scorer import RiskScorer
    from ingestion.snapshot_writer import SnapshotWriter

    logger.info("job_start", job="backfill_snapshots", days=days)
    writer = SnapshotWriter()
    today = date.today()
    total_written = 0

    async with get_db() as db:
        projects = (await db.execute(
            select(DimProject)
        )).scalars().all()

    all_issues: dict[str, list] = {}
    for project in projects:
        async with get_db() as db:
            rows = (await db.execute(
                select(FactIssue).where(FactIssue.project_key == project.id)
            )).scalars().all()
        all_issues[project.id] = [_row_to_record(r) for r in rows]

    for day_offset in range(days):
        as_of = today - timedelta(days=day_offset)
        for project in projects:
            issues = all_issues.get(project.id, [])
            if not issues:
                continue
            try:
                calc = KPICalculator(project.id, issues, as_of=as_of)
                kpis = calc.calculate_all()
                risk_periods = ["1w", "1m", "3m"]
                risks = {}
                for rp in risk_periods:
                    scorer = RiskScorer(kpis, reference_period=rp)
                    risks[rp] = scorer.score()
                risk = risks["1m"]
                await writer.write_kpis(kpis)
                for rp in risk_periods:
                    await writer.write_risk_score(risks[rp])
                for pt in ["daily", "weekly", "monthly"]:
                    await writer.write_snapshot(project.id, as_of, pt, kpis, risk)
                    total_written += 1
            except Exception as e:
                logger.error("backfill_failed", project=project.id, date=as_of, error=str(e))

    logger.info("job_done", job="backfill_snapshots", days=days, written=total_written)
    return total_written


async def job_embedding_pipeline() -> None:
    """Incremental embedding for un-embedded records."""
    from ai_agent.rag_index import EmbeddingPipeline
    logger.info("job_start", job="embedding_pipeline")
    counts = await EmbeddingPipeline.run_incremental()
    logger.info("job_done", job="embedding_pipeline", counts=counts)


async def job_snapshot_maintenance() -> None:
    """Purge snapshots older than retention period."""
    from ingestion.snapshot_writer import SnapshotWriter
    writer = SnapshotWriter()
    deleted = await writer.purge_old_snapshots(
        retention_days=settings.snapshot_retention_days
    )
    logger.info("job_done", job="snapshot_maintenance", deleted=deleted)


def _row_to_record(row) -> "IssueRecord":
    """Convert SQLAlchemy FactIssue row to IssueRecord dataclass."""
    from kpi_engine.calculator import IssueRecord

    def _parse_list(val: str | None) -> list:
        if not val:
            return []
        try:
            return json.loads(val)
        except Exception:
            return []

    return IssueRecord(
        jira_key=row.jira_key,
        project_key=row.project_key,
        summary=row.summary or "",
        issue_type=row.issue_type or "Unknown",
        status=row.status or "",
        status_category=row.status_category or "To Do",
        priority=row.priority,
        assignee_id=row.assignee_id,
        component_ids=_parse_list(row.component_ids),
        fix_version_ids=_parse_list(row.fix_version_ids),
        epic_key=row.epic_key,
        created_date=row.created_date,
        resolved_date=row.resolved_date,
        updated_date=row.updated_date,
        due_date=row.due_date,
        age_days=row.age_days,
        resolution_time_days=row.resolution_time_days,
        cycle_time_days=row.cycle_time_days,
        times_reopened=row.times_reopened or 0,
        is_overdue=row.is_overdue or False,
        days_without_update=row.days_without_update,
        current_status_age_days=row.current_status_age_days,
        dq_missing_assignee=row.dq_missing_assignee or False,
        dq_missing_priority=row.dq_missing_priority or False,
        dq_missing_component=row.dq_missing_component or False,
        dq_missing_fix_version=row.dq_missing_fix_version or False,
        dq_missing_epic=row.dq_missing_epic or False,
        dq_missing_due_date=row.dq_missing_due_date or False,
        dq_closed_without_resolution=row.dq_closed_without_resolution or False,
        labels=_parse_list(row.labels),
        story_points=row.story_points,
        sprint_ids=_parse_list(row.sprint_ids),
    )


def create_scheduler() -> AsyncIOScheduler:
    """Build and return configured scheduler (not yet started)."""
    scheduler = AsyncIOScheduler(timezone="UTC")

    if not settings.scheduler_enabled:
        logger.warning("scheduler_disabled")
        return scheduler

    # Daily incremental extraction at 02:00 UTC
    scheduler.add_job(
        job_incremental_extraction,
        CronTrigger(hour=2, minute=0),
        id="incremental_extraction",
        name="Daily incremental Jira extraction",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # KPI calculation at 03:00 UTC (after extraction)
    scheduler.add_job(
        job_calculate_kpis,
        CronTrigger(hour=3, minute=0),
        id="kpi_calculation",
        name="Daily KPI calculation",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Full sync Sunday at 01:00 UTC
    scheduler.add_job(
        job_full_sync,
        CronTrigger(day_of_week="sun", hour=1, minute=0),
        id="full_sync",
        name="Weekly full Jira sync",
        replace_existing=True,
        misfire_grace_time=7200,
    )

    # Embedding pipeline at 03:30 UTC (after KPI calculation)
    scheduler.add_job(
        job_embedding_pipeline,
        CronTrigger(hour=3, minute=30),
        id="embedding_pipeline",
        name="Daily embedding generation",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Maintenance Friday at 04:00 UTC
    scheduler.add_job(
        job_snapshot_maintenance,
        CronTrigger(day_of_week="fri", hour=4, minute=0),
        id="snapshot_maintenance",
        name="Weekly snapshot maintenance",
        replace_existing=True,
    )

    logger.info("scheduler_configured", jobs=len(scheduler.get_jobs()))
    return scheduler
