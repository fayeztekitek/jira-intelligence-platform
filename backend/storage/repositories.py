"""
storage/repositories.py — Repository layer for complex queries.

Provides repository classes that encapsulate multi-table queries for
specific features (e.g. sprint burndown, trend history).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import structlog

from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


class SprintBurndownRepository:
    """Queries for sprint burndown data (daily remaining story points)."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_burndown(self, sprint_id: int) -> list[dict] | None:
        """Return daily burndown data for a sprint.

        Returns list of {date, remaining_points, ideal_points} or None
        if the sprint is not found.
        """
        from sqlalchemy import select
        from storage.models import DimSprint, FactIssue, FactTransition

        sprint = await self.db.get(DimSprint, sprint_id)
        if sprint is None:
            return None

        if not sprint.start_date or not sprint.end_date:
            logger.warning("sprint_missing_dates", sprint_id=sprint_id)
            return []

        # Get all issues for this sprint
        issues_result = await self.db.execute(
            select(FactIssue).where(
                FactIssue.sprint_ids.isnot(None),
                FactIssue.sprint_ids != "[]",
            )
        )
        all_issues = issues_result.scalars().all()

        sprint_issues = [
            i for i in all_issues
            if sprint_id in self._parse_sprint_ids(i.sprint_ids)
        ]

        if not sprint_issues:
            return []

        total_committed = sum(i.story_points or 0.0 for i in sprint_issues)

        # Collect resolution events for sprint issues
        jira_keys = [i.jira_key for i in sprint_issues]
        transitions_result = await self.db.execute(
            select(FactTransition).where(
                FactTransition.jira_key.in_(jira_keys),
                FactTransition.field == "status",
                FactTransition.to_string.in_(["Done", "Closed"]),
            ).order_by(FactTransition.changed_at.asc())
        )
        resolve_events = transitions_result.scalars().all()

        # Build day → resolved_points mapping
        resolved_by_day: dict[date, float] = {}
        issue_resolved: set[str] = set()

        for t in resolve_events:
            if t.changed_at is None:
                continue
            d = t.changed_at.date() if hasattr(t.changed_at, "date") else t.changed_at
            # Only count first resolution per issue
            if t.jira_key in issue_resolved:
                continue
            issue_resolved.add(t.jira_key)
            # Find the issue's story points
            for iss in sprint_issues:
                if iss.jira_key == t.jira_key:
                    resolved_by_day[d] = resolved_by_day.get(d, 0.0) + (iss.story_points or 0.0)
                    break

        # Build burndown series
        start = sprint.start_date.date() if hasattr(sprint.start_date, "date") else sprint.start_date
        end = sprint.end_date.date() if hasattr(sprint.end_date, "date") else sprint.end_date
        total_days = (end - start).days
        if total_days <= 0:
            total_days = 1

        result: list[dict] = []
        cumulative_resolved = 0.0

        for day_offset in range(total_days + 1):
            current_date = start + timedelta(days=day_offset)
            cumulative_resolved += resolved_by_day.get(current_date, 0.0)
            remaining = max(0.0, total_committed - cumulative_resolved)
            ideal = max(0.0, total_committed * (1 - day_offset / total_days))
            result.append({
                "date": current_date.isoformat(),
                "remaining_points": round(remaining, 1),
                "ideal_points": round(ideal, 1),
            })

        return result

    @staticmethod
    def _parse_sprint_ids(raw: str | None) -> list[int]:
        if not raw:
            return []
        try:
            ids = json.loads(raw)
            return [int(i) for i in ids if i is not None]
        except (json.JSONDecodeError, TypeError, ValueError):
            return []
