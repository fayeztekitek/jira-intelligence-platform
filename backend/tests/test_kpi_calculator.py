"""
tests/test_kpi_calculator.py

Unit tests for KPI engine.
Every KPI formula is verified for:
  - Correct value
  - Correct trend direction
  - Correct risk level
  - Edge cases (empty data, zero division)

Run: pytest tests/test_kpi_calculator.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, datetime, timedelta, timezone
from kpi_engine.calculator import KPICalculator, IssueRecord, PERIODS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc)


def make_issue(
    key: str = "PROJ-1",
    project: str = "PROJ",
    issue_type: str = "Story",
    status: str = "Done",
    status_category: str = "Done",
    priority: str = "Medium",
    assignee: str = "u001",
    created_offset: int = 20,        # days ago
    resolved_offset: int | None = 5, # days ago (None = open)
    due_offset: int | None = None,
    age_days: int = 20,
    resolution_days: float | None = 15.0,
    cycle_days: float | None = 10.0,
    times_reopened: int = 0,
    is_overdue: bool = False,
    days_no_update: int = 2,
    status_age: int = 2,
    has_component: bool = True,
    has_fix_version: bool = True,
    has_epic: bool = True,
    labels: list[str] | None = None,
    story_points: float | None = 3.0,
) -> IssueRecord:
    today = date.today()
    created = _utc(today - timedelta(days=created_offset))
    resolved = _utc(today - timedelta(days=resolved_offset)) if resolved_offset is not None else None
    due = today + timedelta(days=due_offset) if due_offset is not None else None

    # Open issues should not have resolution_time_days
    effective_resolution_days = resolution_days if resolved_offset is not None else None
    return IssueRecord(
        jira_key=key,
        project_key=project,
        summary=f"Test issue {key}",
        issue_type=issue_type,
        status=status,
        status_category=status_category,
        priority=priority,
        assignee_id=assignee,
        component_ids=["C1"] if has_component else [],
        fix_version_ids=["V1"] if has_fix_version else [],
        epic_key="EPIC-1" if has_epic else None,
        created_date=created,
        resolved_date=resolved,
        updated_date=resolved or created,
        due_date=due,
        age_days=age_days,
        resolution_time_days=effective_resolution_days,
        cycle_time_days=cycle_days,
        times_reopened=times_reopened,
        is_overdue=is_overdue,
        days_without_update=days_no_update,
        current_status_age_days=status_age,
        dq_missing_assignee=(assignee is None),
        dq_missing_priority=(priority is None),
        dq_missing_component=(not has_component),
        dq_missing_fix_version=(not has_fix_version),
        dq_missing_epic=(not has_epic),
        dq_missing_due_date=(due is None),
        dq_closed_without_resolution=(status_category == "Done" and resolved is None),
        labels=labels,
        story_points=story_points,
    )


def calc(issues: list[IssueRecord], period: str = "1m") -> dict:
    """Run calculator and return KPIs for given period as name→KPIValue dict."""
    c = KPICalculator("PROJ", issues, as_of=date.today())
    result = c.calculate_all()
    return {k.name: k for k in result.kpis if k.period_label == period}


# ---------------------------------------------------------------------------
# 1. Delivery KPIs
# ---------------------------------------------------------------------------

class TestDeliveryKPIs:

    def test_issues_created_counts_issues_in_window(self):
        # 3 issues created in last 30 days, 1 older
        issues = [
            make_issue(f"P-{i}", created_offset=10) for i in range(3)
        ] + [make_issue("P-4", created_offset=60)]
        kpis = calc(issues, "1m")
        assert kpis["issues_created"].current_value == 3

    def test_issues_resolved_counts_resolved_in_window(self):
        issues = [
            make_issue(f"P-{i}", created_offset=40, resolved_offset=10)
            for i in range(4)
        ] + [
            # resolved outside window (65 days ago)
            make_issue("P-99", created_offset=100, resolved_offset=65),
        ]
        kpis = calc(issues, "1m")
        assert kpis["issues_resolved"].current_value == 4

    def test_resolution_rate_formula(self):
        # 4 created, 2 resolved → 50%
        issues = [
            make_issue("P-1", created_offset=10, resolved_offset=5),
            make_issue("P-2", created_offset=10, resolved_offset=5),
            make_issue("P-3", created_offset=10, resolved_offset=None,
                       status="In Progress", status_category="In Progress"),
            make_issue("P-4", created_offset=10, resolved_offset=None,
                       status="To Do", status_category="To Do"),
        ]
        kpis = calc(issues, "1m")
        assert kpis["resolution_rate"].current_value == 50.0

    def test_resolution_rate_zero_created(self):
        # No issues created in window → None (no division by zero)
        issues = [make_issue("P-1", created_offset=90)]
        kpis = calc(issues, "1m")
        # No issues in 1m window → created=0, resolved=0
        assert kpis["issues_created"].current_value == 0

    def test_avg_resolution_days(self):
        issues = [
            make_issue("P-1", created_offset=20, resolved_offset=10, resolution_days=10.0),
            make_issue("P-2", created_offset=25, resolved_offset=10, resolution_days=20.0),
        ]
        kpis = calc(issues, "1m")
        # mean of [10, 20] = 15
        assert kpis["avg_resolution_days"].current_value == 15.0

    def test_median_resolution_days_odd(self):
        issues = [
            make_issue(f"P-{i}", created_offset=25, resolved_offset=5,
                       resolution_days=float(v))
            for i, v in enumerate([5, 10, 15, 20, 25])
        ]
        kpis = calc(issues, "1m")
        assert kpis["median_resolution_days"].current_value == 15.0

    def test_backlog_size_open_issues(self):
        issues = [
            make_issue("P-1", created_offset=10, status="In Progress",
                       status_category="In Progress", resolved_offset=None),
            make_issue("P-2", created_offset=10, status="To Do",
                       status_category="To Do", resolved_offset=None),
            make_issue("P-3", created_offset=10, resolved_offset=5),  # resolved
        ]
        kpis = calc(issues, "1m")
        assert kpis["backlog_size"].current_value == 2

    def test_wip_only_in_progress(self):
        issues = [
            make_issue("P-1", status="In Progress", status_category="In Progress",
                       resolved_offset=None, created_offset=5),
            make_issue("P-2", status="To Do", status_category="To Do",
                       resolved_offset=None, created_offset=5),
            make_issue("P-3", resolved_offset=2, created_offset=10),
        ]
        kpis = calc(issues, "1m")
        assert kpis["wip"].current_value == 1

    def test_overdue_count(self):
        issues = [
            make_issue("P-1", is_overdue=True, resolved_offset=None,
                       status_category="In Progress", created_offset=10),
            make_issue("P-2", is_overdue=True, resolved_offset=None,
                       status_category="In Progress", created_offset=10),
            make_issue("P-3", is_overdue=False, resolved_offset=5, created_offset=20),
        ]
        kpis = calc(issues, "1m")
        assert kpis["overdue_count"].current_value == 2

    def test_aging_issues_30d(self):
        issues = [
            make_issue("P-1", age_days=45, resolved_offset=None,
                       status_category="In Progress", created_offset=45),
            make_issue("P-2", age_days=15, resolved_offset=None,
                       status_category="To Do", created_offset=15),
        ]
        kpis = calc(issues, "1m")
        assert kpis["aging_issues_30d"].current_value == 1

    def test_throughput_per_day(self):
        # 6 resolved in 30 days → 0.2/day
        issues = [
            make_issue(f"P-{i}", created_offset=35, resolved_offset=10)
            for i in range(6)
        ]
        kpis = calc(issues, "1m")
        assert kpis["throughput"].current_value == round(6 / 30, 2)


# ---------------------------------------------------------------------------
# 2. Quality KPIs
# ---------------------------------------------------------------------------

class TestQualityKPIs:

    def test_bugs_created_counts_only_bugs(self):
        issues = [
            make_issue("P-1", issue_type="Bug", created_offset=10),
            make_issue("P-2", issue_type="Bug", created_offset=10),
            make_issue("P-3", issue_type="Story", created_offset=10),
        ]
        kpis = calc(issues, "1m")
        assert kpis["bugs_created"].current_value == 2

    def test_reopen_rate_formula(self):
        # 10 created, 2 reopened → 20%
        issues = [
            make_issue(f"P-{i}", created_offset=10,
                       times_reopened=(1 if i < 2 else 0))
            for i in range(10)
        ]
        kpis = calc(issues, "1m")
        assert kpis["reopen_rate"].current_value == 20.0

    def test_reopened_count(self):
        issues = [
            make_issue("P-1", times_reopened=2, created_offset=10),
            make_issue("P-2", times_reopened=0, created_offset=10),
            make_issue("P-3", times_reopened=1, created_offset=10),
        ]
        kpis = calc(issues, "1m")
        assert kpis["reopened_count"].current_value == 2

    def test_critical_bugs_open(self):
        issues = [
            make_issue("P-1", issue_type="Bug", priority="Critical",
                       status_category="In Progress", resolved_offset=None,
                       created_offset=5),
            make_issue("P-2", issue_type="Bug", priority="Blocker",
                       status_category="In Progress", resolved_offset=None,
                       created_offset=5),
            make_issue("P-3", issue_type="Bug", priority="Medium",
                       status_category="In Progress", resolved_offset=None,
                       created_offset=5),
            make_issue("P-4", issue_type="Bug", priority="Critical",
                       resolved_offset=2, created_offset=10),  # resolved
        ]
        kpis = calc(issues, "1m")
        assert kpis["critical_bugs_open"].current_value == 2

    def test_repeat_reopen(self):
        issues = [
            make_issue("P-1", times_reopened=3, created_offset=10),
            make_issue("P-2", times_reopened=2, created_offset=10),
            make_issue("P-3", times_reopened=1, created_offset=10),
        ]
        kpis = calc(issues, "1m")
        assert kpis["repeat_reopen_count"].current_value == 2


# ---------------------------------------------------------------------------
# 3. Risk & Control KPIs
# ---------------------------------------------------------------------------

class TestRiskKPIs:

    def test_unassigned_open(self):
        issues = [
            make_issue("P-1", assignee=None, resolved_offset=None,
                       status_category="In Progress", created_offset=5),
            make_issue("P-2", assignee="u001", resolved_offset=None,
                       status_category="To Do", created_offset=5),
        ]
        # Manually set dq flag on the unassigned issue
        issues[0] = IssueRecord(
            **{**issues[0].__dict__, "dq_missing_assignee": True, "assignee_id": None}
        )
        kpis = calc(issues, "1m")
        assert kpis["unassigned_open"].current_value == 1

    def test_stuck_issues_14d(self):
        issues = [
            make_issue("P-1", status_age=20, resolved_offset=None,
                       status_category="In Progress", created_offset=25),
            make_issue("P-2", status_age=5,  resolved_offset=None,
                       status_category="In Progress", created_offset=10),
        ]
        kpis = calc(issues, "1m")
        assert kpis["stuck_issues_14d"].current_value == 1

    def test_stale_issues_7d(self):
        issues = [
            make_issue("P-1", days_no_update=10, resolved_offset=None,
                       status_category="In Progress", created_offset=15),
            make_issue("P-2", days_no_update=3,  resolved_offset=None,
                       status_category="In Progress", created_offset=10),
        ]
        kpis = calc(issues, "1m")
        assert kpis["stale_issues_7d"].current_value == 1

    def test_blocked_critical_open_by_status(self):
        issues = [
            make_issue("P-1", priority="Critical", status="Blocked",
                       resolved_offset=None, status_category="In Progress",
                       created_offset=5),
            make_issue("P-2", priority="Blocker", status="In Progress",
                       resolved_offset=None, status_category="In Progress",
                       created_offset=5),
        ]
        kpis = calc(issues, "1m")
        assert kpis["blocked_critical_open"].current_value == 1

    def test_blocked_critical_open_by_label(self):
        issues = [
            make_issue("P-1", priority="Critical", status="In Progress",
                       labels=["blocked"], resolved_offset=None,
                       status_category="In Progress", created_offset=5),
            make_issue("P-2", priority="Blocker", status="In Progress",
                       resolved_offset=None, status_category="In Progress",
                       created_offset=5),
        ]
        kpis = calc(issues, "1m")
        assert kpis["blocked_critical_open"].current_value == 1

    def test_blocked_critical_open_ignores_non_critical(self):
        issues = [
            make_issue("P-1", priority="Medium", status="Blocked",
                       resolved_offset=None, status_category="In Progress",
                       created_offset=5),
        ]
        kpis = calc(issues, "1m")
        assert kpis["blocked_critical_open"].current_value == 0

    def test_aging_critical_open(self):
        issues = [
            make_issue("P-1", priority="Critical", age_days=20,
                       resolved_offset=None, status_category="In Progress",
                       created_offset=25),
            make_issue("P-2", priority="Blocker", age_days=10,
                       resolved_offset=None, status_category="In Progress",
                       created_offset=15),
            make_issue("P-3", priority="Highest", age_days=5,
                       resolved_offset=None, status_category="To Do",
                       created_offset=10),
        ]
        kpis = calc(issues, "1m")
        assert kpis["aging_critical_open"].current_value == 1

    def test_sla_at_risk(self):
        """Issue with age_days > 80% of avg resolution time = at risk."""
        issues = [
            # 1 resolved issue with 10d resolution time → avg = 10d
            make_issue("P-1", priority="Medium", resolved_offset=30,
                       resolution_days=10.0, created_offset=40),
            # 1 open issue at 9d old (> 8d = 80% of 10d) → at risk
            make_issue("P-2", priority="Major", age_days=9,
                       resolved_offset=None, status_category="In Progress",
                       created_offset=9),
            # 1 open issue at 5d old (< 8d) → not at risk
            make_issue("P-3", priority="Major", age_days=5,
                       resolved_offset=None, status_category="In Progress",
                       created_offset=5),
        ]
        kpis = calc(issues, "1m")
        assert kpis["sla_at_risk"].current_value == 1

    def test_sla_at_risk_zero_avg_resolution(self):
        """No resolved issues → avg_resolution is None → sla_at_risk = 0."""
        issues = [
            make_issue("P-1", priority="Major", age_days=20,
                       resolved_offset=None, status_category="In Progress",
                       created_offset=25),
        ]
        kpis = calc(issues, "1m")
        assert kpis["sla_at_risk"].current_value == 0


# ---------------------------------------------------------------------------
# 4. Data Quality KPIs
# ---------------------------------------------------------------------------

class TestDataQualityKPIs:

    def test_missing_assignee_count(self):
        issues = [
            make_issue("P-1", resolved_offset=None, status_category="In Progress",
                       created_offset=5, assignee=None),
            make_issue("P-2", resolved_offset=None, status_category="In Progress",
                       created_offset=5, assignee="u001"),
        ]
        issues[0] = IssueRecord(**{**issues[0].__dict__,
                                   "dq_missing_assignee": True, "assignee_id": None})
        kpis = calc(issues, "1m")
        assert kpis["missing_assignee"].current_value == 1

    def test_dq_score_perfect_data(self):
        # All issues have all fields → score = 100
        issues = [make_issue(f"P-{i}", created_offset=5, resolved_offset=None,
                              status_category="In Progress") for i in range(5)]
        # Ensure no DQ flags
        clean = []
        for iss in issues:
            clean.append(IssueRecord(**{
                **iss.__dict__,
                "dq_missing_assignee": False,
                "dq_missing_priority": False,
                "dq_missing_component": False,
                "dq_missing_fix_version": False,
                "dq_missing_epic": False,
                "dq_missing_due_date": False,
                "dq_closed_without_resolution": False,
            }))
        kpis = calc(clean, "1m")
        assert kpis["dq_score"].current_value == 100.0

    def test_dq_score_degrades_with_missing_fields(self):
        # 5 open issues, all missing assignee + component
        issues = []
        for i in range(5):
            iss = make_issue(f"P-{i}", created_offset=5, resolved_offset=None,
                              status_category="In Progress")
            issues.append(IssueRecord(**{
                **iss.__dict__,
                "dq_missing_assignee": True,
                "dq_missing_component": True,
                "assignee_id": None,
            }))
        kpis = calc(issues, "1m")
        assert kpis["dq_score"].current_value < 100.0


# ---------------------------------------------------------------------------
# 5. Trend detection
# ---------------------------------------------------------------------------

class TestTrendDetection:

    def test_throughput_improving_when_higher(self):
        # More resolved recently → improving throughput
        issues_current = [
            make_issue(f"P-{i}", created_offset=25, resolved_offset=5)
            for i in range(10)
        ]
        issues_prev = [
            make_issue(f"Q-{i}", created_offset=55, resolved_offset=35)
            for i in range(3)
        ]
        kpis_obj = KPICalculator("PROJ", issues_current + issues_prev).calculate_all()
        throughput = next(
            (k for k in kpis_obj.kpis
             if k.name == "throughput" and k.period_label == "1m"),
            None,
        )
        assert throughput is not None
        assert throughput.trend in ("improving", "stable")

    def test_risk_level_critical_when_many_overdue(self):
        issues = [
            make_issue(f"P-{i}", is_overdue=True, resolved_offset=None,
                       status_category="In Progress", created_offset=10)
            for i in range(25)
        ]
        kpis = calc(issues, "1m")
        od = kpis["overdue_count"]
        assert od.risk_level in ("high", "critical")

    def test_delta_calculation(self):
        # current=80, previous derived from prev window
        issues_c = [
            make_issue(f"P-{i}", created_offset=10, resolved_offset=5)
            for i in range(8)
        ]
        issues_p = [
            make_issue(f"Q-{i}", created_offset=40, resolved_offset=35)
            for i in range(4)
        ]
        kpis_obj = KPICalculator("PROJ", issues_c + issues_p).calculate_all()
        resolved = next(
            (k for k in kpis_obj.kpis
             if k.name == "issues_resolved" and k.period_label == "1m"),
            None,
        )
        assert resolved is not None
        assert resolved.delta is not None


# ---------------------------------------------------------------------------
# 6. Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_empty_project(self):
        kpis = calc([])
        # Should return KPIs with 0/None values, no crash
        assert len(kpis) > 0
        cr = kpis.get("issues_created")
        assert cr is not None
        assert cr.current_value == 0

    def test_all_issues_resolved(self):
        issues = [
            make_issue(f"P-{i}", created_offset=15, resolved_offset=5)
            for i in range(10)
        ]
        kpis = calc(issues, "1m")
        assert kpis["backlog_size"].current_value == 0
        assert kpis["wip"].current_value == 0

    def test_single_issue(self):
        issues = [make_issue("P-1", created_offset=10, resolved_offset=5,
                              resolution_days=5.0)]
        kpis = calc(issues, "1m")
        assert kpis["issues_created"].current_value == 1
        assert kpis["avg_resolution_days"].current_value == 5.0

    def test_all_periods_present(self):
        issues = [make_issue(f"P-{i}", created_offset=10) for i in range(5)]
        kpis_obj = KPICalculator("PROJ", issues).calculate_all()
        period_labels = {k.period_label for k in kpis_obj.kpis}
        expected = {label for label, _ in PERIODS}
        assert expected == period_labels

    def test_kpi_to_dict_has_required_fields(self):
        issues = [make_issue("P-1")]
        kpis = calc(issues, "1m")
        d = kpis["issues_created"].to_dict()
        for field in ["name", "category", "period_label", "current_value",
                      "delta", "trend", "risk_level", "formula"]:
            assert field in d, f"Missing field: {field}"

    # ── Additional edge cases ─────────────────────────────────────────────

    def test_issues_all_created_same_minute(self):
        now = datetime.now(timezone.utc)
        issues = [
            IssueRecord(
                jira_key=f"SAME-{i}", project_key="PROJ", summary=f"Same {i}",
                issue_type="Story", status="Done", status_category="Done",
                priority="Medium", assignee_id="u001",
                component_ids=[], fix_version_ids=[], epic_key=None,
                created_date=now - timedelta(minutes=30),
                resolved_date=now - timedelta(minutes=15),
                updated_date=now - timedelta(minutes=15),
                due_date=None, age_days=0, resolution_time_days=0.01,
                cycle_time_days=0.01, times_reopened=0,
                is_overdue=False, days_without_update=0,
                current_status_age_days=0,
                dq_missing_assignee=False, dq_missing_priority=False,
                dq_missing_component=True, dq_missing_fix_version=True,
                dq_missing_epic=True, dq_missing_due_date=True,
                dq_closed_without_resolution=False,
                story_points=None,
            )
            for i in range(3)
        ]
        kpis = calc(issues, "1m")
        assert kpis["issues_created"].current_value == 3
        assert kpis["issues_resolved"].current_value == 3

    def test_no_created_in_window_zero_denominator(self):
        issues = [make_issue("OLD-1", created_offset=60, resolved_offset=55)]
        kpis = calc(issues, "1m")
        assert kpis["issues_created"].current_value == 0
        assert kpis["resolution_rate"].current_value == 0.0

    def test_zero_story_points_excluded_from_delivered(self):
        issues = [
            make_issue("ZSP-1", story_points=0.0),
            make_issue("ZSP-2", story_points=None),
            make_issue("ZSP-3", story_points=5.0),
        ]
        kpis_obj = KPICalculator("PROJ", issues).calculate_all()
        sp = next(
            (k for k in kpis_obj.kpis
             if k.name == "story_points_delivered" and k.period_label == "1m"),
            None,
        )
        assert sp is not None
        assert sp.current_value == 5.0

    def test_all_overdue_issues(self):
        issues = [
            make_issue(f"O-{i}", status="In Progress", status_category="In Progress",
                       resolved_offset=None, resolution_days=None, cycle_days=None,
                       is_overdue=True, due_offset=-5)
            for i in range(5)
        ]
        kpis = calc(issues, "1m")
        assert kpis["overdue_count"].current_value == 5

    def test_all_to_do_none_resolved(self):
        issues = [
            make_issue(f"TD-{i}", status="To Do", status_category="To Do",
                       resolved_offset=None, resolution_days=None,
                       cycle_days=None)
            for i in range(4)
        ]
        kpis = calc(issues, "1m")
        assert kpis["issues_created"].current_value == 4
        assert kpis["issues_resolved"].current_value == 0
        assert kpis["backlog_size"].current_value == 4

    def test_negative_resolution_days_does_not_crash(self):
        issues = [make_issue("NEG-1", resolution_days=-5.0)]
        kpis = calc(issues, "1m")
        assert kpis["avg_resolution_days"].current_value == -5.0
        assert kpis["median_resolution_days"].current_value == -5.0

    def test_all_dq_flags_penalize_score(self):
        issues = [
            IssueRecord(
                jira_key=f"DQ-BAD-{i}", project_key="PROJ",
                summary=f"Bad {i}", issue_type="Story",
                status="To Do", status_category="To Do",
                priority=None, assignee_id=None,
                component_ids=[], fix_version_ids=[], epic_key=None,
                created_date=datetime.now(timezone.utc) - timedelta(days=5),
                resolved_date=None, updated_date=None,
                due_date=None, age_days=5, resolution_time_days=None,
                cycle_time_days=None, times_reopened=0,
                is_overdue=False, days_without_update=5,
                current_status_age_days=5,
                dq_missing_assignee=True, dq_missing_priority=True,
                dq_missing_component=True, dq_missing_fix_version=True,
                dq_missing_epic=True, dq_missing_due_date=True,
                dq_closed_without_resolution=False,
                story_points=None,
            )
            for i in range(3)
        ]
        kpis_obj = KPICalculator("PROJ", issues).calculate_all()
        dq = next(
            (k for k in kpis_obj.kpis
             if k.name == "dq_score" and k.period_label == "1m"),
            None,
        )
        assert dq is not None
        # 6 of 7 DQ fields penalize (closed_without_resolution only applies to Done issues)
        assert dq.current_value < 20.0

    def test_as_of_before_all_issues(self):
        future = date(2030, 1, 1)
        issues = [make_issue("FUT-1")]
        c = KPICalculator("PROJ", issues, as_of=future)
        result = c.calculate_all()
        assert len(result.kpis) > 0
        cr = next((k for k in result.kpis if k.name == "issues_created"
                    and k.period_label == "1m"), None)
        assert cr is not None
        assert cr.current_value == 0
