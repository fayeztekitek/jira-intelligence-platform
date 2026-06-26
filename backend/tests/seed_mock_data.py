"""
tests/seed_mock_data.py

Generates realistic mock data for local testing without a Jira connection.
Creates:
  - 3 projects (CORE, MOBILE, INFRA)
  - ~540 issues spanning 18 months
  - Status transitions / changelog entries
  - Calculated KPIs and risk scores
  - Extraction run audit record

Run: python -m tests.seed_mock_data
"""
import asyncio
import json
import random
import sys
import os
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage.database import init_db, get_db
from storage.models import (
    DimProject, DimUser, DimComponent, DimVersion, DimSprint,
    FactIssue, FactTransition, ExtractionRun, RunStatus,
)
from kpi_engine.calculator import KPICalculator, IssueRecord
from risk_engine.scorer import RiskScorer
from ingestion.snapshot_writer import SnapshotWriter

random.seed(42)

# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

PROJECTS = [
    {"key": "CORE",   "name": "Core Platform",     "type": "software", "lead": "Alice Martin"},
    {"key": "MOBILE", "name": "Mobile Application", "type": "software", "lead": "Bob Chen"},
    {"key": "INFRA",  "name": "Infrastructure",     "type": "software", "lead": "Carol White"},
]

USERS = [
    ("u001", "Alice Martin",   "alice@company.com"),
    ("u002", "Bob Chen",       "bob@company.com"),
    ("u003", "Carol White",    "carol@company.com"),
    ("u004", "David Kim",      "david@company.com"),
    ("u005", "Emma Wilson",    "emma@company.com"),
    ("u006", "Frank Dubois",   "frank@company.com"),
    ("u007", "Grace Lee",      "grace@company.com"),
    ("u008", "Hugo Berger",    "hugo@company.com"),
]

COMPONENTS = {
    "CORE":   ["Authentication", "API Gateway", "Database", "Reporting"],
    "MOBILE": ["iOS", "Android", "Push Notifications", "Offline Sync"],
    "INFRA":  ["Kubernetes", "CI/CD", "Monitoring", "Security"],
}

VERSIONS = {
    "CORE":   ["v2.1.0", "v2.2.0", "v2.3.0", "v3.0.0"],
    "MOBILE": ["1.4.0", "1.5.0", "1.6.0", "2.0.0"],
    "INFRA":  ["infra-2024-Q1", "infra-2024-Q2", "infra-2024-Q3", "infra-2025-Q1"],
}

ISSUE_TYPES = ["Bug", "Story", "Task", "Epic", "Sub-task"]
PRIORITIES   = ["Critical", "High", "Medium", "Low"]
STATUSES     = [
    ("To Do",       "To Do"),
    ("In Progress", "In Progress"),
    ("In Review",   "In Progress"),
    ("Done",        "Done"),
    ("Closed",      "Done"),
    ("Reopened",    "In Progress"),
]

SUMMARIES = {
    "Bug": [
        "Login fails with OAuth provider after session expiry",
        "Data export truncates records above 10k rows",
        "Push notification not delivered on Android 14",
        "Memory leak in background sync service",
        "Dashboard chart renders incorrectly on Safari",
        "API returns 500 when component field is null",
        "Kubernetes pod crashes under high load",
        "CI pipeline fails intermittently on test step",
        "Report PDF generates with wrong date range",
        "Search returns duplicate results on pagination",
        "User permissions not enforced on bulk operations",
        "Offline mode loses changes on reconnect",
    ],
    "Story": [
        "Implement dark mode for mobile application",
        "Add CSV export to all dashboard views",
        "Build user preference persistence layer",
        "Integrate SSO with Azure Active Directory",
        "Create audit log viewer for compliance team",
        "Add bulk issue assignment feature",
        "Implement real-time notifications via WebSocket",
        "Build executive summary report generator",
        "Add two-factor authentication support",
        "Create component health score dashboard",
    ],
    "Task": [
        "Upgrade PostgreSQL to version 16",
        "Rotate API keys for production environment",
        "Update Kubernetes cluster to 1.29",
        "Add indexes to slow query endpoints",
        "Migrate legacy endpoints to v2 API",
        "Clean up unused Docker images",
        "Update SSL certificates for all services",
        "Review and close stale pull requests",
    ],
}


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc)


def _rand_date(start: date, end: date) -> date:
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, delta))


def _rand_dt(start: date, end: date) -> datetime:
    d = _rand_date(start, end)
    return _utc(datetime(d.year, d.month, d.day,
                         random.randint(8, 18), random.randint(0, 59)))


def _make_issues(project_key: str, count: int, start: date, end: date) -> list[dict]:
    issues = []
    comp_ids = [f"{project_key}-C{i+1}" for i in range(len(COMPONENTS[project_key]))]
    ver_ids  = [f"{project_key}-V{i+1}" for i in range(len(VERSIONS[project_key]))]
    user_ids = [u[0] for u in USERS]

    for i in range(count):
        itype = random.choices(
            ISSUE_TYPES, weights=[25, 35, 25, 5, 10]
        )[0]
        priority = random.choices(
            PRIORITIES, weights=[5, 20, 50, 25]
        )[0]
        status_tuple = random.choices(
            STATUSES, weights=[20, 25, 15, 30, 8, 2]
        )[0]
        status, status_cat = status_tuple

        created_dt = _rand_dt(start, end)
        created_d  = created_dt.date()

        # Resolved?
        resolved_dt = None
        resolution_days = None
        if status_cat == "Done":
            resolve_offset = random.randint(1, 45)
            resolved_dt = _utc(datetime(
                created_dt.year, created_dt.month, created_dt.day,
            ) + timedelta(days=resolve_offset))
            resolution_days = resolve_offset

        # Due date (50% of issues)
        due_date = None
        if random.random() < 0.5:
            due_date = (created_dt + timedelta(days=random.randint(7, 60))).date()

        age_days = (end - created_d).days

        # Reopened (8% chance)
        times_reopened = 0
        if random.random() < 0.08:
            times_reopened = random.randint(1, 3)

        # DQ flags
        dq_no_assignee   = random.random() < 0.15
        dq_no_priority   = random.random() < 0.10
        dq_no_component  = random.random() < 0.20
        dq_no_version    = random.random() < 0.25
        dq_no_epic       = random.random() < 0.30
        dq_no_due        = due_date is None

        assignee = None if dq_no_assignee else random.choice(user_ids)

        # Summary
        pool = SUMMARIES.get(itype, SUMMARIES["Task"])
        summary = random.choice(pool) + f" [{project_key}-{i+1}]"

        # Overdue?
        is_overdue = bool(
            due_date and status_cat != "Done" and due_date < end
        )

        # Story points for Stories/Epics
        sp = None
        if itype in ("Story", "Epic"):
            sp = random.choice([1, 2, 3, 5, 8, 13])

        # Current status age
        status_age = random.randint(0, min(age_days, 30)) if age_days > 0 else 0

        issues.append({
            "jira_id": f"{project_key}-ID-{1000 + i}",
            "jira_key": f"{project_key}-{1000 + i}",
            "project_key": project_key,
            "assignee_id": assignee,
            "reporter_id": random.choice(user_ids),
            "component_ids": json.dumps(
                [] if dq_no_component else [random.choice(comp_ids)]
            ),
            "fix_version_ids": json.dumps(
                [] if dq_no_version else [random.choice(ver_ids)]
            ),
            "sprint_ids": json.dumps([]),
            "summary": summary,
            "issue_type": itype,
            "status": status,
            "status_category": status_cat,
            "priority": None if dq_no_priority else priority,
            "resolution": "Done" if status_cat == "Done" else None,
            "created_date": created_dt,
            "updated_date": resolved_dt or created_dt,
            "due_date": due_date,
            "resolved_date": resolved_dt,
            "age_days": age_days,
            "resolution_time_days": resolution_days,
            "cycle_time_days": round(resolution_days * 0.6, 1) if resolution_days else None,
            "times_reopened": times_reopened,
            "is_overdue": is_overdue,
            "days_without_update": (end - (resolved_dt or created_dt).date()).days,
            "current_status_age_days": status_age,
            "story_points": sp,
            "dq_missing_assignee": dq_no_assignee,
            "dq_missing_priority": dq_no_priority,
            "dq_missing_component": dq_no_component,
            "dq_missing_fix_version": dq_no_version,
            "dq_missing_epic": dq_no_epic,
            "dq_missing_due_date": dq_no_due,
            "dq_closed_without_resolution": (status_cat == "Done" and not resolved_dt),
            "epic_key": None,
            "labels": json.dumps([]),
            "last_synced_at": datetime.now(timezone.utc),
        })

    return issues


async def seed():
    print("Initializing database...")
    await init_db()

    end_date = date.today()
    start_date = end_date - timedelta(days=548)  # 18 months

    print(f"Seeding data from {start_date} to {end_date}")

    async with get_db() as db:
        # Users
        for uid, name, email in USERS:
            db.add(DimUser(account_id=uid, display_name=name,
                           email=email, is_active=True))

        # Projects, components, versions
        for p in PROJECTS:
            db.add(DimProject(
                id=p["key"], name=p["name"],
                project_type=p["type"],
                lead_display_name=p["lead"],
                is_active=True,
            ))
            for ci, comp in enumerate(COMPONENTS[p["key"]]):
                db.add(DimComponent(
                    id=f"{p['key']}-C{ci+1}",
                    project_key=p["key"],
                    name=comp,
                ))
            for vi, ver in enumerate(VERSIONS[p["key"]]):
                release_date = end_date - timedelta(days=random.randint(0, 180))
                db.add(DimVersion(
                    id=f"{p['key']}-V{vi+1}",
                    project_key=p["key"],
                    name=ver,
                    is_released=(vi < 2),
                    release_date=release_date,
                ))

    # Issues per project
    issue_counts = {"CORE": 200, "MOBILE": 180, "INFRA": 160}

    all_issues: dict[str, list] = {}
    for proj_key, count in issue_counts.items():
        print(f"Generating {count} issues for {proj_key}...")
        issues = _make_issues(proj_key, count, start_date, end_date)
        all_issues[proj_key] = issues

        async with get_db() as db:
            for issue_data in issues:
                db.add(FactIssue(**issue_data))

        # Changelog entries
        async with get_db() as db:
            for issue_data in issues:
                if random.random() < 0.7:  # 70% have changelog
                    num_transitions = random.randint(1, 5)
                    for t in range(num_transitions):
                        db.add(FactTransition(
                            jira_key=issue_data["jira_key"],
                            changelog_id=str(uuid4()),
                            field="status",
                            from_string=random.choice(["To Do", "In Progress", "In Review"]),
                            to_string=random.choice(["In Progress", "In Review", "Done"]),
                            changed_by_id=random.choice([u[0] for u in USERS]),
                            changed_by_name=random.choice([u[1] for u in USERS]),
                            changed_at=_rand_dt(start_date, end_date),
                        ))

    # Extraction run audit record
    async with get_db() as db:
        db.add(ExtractionRun(
            run_id=str(uuid4()),
            run_type="full",
            triggered_by="seed_script",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            completed_at=datetime.now(timezone.utc),
            status=RunStatus.SUCCESS,
            projects_processed=len(PROJECTS),
            issues_extracted=sum(issue_counts.values()),
            issues_updated=0,
            transitions_extracted=sum(issue_counts.values()) * 2,
            error_count=0,
            duration_seconds=300,
            jira_api_calls=sum(issue_counts.values()) + 50,
        ))

    print("Computing KPIs and risk scores...")
    writer = SnapshotWriter()
    today = date.today()

    for proj_key, raw_issues in all_issues.items():
        # Convert to IssueRecord
        records = []
        for d in raw_issues:
            records.append(IssueRecord(
                jira_key=d["jira_key"],
                project_key=d["project_key"],
                summary=d["summary"],
                issue_type=d["issue_type"],
                status=d["status"],
                status_category=d["status_category"],
                priority=d["priority"],
                assignee_id=d["assignee_id"],
                component_ids=json.loads(d["component_ids"]),
                fix_version_ids=json.loads(d["fix_version_ids"]),
                epic_key=d["epic_key"],
                created_date=d["created_date"],
                resolved_date=d["resolved_date"],
                updated_date=d["updated_date"],
                due_date=d["due_date"],
                age_days=d["age_days"],
                resolution_time_days=d["resolution_time_days"],
                cycle_time_days=d["cycle_time_days"],
                times_reopened=d["times_reopened"],
                is_overdue=d["is_overdue"],
                days_without_update=d["days_without_update"],
                current_status_age_days=d["current_status_age_days"],
                dq_missing_assignee=d["dq_missing_assignee"],
                dq_missing_priority=d["dq_missing_priority"],
                dq_missing_component=d["dq_missing_component"],
                dq_missing_fix_version=d["dq_missing_fix_version"],
                dq_missing_epic=d["dq_missing_epic"],
                dq_missing_due_date=d["dq_missing_due_date"],
                dq_closed_without_resolution=d["dq_closed_without_resolution"],
                story_points=d["story_points"],
            ))

        calc = KPICalculator(proj_key, records, as_of=today)
        kpis = calc.calculate_all()
        scorer = RiskScorer(kpis)
        risk = scorer.score()

        await writer.write_kpis(kpis)
        await writer.write_risk_score(risk)
        for pt in ["daily", "weekly", "monthly"]:
            await writer.write_snapshot(proj_key, today, pt, kpis, risk)

        print(f"  {proj_key}: {len(kpis.kpis)} KPIs, risk={risk.risk_level} ({risk.composite_score:.1f})")

    print("\n✅ Mock data seeded successfully!")
    print(f"   Projects: {len(PROJECTS)}")
    print(f"   Issues:   {sum(issue_counts.values())}")
    print(f"   Run: python -m uvicorn main:app --reload  (from backend/)")


if __name__ == "__main__":
    asyncio.run(seed())
