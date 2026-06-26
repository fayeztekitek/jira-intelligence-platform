"""
kpi_engine/sprint.py — Sprint velocity, predictability & scope analysis.

Provides SprintAnalyzer that consumes FactIssue + DimSprint data
and computes agile metrics per sprint.

Key metrics:
  - Velocity: story points completed per sprint
  - Commitment: story points planned at sprint start
  - Predictability: completed / commitment
  - Carry-over: open issues from previous sprint
  - Scope change: issues added/removed mid-sprint
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import structlog

from storage.models import DimSprint, FactIssue, FactTransition

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Sprint data model
# ---------------------------------------------------------------------------


@dataclass
class SprintSummary:
    """Computed metrics for a single sprint."""
    sprint_id: int
    sprint_name: str
    board_id: int
    state: str
    start_date: datetime | None
    end_date: datetime | None
    complete_date: datetime | None

    # Velocity metrics
    total_committed: float = 0.0
    total_completed: float = 0.0
    carry_over: float = 0.0
    predictability: float | None = None

    # Scope
    scope_added: float = 0.0
    scope_removed: float = 0.0

    # Counts
    issues_count: int = 0
    completed_count: int = 0
    carry_over_count: int = 0

    def to_dict(self) -> dict:
        return {
            "sprint_id": self.sprint_id,
            "sprint_name": self.sprint_name,
            "board_id": self.board_id,
            "state": self.state,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "complete_date": self.complete_date.isoformat() if self.complete_date else None,
            "total_committed": self.total_committed,
            "total_completed": self.total_completed,
            "carry_over": self.carry_over,
            "predictability": self.predictability,
            "scope_added": self.scope_added,
            "scope_removed": self.scope_removed,
            "issues_count": self.issues_count,
            "completed_count": self.completed_count,
            "carry_over_count": self.carry_over_count,
        }


# ---------------------------------------------------------------------------
# Sprint analyzer
# ---------------------------------------------------------------------------


class SprintAnalyzer:
    """Computes sprint velocity and related KPIs from stored issue data."""

    def __init__(self, project_key: str, as_of: date | None = None):
        self.project_key = project_key
        self.as_of = as_of or date.today()

    async def _get_scope_transitions(self, db) -> dict[str, list[FactTransition]]:
        """Fetch all sprint-related changelog transitions for the project."""
        from sqlalchemy import select, or_

        issue_keys = (await db.execute(
            select(FactIssue.jira_key).where(
                FactIssue.project_key == self.project_key,
                FactIssue.sprint_ids.isnot(None),
                FactIssue.sprint_ids != "[]",
            )
        )).scalars().all()

        if not issue_keys:
            return {}

        transitions = (await db.execute(
            select(FactTransition).where(
                FactTransition.jira_key.in_(issue_keys),
                or_(
                    FactTransition.field == "Sprint",
                    FactTransition.field.startswith("customfield"),
                    FactTransition.field.startswith("customfield_"),
                ),
            )
        )).scalars().all()

        result: dict[str, list[FactTransition]] = {}
        for t in transitions:
            result.setdefault(t.jira_key, []).append(t)
        return result

    async def analyze(self, db) -> list[SprintSummary]:
        """Analyze all sprints for the project.

        Returns list of SprintSummary sorted by end_date DESC.
        """
        from sqlalchemy import select

        all_sprints = (await db.execute(
            select(DimSprint).order_by(DimSprint.end_date.desc())
        )).scalars().all()

        project_issues = (await db.execute(
            select(FactIssue).where(
                FactIssue.project_key == self.project_key,
                FactIssue.sprint_ids.isnot(None),
                FactIssue.sprint_ids != "[]",
            )
        )).scalars().all()

        sprint_transitions = await self._get_scope_transitions(db)

        sprint_map: dict[int, list[FactIssue]] = {}
        for issue in project_issues:
            ids = self._parse_sprint_ids(issue.sprint_ids)
            for sid in ids:
                if sid is not None:
                    sprint_map.setdefault(sid, []).append(issue)

        sprint_name_map = {s.id: s.name for s in all_sprints}

        results: list[SprintSummary] = []
        for sprint in all_sprints:
            issues = sprint_map.get(sprint.id, [])
            if not issues:
                continue
            summary = self._compute_sprint(sprint, issues, sprint_transitions, sprint_name_map)
            results.append(summary)

        return results

    async def analyze_sprint(self, db, sprint_id: int) -> SprintSummary | None:
        """Analyze a single sprint by ID."""
        from sqlalchemy import select

        sprint = await db.get(DimSprint, sprint_id)
        if sprint is None:
            return None

        all_issues = (await db.execute(
            select(FactIssue).where(
                FactIssue.project_key == self.project_key,
                FactIssue.sprint_ids.isnot(None),
                FactIssue.sprint_ids != "[]",
            )
        )).scalars().all()

        matching = [
            i for i in all_issues
            if sprint_id in self._parse_sprint_ids(i.sprint_ids)
        ]

        sprint_transitions = await self._get_scope_transitions(db)
        sprint_name_map = {sprint.id: sprint.name} if sprint else {}

        return self._compute_sprint(sprint, matching, sprint_transitions, sprint_name_map)

    # ── Internals ────────────────────────────────────────────────────────

    def _compute_sprint(
        self,
        sprint: DimSprint,
        issues: list[FactIssue],
        sprint_transitions: dict[str, list[FactTransition]] | None = None,
        sprint_name_map: dict[int, str] | None = None,
    ) -> SprintSummary:
        committed = 0.0
        completed = 0.0
        carry_over = 0.0
        carry_over_count = 0
        completed_count = 0
        scope_added = 0.0
        scope_removed = 0.0

        sprint_start: datetime | None = sprint.start_date
        sprint_end: datetime | None = sprint.end_date

        for issue in issues:
            sp = issue.story_points or 0.0
            committed += sp

            is_done = issue.status_category == "Done"
            if is_done and issue.resolved_date and sprint_end:
                rd = self._naive(issue.resolved_date)
                se = self._naive(sprint_end)
                if rd <= se:
                    completed += sp
                    completed_count += 1
            elif issue.resolved_date and sprint_end:
                rd = self._naive(issue.resolved_date)
                se = self._naive(sprint_end)
                if rd <= se:
                    completed += sp
                    completed_count += 1

            # Carry-over: not done, and existed before sprint start
            if not is_done and issue.created_date and sprint_start:
                if self._naive(issue.created_date) < self._naive(sprint_start):
                    carry_over += sp
                    carry_over_count += 1

            # Scope change: check transitions for sprint field changes
            if sprint_transitions and sprint_name_map and sprint.start_date:
                iss = self._compute_scope_change(
                    issue, sprint, sprint_name_map,
                    sprint_transitions.get(issue.jira_key, []),
                )
                scope_added += iss[0]
                scope_removed += iss[1]

        predictability = round(completed / committed, 4) if committed > 0 else None

        return SprintSummary(
            sprint_id=sprint.id,
            sprint_name=sprint.name,
            board_id=sprint.board_id,
            state=sprint.state,
            start_date=sprint.start_date,
            end_date=sprint.end_date,
            complete_date=sprint.complete_date,
            total_committed=round(committed, 1),
            total_completed=round(completed, 1),
            carry_over=round(carry_over, 1),
            predictability=predictability,
            issues_count=len(issues),
            completed_count=completed_count,
            carry_over_count=carry_over_count,
            scope_added=round(scope_added, 1),
            scope_removed=round(scope_removed, 1),
        )

    @staticmethod
    def _compute_scope_change(
        issue: FactIssue,
        sprint: DimSprint,
        sprint_name_map: dict[int, str],
        transitions: list[FactTransition],
    ) -> tuple[float, float]:
        """Compute scope_added, scope_removed for one issue in a sprint.

        Returns (added_sp, removed_sp).
        """
        sprint_name = sprint_name_map.get(sprint.id, "")
        if not sprint_name or not sprint.start_date:
            return (0.0, 0.0)

        sp = issue.story_points or 0.0
        added = 0.0
        removed = 0.0

        for t in transitions:
            if t.changed_at is None or t.changed_at < sprint.start_date:
                continue

            to_contains = t.to_string and sprint_name.lower() in t.to_string.lower()
            from_contains = t.from_string and sprint_name.lower() in t.from_string.lower()

            if to_contains and not from_contains:
                added += sp
            elif from_contains and not to_contains:
                removed += sp

        return (added, removed)

    @staticmethod
    def _naive(dt: datetime) -> datetime:
        """Strip timezone info for safe comparison (SQLite doesn't preserve tz)."""
        return dt.replace(tzinfo=None) if dt else dt

    @staticmethod
    def _parse_sprint_ids(raw: str | None) -> list[int]:
        if not raw:
            return []
        try:
            ids = json.loads(raw)
            return [int(i) for i in ids if i is not None]
        except (json.JSONDecodeError, TypeError, ValueError):
            return []

    @staticmethod
    def _guess_prev_sprint_end(sprint: DimSprint) -> datetime | None:
        """Approximate previous sprint end as 2 weeks before this sprint started."""
        if sprint.start_date:
            from datetime import timedelta
            return sprint.start_date - timedelta(days=14)
        return None
