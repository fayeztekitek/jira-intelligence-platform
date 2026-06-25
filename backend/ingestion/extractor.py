"""
ingestion/extractor.py — Jira data extraction, transformation, and storage.

Responsibilities:
1. Extract projects, issues, changelogs, sprints, versions, components
2. Transform raw Jira JSON into DB models
3. Upsert into fact/dimension tables
4. Record extraction run audit log
5. Support incremental loading (only updated issues)
6. Compute derived fields (age, DQ flags, cycle time estimate)
"""
import json
import uuid
from datetime import datetime, timezone, date
from typing import Optional

import structlog

from config import get_settings
from jira_connector.client import JiraClient
from jira_connector.fields import FieldDiscoverer
from storage.database import get_db
from storage.models import (
    DimProject, DimUser, DimComponent, DimVersion, DimSprint,
    FactIssue, FactTransition, ExtractionRun, RunStatus
)

logger = structlog.get_logger(__name__)
settings = get_settings()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
    return None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None


def _today_utc() -> datetime:
    return datetime.now(timezone.utc)


def _days_between(start: datetime | None, end: datetime | None) -> float | None:
    if start and end:
        delta = (end - start.replace(tzinfo=timezone.utc)
                 if start.tzinfo is None else end - start)
        return round(delta.total_seconds() / 86400, 2)
    return None


class JiraExtractor:
    """
    Orchestrates full or incremental Jira data extraction.
    """

    def __init__(self, client: JiraClient):
        self.client = client
        self.run_id = str(uuid.uuid4())
        self.stats = {
            "projects": 0,
            "issues": 0,
            "updated": 0,
            "transitions": 0,
            "errors": 0,
            "cycle_time_computed": 0,
            "first_responses_computed": 0,
        }
        self._field_map: dict[str, str] | None = None

    async def _get_field_map(self) -> dict[str, str]:
        if self._field_map is None:
            discoverer = FieldDiscoverer(self.client)
            self._field_map = await discoverer.get_field_map()
        return self._field_map

    async def run_full_extraction(self, triggered_by: str = "manual") -> str:
        """
        Full extraction: all projects, all issues.
        Returns the run_id for audit tracking.
        """
        logger.info("extraction_start", run_id=self.run_id, type="full")
        started = _today_utc()

        async with get_db() as db:
            run = ExtractionRun(
                run_id=self.run_id,
                run_type="full",
                triggered_by=triggered_by,
                started_at=started,
                status=RunStatus.RUNNING,
            )
            db.add(run)
            await db.flush()

        try:
            projects = await self.client.get_all_projects()
            for proj_data in projects:
                await self._extract_project(proj_data)

            duration = (_today_utc() - started).total_seconds()
            await self._finalize_run(RunStatus.SUCCESS, duration)
            logger.info("extraction_complete", run_id=self.run_id, **self.stats)

        except Exception as e:
            logger.error("extraction_failed", run_id=self.run_id, error=str(e))
            await self._finalize_run(RunStatus.FAILED, 0, str(e))
            raise

        return self.run_id

    async def run_incremental_extraction(
        self,
        since_hours: int = 24,
        triggered_by: str = "scheduler",
    ) -> str:
        """
        Incremental extraction: only issues updated in the last N hours.
        """
        logger.info("extraction_start", run_id=self.run_id, type="incremental",
                    since_hours=since_hours)
        started = _today_utc()

        async with get_db() as db:
            run = ExtractionRun(
                run_id=self.run_id,
                run_type="incremental",
                triggered_by=triggered_by,
                started_at=started,
                status=RunStatus.RUNNING,
            )
            db.add(run)
            await db.flush()

        try:
            projects = await self.client.get_all_projects()
            for proj_data in projects:
                await self._upsert_project(proj_data)
                project_key = proj_data.get("key")
                jql = (
                    f'project = "{project_key}" '
                    f'AND updated >= "-{since_hours}h" '
                    f'ORDER BY updated DESC'
                )
                await self._extract_issues_by_jql(project_key, jql)

            duration = (_today_utc() - started).total_seconds()
            await self._finalize_run(RunStatus.SUCCESS, duration)

        except Exception as e:
            await self._finalize_run(RunStatus.FAILED, 0, str(e))
            raise

        return self.run_id

    # ─── Project extraction ───────────────────────────────────────────────────

    async def _extract_project(self, proj_data: dict) -> None:
        project_key = proj_data.get("key")
        try:
            await self._upsert_project(proj_data)
            await self._extract_project_metadata(project_key)

            jql = f'project = "{project_key}" ORDER BY created ASC'
            await self._extract_issues_by_jql(project_key, jql)
            self.stats["projects"] += 1

        except Exception as e:
            self.stats["errors"] += 1
            logger.error("project_extraction_failed", project=project_key, error=str(e))

    async def _extract_project_metadata(self, project_key: str) -> None:
        """Extract versions, components, sprints."""
        try:
            versions = await self.client.get_versions(project_key)
            if isinstance(versions, list):
                async with get_db() as db:
                    for v in versions:
                        await self._upsert_version(db, v, project_key)

            components = await self.client.get_components(project_key)
            if isinstance(components, list):
                async with get_db() as db:
                    for c in components:
                        await self._upsert_component(db, c, project_key)

            boards = await self.client.get_boards(project_key)
            for board in boards:
                sprints = await self.client.get_sprints(board["id"])
                async with get_db() as db:
                    for s in sprints:
                        await self._upsert_sprint(db, s)

        except Exception as e:
            logger.warning("metadata_extraction_partial", project=project_key, error=str(e))

    async def _extract_issues_by_jql(self, project_key: str, jql: str) -> None:
        batch = []
        field_map = await self._get_field_map()
        async for raw_issue in self.client.search_issues(jql, field_map=field_map):
            issue_model = self._transform_issue(raw_issue, project_key, field_map)
            batch.append((issue_model, raw_issue))

            if len(batch) >= 50:
                await self._flush_issue_batch(batch)
                batch.clear()

        if batch:
            await self._flush_issue_batch(batch)

    async def _flush_issue_batch(self, batch: list[tuple]) -> None:
        async with get_db() as db:
            for issue_model, raw_issue in batch:
                # Check if exists
                from sqlalchemy import select
                result = await db.execute(
                    select(FactIssue).where(FactIssue.jira_key == issue_model.jira_key)
                )
                existing = result.scalar_one_or_none()

                if existing:
                    # Update fields
                    for attr in vars(issue_model):
                        if not attr.startswith("_"):
                            try:
                                setattr(existing, attr, getattr(issue_model, attr))
                            except AttributeError:
                                pass
                    self.stats["updated"] += 1
                else:
                    db.add(issue_model)
                    self.stats["issues"] += 1

                # Extract user dims
                issue_key = issue_model.jira_key
                fields = raw_issue.get("fields", {})
                for user_field in ["assignee", "reporter"]:
                    user_data = fields.get(user_field)
                    if user_data:
                        await self._upsert_user(db, user_data)

        # Extract changelog separately (outside issue batch for rate limiting)
        for issue_model, raw_issue in batch:
            await self._extract_changelog(issue_model.jira_key)
            await self._compute_cycle_time(issue_model.jira_key)
            reporter = (raw_issue.get("fields", {}) or {}).get("reporter") or {}
            await self._compute_first_response_date(
                issue_model.jira_key,
                reporter.get("accountId"),
            )

    async def _extract_changelog(self, issue_key: str) -> None:
        try:
            changelog = await self.client.get_issue_changelog(issue_key)
            if not changelog:
                return

            async with get_db() as db:
                from sqlalchemy import select
                for entry in changelog:
                    for item in entry.get("items", []):
                        changelog_id = f"{entry['id']}_{item.get('field', 'unknown')}"
                        exists = await db.execute(
                            select(FactTransition).where(
                                FactTransition.changelog_id == changelog_id
                            )
                        )
                        if exists.scalar_one_or_none():
                            continue

                        author = entry.get("author", {})
                        transition = FactTransition(
                            jira_key=issue_key,
                            changelog_id=changelog_id,
                            field=item.get("field"),
                            from_value=item.get("from"),
                            from_string=item.get("fromString"),
                            to_value=item.get("to"),
                            to_string=item.get("toString"),
                            changed_by_id=author.get("accountId"),
                            changed_by_name=author.get("displayName"),
                            changed_at=_parse_dt(entry.get("created")),
                        )
                        db.add(transition)
                        self.stats["transitions"] += 1

        except Exception as e:
            logger.warning("changelog_extraction_failed", issue=issue_key, error=str(e))

    async def _compute_cycle_time(self, issue_key: str) -> None:
        """Compute cycle_time_days from changelog status transitions.

        cycle_time = resolved_date - first entry into an 'In Progress' status.
        Uses configurable jira_in_progress_statuses to match status names.
        If resolved_date is None but the issue has moved to a Done status,
        uses the last Done transition timestamp as the end date.
        """
        in_progress_names = {
            s.strip()
            for s in settings.jira_in_progress_statuses.split(",")
            if s.strip()
        }
        if not in_progress_names:
            return

        try:
            from sqlalchemy import select
            async with get_db() as db:
                result = await db.execute(
                    select(FactTransition)
                    .where(
                        FactTransition.jira_key == issue_key,
                        FactTransition.field == "status",
                    )
                    .order_by(FactTransition.changed_at.asc())
                )
                transitions = result.scalars().all()

            if not transitions:
                return

            first_in_progress: datetime | None = None
            last_done: datetime | None = None
            for t in transitions:
                if t.to_string in in_progress_names and first_in_progress is None:
                    first_in_progress = t.changed_at
                if t.to_string in ("Done", "Closed", "Resolved"):
                    last_done = t.changed_at

            if first_in_progress is None:
                return

            async with get_db() as db:
                issue = await db.execute(
                    select(FactIssue).where(FactIssue.jira_key == issue_key)
                )
                issue_row = issue.scalar_one_or_none()
                if issue_row is None:
                    return

                end = issue_row.resolved_date or last_done
                if end is None:
                    return
                if isinstance(end, date) and not isinstance(end, datetime):
                    end = datetime.combine(end, datetime.min.time())

                end_utc = end.replace(tzinfo=None)
                start_utc = first_in_progress.replace(tzinfo=None)
                cycle = (end_utc - start_utc).total_seconds() / 86400.0
                if cycle >= 0:
                    issue_row.cycle_time_days = round(cycle, 2)
                    if issue_row.lead_time_days is None and issue_row.created_date:
                        lt_utc = issue_row.created_date.replace(tzinfo=None)
                        lt = (end_utc - lt_utc).total_seconds() / 86400.0
                        issue_row.lead_time_days = round(lt, 2)
                    await db.commit()
                    self.stats["cycle_time_computed"] += 1

        except Exception as e:
            logger.warning("cycle_time_computation_failed", issue=issue_key, error=str(e))

    async def _compute_first_response_date(self, issue_key: str, reporter_id: str | None) -> None:
        """Compute first_response_date from issue comments.

        The first response is the earliest comment by someone other than the
        reporter (first non-author reply). Sets FactIssue.first_response_date.
        """
        if not reporter_id:
            return
        try:
            comments = await self.client.get_issue_comments(issue_key)
            if not comments:
                return

            first_reply: datetime | None = None
            for c in comments:
                author = c.get("author", {})
                if author.get("accountId") != reporter_id:
                    first_reply = _parse_dt(c.get("created"))
                    break

            if first_reply is None:
                return

            from sqlalchemy import select
            async with get_db() as db:
                result = await db.execute(
                    select(FactIssue).where(FactIssue.jira_key == issue_key)
                )
                issue_row = result.scalar_one_or_none()
                if issue_row is None:
                    return

                issue_row.first_response_date = first_reply
                await db.commit()
                self.stats["first_responses_computed"] += 1

        except Exception as e:
            logger.warning("first_response_computation_failed", issue=issue_key, error=str(e))

    # ─── Transform ────────────────────────────────────────────────────────────

    def _transform_issue(self, raw: dict, project_key: str, field_map: dict[str, str] | None = None) -> FactIssue:
        fm = field_map or {}
        sprint_field = fm.get("sprint") or settings.jira_field_sprint
        epic_field = fm.get("epic_link") or settings.jira_field_epic_link
        sp_field = fm.get("story_points") or settings.jira_field_story_points

        fields = raw.get("fields", {})
        now = _today_utc()

        created = _parse_dt(fields.get("created"))
        updated = _parse_dt(fields.get("updated"))
        resolved = _parse_dt(fields.get("resolutiondate"))
        due = _parse_date(fields.get("duedate"))

        age_days = int((now - created).days) if created else None
        days_no_update = int((now - updated).days) if updated else None
        resolution_days = _days_between(created, resolved)
        lead_days = resolution_days
        cycle_days = None  # Computed from changelog in P1

        assignee = fields.get("assignee") or {}
        components = fields.get("components") or []
        fix_versions = fields.get("fixVersions") or []
        labels = fields.get("labels") or []
        sprints_raw = fields.get(sprint_field) or []

        status_obj = fields.get("status") or {}
        status_name = status_obj.get("name", "")
        status_cat = (status_obj.get("statusCategory") or {}).get("name", "")

        priority_obj = fields.get("priority") or {}
        resolution_obj = fields.get("resolution") or {}

        epic_key = (
            fields.get(epic_field)
            or (fields.get("parent") or {}).get("key")
        )

        story_points = fields.get(sp_field)
        if isinstance(story_points, dict):
            story_points = None

        is_overdue = bool(
            due and status_cat != "Done" and due < date.today()
        )

        dq_missing_assignee = not bool(assignee)
        dq_missing_priority = not bool(priority_obj)
        dq_missing_component = len(components) == 0
        dq_missing_fix_version = len(fix_versions) == 0
        dq_missing_epic = not bool(epic_key)
        dq_missing_due_date = not bool(due)
        dq_closed_without_resolution = (
            status_cat == "Done" and not bool(resolution_obj)
        )

        return FactIssue(
            jira_id=raw.get("id"),
            jira_key=raw.get("key"),
            project_key=project_key,
            assignee_id=assignee.get("accountId"),
            reporter_id=(fields.get("reporter") or {}).get("accountId"),
            component_ids=json.dumps([c["id"] for c in components]),
            fix_version_ids=json.dumps([v["id"] for v in fix_versions]),
            sprint_ids=json.dumps(
                [s.get("id") for s in sprints_raw if isinstance(s, dict)]
            ),
            summary=fields.get("summary", ""),
            description=(fields.get("description") or "")[:4000],
            issue_type=(fields.get("issuetype") or {}).get("name"),
            status=status_name,
            status_category=status_cat,
            priority=priority_obj.get("name"),
            resolution=resolution_obj.get("name"),
            labels=json.dumps(labels),
            epic_key=epic_key,
            parent_key=(fields.get("parent") or {}).get("key"),
            story_points=story_points,
            original_estimate_seconds=fields.get("timeoriginalestimate"),
            time_spent_seconds=fields.get("timespent"),
            created_date=created,
            updated_date=updated,
            due_date=due,
            resolved_date=resolved,
            age_days=age_days,
            resolution_time_days=resolution_days,
            lead_time_days=lead_days,
            cycle_time_days=cycle_days,
            is_overdue=is_overdue,
            days_without_update=days_no_update,
            dq_missing_assignee=dq_missing_assignee,
            dq_missing_priority=dq_missing_priority,
            dq_missing_component=dq_missing_component,
            dq_missing_fix_version=dq_missing_fix_version,
            dq_missing_epic=dq_missing_epic,
            dq_missing_due_date=dq_missing_due_date,
            dq_closed_without_resolution=dq_closed_without_resolution,
            last_synced_at=now,
            raw_json=json.dumps({"key": raw.get("key"), "fields_keys": list(fields.keys())}),
        )

    # ─── Upsert helpers ───────────────────────────────────────────────────────

    async def _upsert_project(self, data: dict) -> None:
        lead = data.get("lead") or {}
        async with get_db() as db:
            from sqlalchemy import select
            result = await db.execute(
                select(DimProject).where(DimProject.id == data["key"])
            )
            proj = result.scalar_one_or_none()
            if proj:
                proj.name = data.get("name", proj.name)
                proj.description = (data.get("description") or "")[:1000]
            else:
                db.add(DimProject(
                    id=data["key"],
                    name=data.get("name", ""),
                    project_type=data.get("projectTypeKey"),
                    lead_account_id=lead.get("accountId"),
                    lead_display_name=lead.get("displayName"),
                    description=(data.get("description") or "")[:1000],
                    url=data.get("self"),
                ))

    async def _upsert_user(self, db, data: dict) -> None:
        from sqlalchemy import select
        account_id = data.get("accountId")
        if not account_id:
            return
        result = await db.execute(
            select(DimUser).where(DimUser.account_id == account_id)
        )
        if not result.scalar_one_or_none():
            db.add(DimUser(
                account_id=account_id,
                display_name=data.get("displayName"),
                email=data.get("emailAddress"),
                is_active=data.get("active", True),
                timezone=data.get("timeZone"),
            ))

    async def _upsert_version(self, db, data: dict, project_key: str) -> None:
        from sqlalchemy import select
        v_id = str(data.get("id", ""))
        result = await db.execute(
            select(DimVersion).where(DimVersion.id == v_id)
        )
        if not result.scalar_one_or_none():
            db.add(DimVersion(
                id=v_id,
                project_key=project_key,
                name=data.get("name", ""),
                description=data.get("description"),
                release_date=_parse_date(data.get("releaseDate")),
                is_released=data.get("released", False),
                is_archived=data.get("archived", False),
                is_overdue=data.get("overdue", False),
            ))

    async def _upsert_component(self, db, data: dict, project_key: str) -> None:
        from sqlalchemy import select
        c_id = str(data.get("id", ""))
        result = await db.execute(
            select(DimComponent).where(DimComponent.id == c_id)
        )
        if not result.scalar_one_or_none():
            db.add(DimComponent(
                id=c_id,
                project_key=project_key,
                name=data.get("name", ""),
                description=data.get("description"),
                lead_account_id=(data.get("lead") or {}).get("accountId"),
            ))

    async def _upsert_sprint(self, db, data: dict) -> None:
        from sqlalchemy import select
        result = await db.execute(
            select(DimSprint).where(DimSprint.id == data.get("id"))
        )
        if not result.scalar_one_or_none():
            db.add(DimSprint(
                id=data.get("id"),
                board_id=data.get("originBoardId"),
                name=data.get("name", ""),
                state=data.get("state"),
                start_date=_parse_dt(data.get("startDate")),
                end_date=_parse_dt(data.get("endDate")),
                complete_date=_parse_dt(data.get("completeDate")),
                goal=data.get("goal"),
            ))

    async def _finalize_run(
        self,
        status: RunStatus,
        duration: float,
        error: str | None = None,
    ) -> None:
        async with get_db() as db:
            from sqlalchemy import select
            result = await db.execute(
                select(ExtractionRun).where(ExtractionRun.run_id == self.run_id)
            )
            run = result.scalar_one_or_none()
            if run:
                run.status = status
                run.completed_at = _today_utc()
                run.duration_seconds = duration
                run.projects_processed = self.stats["projects"]
                run.issues_extracted = self.stats["issues"]
                run.issues_updated = self.stats["updated"]
                run.transitions_extracted = self.stats["transitions"]
                run.error_count = self.stats["errors"]
                run.error_details = error
                run.jira_api_calls = self.client.api_call_count
