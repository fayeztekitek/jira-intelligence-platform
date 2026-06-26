"""
kpi_engine/release.py — Fix version / release KPI analysis.

Provides ReleaseAnalyzer that computes per-version metrics:
  - Version completion: resolved / total issues
  - Scope increase: issues added after version release date
  - Delayed issues: unresolved issues past release date
  - Version readiness: completion % with delay penalty
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import structlog

from storage.models import DimVersion, FactIssue

logger = structlog.get_logger(__name__)


@dataclass
class VersionSummary:
    """Computed metrics for a single fix version / release."""
    version_id: str
    version_name: str
    project_key: str
    release_date: date | None
    is_released: bool
    is_overdue: bool

    total_issues: int = 0
    resolved_issues: int = 0
    unresolved_issues: int = 0
    completion_pct: float | None = None

    scope_increase: int = 0
    delayed_issues: int = 0
    readiness_score: float | None = None

    total_story_points: float = 0.0
    completed_story_points: float = 0.0

    def to_dict(self) -> dict:
        return {
            "version_id": self.version_id,
            "version_name": self.version_name,
            "project_key": self.project_key,
            "release_date": self.release_date.isoformat() if self.release_date else None,
            "is_released": self.is_released,
            "is_overdue": self.is_overdue,
            "total_issues": self.total_issues,
            "resolved_issues": self.resolved_issues,
            "unresolved_issues": self.unresolved_issues,
            "completion_pct": self.completion_pct,
            "scope_increase": self.scope_increase,
            "delayed_issues": self.delayed_issues,
            "readiness_score": self.readiness_score,
            "total_story_points": round(self.total_story_points, 1),
            "completed_story_points": round(self.completed_story_points, 1),
        }


class ReleaseAnalyzer:
    """Computes release / fix version KPIs from stored issue data."""

    def __init__(self, project_key: str, as_of: date | None = None):
        self.project_key = project_key
        self.as_of = as_of or date.today()

    async def analyze(self, db) -> list[VersionSummary]:
        """Analyze all versions for the project."""
        from sqlalchemy import select

        versions = (await db.execute(
            select(DimVersion).where(
                DimVersion.project_key == self.project_key
            ).order_by(DimVersion.release_date.desc().nullslast())
        )).scalars().all()

        project_issues = (await db.execute(
            select(FactIssue).where(
                FactIssue.project_key == self.project_key,
                FactIssue.fix_version_ids.isnot(None),
                FactIssue.fix_version_ids != "[]",
            )
        )).scalars().all()

        version_map: dict[str, list[FactIssue]] = {}
        for issue in project_issues:
            ids = self._parse_version_ids(issue.fix_version_ids)
            for vid in ids:
                version_map.setdefault(vid, []).append(issue)

        results: list[VersionSummary] = []
        for version in versions:
            issues = version_map.get(version.id, [])
            summary = self._compute_version(version, issues)
            results.append(summary)

        return results

    async def analyze_version(self, db, version_id: str) -> VersionSummary | None:
        """Analyze a single version by ID."""
        from sqlalchemy import select

        version = await db.get(DimVersion, version_id)
        if version is None:
            return None

        all_issues = (await db.execute(
            select(FactIssue).where(
                FactIssue.project_key == self.project_key,
                FactIssue.fix_version_ids.isnot(None),
                FactIssue.fix_version_ids != "[]",
            )
        )).scalars().all()

        matching = [
            i for i in all_issues
            if version_id in self._parse_version_ids(i.fix_version_ids)
        ]

        return self._compute_version(version, matching)

    def _compute_version(self, version: DimVersion, issues: list[FactIssue]) -> VersionSummary:
        total = len(issues)
        resolved = 0
        unresolved = 0
        total_sp = 0.0
        completed_sp = 0.0
        scope_increase = 0
        delayed = 0

        for issue in issues:
            sp = issue.story_points or 0.0
            total_sp += sp

            is_done = issue.status_category == "Done"
            if is_done:
                resolved += 1
                completed_sp += sp
            else:
                unresolved += 1

            # Scope increase: issue created after version release date
            if version.release_date and issue.created_date:
                cd = issue.created_date.date() if hasattr(issue.created_date, "date") else issue.created_date
                if cd > version.release_date:
                    scope_increase += 1

            # Delayed: unresolved issue past a PAST version release date
            if (not is_done) and version.release_date and version.release_date < self.as_of and issue.created_date:
                delayed += 1

        completion_pct = round((resolved / total) * 100, 1) if total > 0 else None

        delay_penalty = 0.0
        if total > 0:
            delay_ratio = delayed / total
            delay_penalty = delay_ratio * 100
        readiness = round(completion_pct - delay_penalty, 1) if completion_pct is not None else None

        is_overdue = False
        if version.release_date and version.release_date < self.as_of and unresolved > 0:
            is_overdue = True

        return VersionSummary(
            version_id=version.id,
            version_name=version.name,
            project_key=version.project_key,
            release_date=version.release_date,
            is_released=version.is_released,
            is_overdue=is_overdue or version.is_overdue,
            total_issues=total,
            resolved_issues=resolved,
            unresolved_issues=unresolved,
            completion_pct=completion_pct,
            scope_increase=scope_increase,
            delayed_issues=delayed,
            readiness_score=readiness,
            total_story_points=round(total_sp, 1),
            completed_story_points=round(completed_sp, 1),
        )

    @staticmethod
    def _parse_version_ids(raw: str | None) -> list[str]:
        if not raw:
            return []
        try:
            ids = json.loads(raw)
            return [str(i) for i in ids if i is not None]
        except (json.JSONDecodeError, TypeError, ValueError):
            return []
