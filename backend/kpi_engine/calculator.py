"""
kpi_engine/calculator.py

Complete KPI calculation engine. Every KPI is:
  - Traceable: stores formula + contributing issue count
  - Auditable: linked to extraction_run_id
  - Comparable: computed for all 10 time periods
  - Actionable: includes threshold, interpretation, recommended action

Categories:
  1. Delivery Performance
  2. Quality & Regression
  3. Risk & Control
  4. Data Quality
  5. Product/Project Governance
  6. Team & Ownership
  7. Trend & Historical (cross-cutting)
"""
from __future__ import annotations

import bisect
import json
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Period definitions
# ---------------------------------------------------------------------------

PERIODS: list[tuple[str, int]] = [
    ("1d",  1),
    ("1w",  7),
    ("2w",  14),
    ("3w",  21),
    ("4w",  28),
    ("1m",  30),
    ("3m",  90),
    ("6m",  180),
    ("9m",  270),
    ("1y",  365),
]

# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------

@dataclass
class KPIValue:
    name: str
    category: str
    period_label: str
    current_value: float | None
    previous_value: float | None
    unit: str = ""                  # count | % | days | score
    formula: str = ""
    interpretation: str = ""
    threshold_low: float | None = None
    threshold_high: float | None = None
    recommended_action: str = ""
    contributing_count: int = 0     # number of issues that drove this KPI

    @property
    def delta(self) -> float | None:
        if self.current_value is None or self.previous_value is None:
            return None
        return round(self.current_value - self.previous_value, 3)

    @property
    def delta_pct(self) -> float | None:
        if self.delta is None or self.previous_value in (None, 0):
            return None
        return round((self.delta / self.previous_value) * 100, 1)

    @property
    def trend(self) -> str:
        d = self.delta
        if d is None:
            return "unknown"
        # For most KPIs, lower is better (bugs, aging, risk).
        # For throughput/velocity, higher is better.
        higher_is_better = self.name in {
            "throughput", "resolution_rate", "bug_resolution_rate",
            "sprint_velocity", "sprint_predictability", "dq_score",
        }
        thr = 0.02 * (abs(self.previous_value) if self.previous_value else 1)
        if abs(d) <= thr:
            return "stable"
        improving = (d > 0) if higher_is_better else (d < 0)
        return "improving" if improving else "degrading"

    @property
    def risk_level(self) -> str:
        if self.current_value is None:
            return "unknown"
        v = self.current_value
        hi = self.threshold_high
        lo = self.threshold_low
        if hi is not None and v >= hi:
            return "critical"
        if hi is not None and v >= hi * 0.75:
            return "high"
        if lo is not None and v >= lo:
            return "medium"
        return "low"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "category": self.category,
            "period_label": self.period_label,
            "current_value": self.current_value,
            "previous_value": self.previous_value,
            "delta": self.delta,
            "delta_pct": self.delta_pct,
            "trend": self.trend,
            "risk_level": self.risk_level,
            "unit": self.unit,
            "formula": self.formula,
            "interpretation": self.interpretation,
            "recommended_action": self.recommended_action,
            "contributing_count": self.contributing_count,
        }


@dataclass
class ProjectKPIs:
    project_key: str
    calculated_at: date
    kpis: list[KPIValue] = field(default_factory=list)

    def by_name(self, name: str, period: str = "1m") -> KPIValue | None:
        for k in self.kpis:
            if k.name == name and k.period_label == period:
                return k
        return None

    def to_dict(self) -> dict:
        return {
            "project_key": self.project_key,
            "calculated_at": self.calculated_at.isoformat(),
            "kpis": [k.to_dict() for k in self.kpis],
        }


# ---------------------------------------------------------------------------
# Issue data container (dict-based for performance — no ORM overhead)
# ---------------------------------------------------------------------------

@dataclass
class IssueRecord:
    jira_key: str
    project_key: str
    summary: str
    issue_type: str
    status: str
    status_category: str        # "To Do" | "In Progress" | "Done"
    priority: str | None
    assignee_id: str | None
    component_ids: list[str]
    fix_version_ids: list[str]
    epic_key: str | None
    created_date: datetime | None
    resolved_date: datetime | None
    updated_date: datetime | None
    due_date: date | None
    age_days: int | None
    resolution_time_days: float | None
    cycle_time_days: float | None
    times_reopened: int
    is_overdue: bool
    days_without_update: int | None
    current_status_age_days: int | None
    dq_missing_assignee: bool
    dq_missing_priority: bool
    dq_missing_component: bool
    dq_missing_fix_version: bool
    dq_missing_epic: bool
    dq_missing_due_date: bool
    dq_closed_without_resolution: bool
    story_points: float | None
    labels: list[str] | None = None
    sprint_ids: list[int] | None = None


# ---------------------------------------------------------------------------
# Core calculator
# ---------------------------------------------------------------------------

class KPICalculator:
    """
    Calculates all KPIs for a given project from a list of IssueRecords.

    Usage:
        calc = KPICalculator(project_key, issues, as_of_date)
        result = calc.calculate_all()
    """

    def __init__(
        self,
        project_key: str,
        issues: list[IssueRecord],
        as_of: date | None = None,
    ):
        self.project_key = project_key
        self.issues = issues
        self.as_of = as_of or date.today()
        self._result = ProjectKPIs(project_key=project_key, calculated_at=self.as_of)

        # Pre-sort for binary search windowing (O(n log n) once vs O(n×p) for each period)
        self._sorted_by_created = sorted(
            [i for i in issues if i.created_date],
            key=lambda x: x.created_date.date() if hasattr(x.created_date, "date") else x.created_date,
        )
        self._sorted_by_resolved = sorted(
            [i for i in issues if i.resolved_date],
            key=lambda x: x.resolved_date.date() if hasattr(x.resolved_date, "date") else x.resolved_date,
        )
        self._created_dates = [
            i.created_date.date() if hasattr(i.created_date, "date") else i.created_date
            for i in self._sorted_by_created
        ]
        self._resolved_dates = [
            i.resolved_date.date() if hasattr(i.resolved_date, "date") else i.resolved_date
            for i in self._sorted_by_resolved
        ]

    def calculate_all(self) -> ProjectKPIs:
        for label, days in PERIODS:
            window_end = self.as_of
            window_start = self.as_of - timedelta(days=days)
            prev_start = window_start - timedelta(days=days)

            cur = self._window(window_start, window_end)
            prev = self._window(prev_start, window_start)

            self._delivery(label, cur, prev)
            self._quality(label, cur, prev)
            self._risk_control(label, cur, prev)
            self._data_quality(label, cur, prev)
            self._governance(label, cur, prev)
            self._team(label, cur, prev)

        logger.info("kpis_calculated", project=self.project_key,
                    kpi_count=len(self._result.kpis))
        return self._result

    # -----------------------------------------------------------------------
    # Optimized window helpers (binary search)
    # -----------------------------------------------------------------------

    def _bisect_range(self, dates: list, sorted_items: list, start: date, end: date) -> list:
        """Return items where the corresponding date is in [start, end)."""
        lo = bisect.bisect_left(dates, start)
        hi = bisect.bisect_left(dates, end)
        return sorted_items[lo:hi]

    def _window(self, start: date, end: date) -> list[IssueRecord]:
        """Issues created within [start, end) — O(log n + k)."""
        return self._bisect_range(self._created_dates, self._sorted_by_created, start, end)

    def _resolved_in(self, start: date, end: date) -> list[IssueRecord]:
        """Issues resolved within [start, end) — O(log n + k)."""
        return self._bisect_range(self._resolved_dates, self._sorted_by_resolved, start, end)

    def _open_as_of(self, as_of_date: date) -> list[IssueRecord]:
        """Issues that were open (created before and not resolved before) as of as_of_date."""
        # Use binary search to get only issues created before as_of_date
        lo = bisect.bisect_left(self._created_dates, as_of_date)
        candidates = self._sorted_by_created[:lo]

        result = []
        for i in candidates:
            if i.resolved_date is None:
                result.append(i)
            else:
                rd = i.resolved_date.date() if hasattr(i.resolved_date, "date") else i.resolved_date
                if rd >= as_of_date:
                    result.append(i)
        return result

    @staticmethod
    def _safe_pct(num: int | float, den: int | float) -> float | None:
        if den == 0:
            return None
        return round((num / den) * 100, 1)

    @staticmethod
    def _median(values: list[float]) -> float | None:
        clean = [v for v in values if v is not None]
        if not clean:
            return None
        return round(statistics.median(clean), 1)

    @staticmethod
    def _mean(values: list[float]) -> float | None:
        clean = [v for v in values if v is not None]
        if not clean:
            return None
        return round(statistics.mean(clean), 1)

    def _add(self, kpi: KPIValue) -> None:
        self._result.kpis.append(kpi)

    # -----------------------------------------------------------------------
    # 1. Delivery Performance
    # -----------------------------------------------------------------------

    def _delivery(self, label: str, cur: list, prev: list) -> None:
        days = next(d for l, d in PERIODS if l == label)
        wend = self.as_of
        wstart = self.as_of - timedelta(days=days)
        pstart = wstart - timedelta(days=days)

        cur_created = len(cur)
        prev_created = len(prev)
        cur_resolved = self._resolved_in(wstart, wend)
        prev_resolved = self._resolved_in(pstart, wstart)

        cur_open = self._open_as_of(wend)
        prev_open = self._open_as_of(wstart)

        # --- Created ---
        self._add(KPIValue(
            name="issues_created",
            category="delivery",
            period_label=label,
            current_value=cur_created,
            previous_value=prev_created,
            unit="count",
            formula="COUNT(issues WHERE created_date IN period)",
            interpretation="Volume of new work entering the backlog.",
            threshold_low=None,
            threshold_high=None,
            recommended_action="Monitor for sudden spikes indicating scope creep.",
            contributing_count=cur_created,
        ))

        # --- Resolved ---
        self._add(KPIValue(
            name="issues_resolved",
            category="delivery",
            period_label=label,
            current_value=len(cur_resolved),
            previous_value=len(prev_resolved),
            unit="count",
            formula="COUNT(issues WHERE resolved_date IN period)",
            interpretation="Volume of work completed in the period.",
            contributing_count=len(cur_resolved),
        ))

        # --- Resolution rate ---
        self._add(KPIValue(
            name="resolution_rate",
            category="delivery",
            period_label=label,
            current_value=self._safe_pct(len(cur_resolved), cur_created or 1),
            previous_value=self._safe_pct(len(prev_resolved), prev_created or 1),
            unit="%",
            formula="resolved_in_period / created_in_period × 100",
            interpretation="Ratio of issues closed vs opened. <80% = backlog growing.",
            threshold_low=80.0,
            threshold_high=None,
            recommended_action="Resolution rate below 80%: review capacity and blockers.",
        ))

        # --- Avg resolution time ---
        cur_res_times = [i.resolution_time_days for i in cur_resolved if i.resolution_time_days is not None]
        prev_res_times = [i.resolution_time_days for i in prev_resolved if i.resolution_time_days is not None]
        self._add(KPIValue(
            name="avg_resolution_days",
            category="delivery",
            period_label=label,
            current_value=self._mean(cur_res_times),
            previous_value=self._mean(prev_res_times),
            unit="days",
            formula="MEAN(resolved_date - created_date) for resolved issues in period",
            interpretation="Average end-to-end resolution time. Increasing trend = bottleneck.",
            threshold_low=14.0,
            threshold_high=30.0,
            recommended_action="Resolution time >30d: identify stuck issues and escalate.",
            contributing_count=len(cur_res_times),
        ))

        # --- Median resolution time ---
        self._add(KPIValue(
            name="median_resolution_days",
            category="delivery",
            period_label=label,
            current_value=self._median(cur_res_times),
            previous_value=self._median(prev_res_times),
            unit="days",
            formula="MEDIAN(resolved_date - created_date) for resolved issues in period",
            interpretation="Median is more robust than mean for skewed distributions.",
            contributing_count=len(cur_res_times),
        ))

        # --- Throughput (resolved per day) ---
        cur_tp = round(len(cur_resolved) / days, 2) if days > 0 else None
        prev_tp = round(len(prev_resolved) / days, 2) if days > 0 else None
        self._add(KPIValue(
            name="throughput",
            category="delivery",
            period_label=label,
            current_value=cur_tp,
            previous_value=prev_tp,
            unit="issues/day",
            formula="resolved_in_period / period_days",
            interpretation="Daily delivery rate. Declining throughput signals capacity issues.",
            contributing_count=len(cur_resolved),
        ))

        # --- Backlog size ---
        self._add(KPIValue(
            name="backlog_size",
            category="delivery",
            period_label=label,
            current_value=len(cur_open),
            previous_value=len(prev_open),
            unit="count",
            formula="COUNT(open issues as of period end date)",
            interpretation="Total open work. Growing backlog = demand exceeds capacity.",
            threshold_low=100,
            threshold_high=500,
            recommended_action="Backlog >500: prioritise triage and close stale items.",
            contributing_count=len(cur_open),
        ))

        # --- WIP (In Progress) ---
        cur_wip = [i for i in cur_open if i.status_category == "In Progress"]
        prev_wip = [i for i in prev_open if i.status_category == "In Progress"]
        self._add(KPIValue(
            name="wip",
            category="delivery",
            period_label=label,
            current_value=len(cur_wip),
            previous_value=len(prev_wip),
            unit="count",
            formula="COUNT(issues WHERE status_category='In Progress' as of period end)",
            interpretation="Active work in flight. High WIP reduces throughput (Little's Law).",
            threshold_low=20,
            threshold_high=50,
            recommended_action="WIP >50: enforce WIP limits, complete before starting new work.",
            contributing_count=len(cur_wip),
        ))

        # --- Overdue issues ---
        cur_overdue = [i for i in cur_open if i.is_overdue]
        prev_overdue = [i for i in prev_open if i.is_overdue]
        self._add(KPIValue(
            name="overdue_count",
            category="delivery",
            period_label=label,
            current_value=len(cur_overdue),
            previous_value=len(prev_overdue),
            unit="count",
            formula="COUNT(open issues WHERE due_date < today)",
            interpretation="Issues past due date. Each is a broken commitment.",
            threshold_low=5,
            threshold_high=20,
            recommended_action="Review overdue issues with assignees; re-plan or escalate.",
            contributing_count=len(cur_overdue),
        ))

        # --- Aging open issues (>30 days open) ---
        aging = [i for i in cur_open if (i.age_days or 0) > 30]
        prev_aging = [i for i in prev_open if (i.age_days or 0) > 30]
        self._add(KPIValue(
            name="aging_issues_30d",
            category="delivery",
            period_label=label,
            current_value=len(aging),
            previous_value=len(prev_aging),
            unit="count",
            formula="COUNT(open issues WHERE age_days > 30)",
            interpretation="Long-lived open issues signal abandonment or blocking.",
            threshold_low=10,
            threshold_high=50,
            recommended_action="Triage aging issues: close stale, re-assign blocked.",
            contributing_count=len(aging),
        ))

        # --- Cycle time (In Progress → Done) ---
        cur_cycle = [i.cycle_time_days for i in cur_resolved if i.cycle_time_days is not None]
        prev_cycle = [i.cycle_time_days for i in prev_resolved if i.cycle_time_days is not None]
        self._add(KPIValue(
            name="avg_cycle_time_days",
            category="delivery",
            period_label=label,
            current_value=self._mean(cur_cycle),
            previous_value=self._mean(prev_cycle),
            unit="days",
            formula="MEAN(first_in_progress_date → resolved_date)",
            interpretation="Time from work start to delivery. Measures execution efficiency.",
            threshold_low=7.0,
            threshold_high=21.0,
            recommended_action="Cycle time >21d: identify handoff delays and blockers.",
            contributing_count=len(cur_cycle),
        ))

    # -----------------------------------------------------------------------
    # 2. Quality & Regression
    # -----------------------------------------------------------------------

    def _quality(self, label: str, cur: list, prev: list) -> None:
        days = next(d for l, d in PERIODS if l == label)
        wend = self.as_of
        wstart = self.as_of - timedelta(days=days)
        pstart = wstart - timedelta(days=days)

        cur_bugs = [i for i in cur if i.issue_type == "Bug"]
        prev_bugs = [i for i in prev if i.issue_type == "Bug"]
        cur_res_bugs = [i for i in self._resolved_in(wstart, wend) if i.issue_type == "Bug"]
        prev_res_bugs = [i for i in self._resolved_in(pstart, wstart) if i.issue_type == "Bug"]

        open_now = self._open_as_of(wend)
        open_prev = self._open_as_of(wstart)

        # --- Bugs created ---
        self._add(KPIValue(
            name="bugs_created",
            category="quality",
            period_label=label,
            current_value=len(cur_bugs),
            previous_value=len(prev_bugs),
            unit="count",
            formula="COUNT(issues WHERE type='Bug' AND created IN period)",
            interpretation="Defect injection rate. Increasing = quality regression.",
            threshold_low=10,
            threshold_high=30,
            recommended_action="Bug spike >30: initiate root cause analysis.",
            contributing_count=len(cur_bugs),
        ))

        # --- Bug resolution rate ---
        self._add(KPIValue(
            name="bug_resolution_rate",
            category="quality",
            period_label=label,
            current_value=self._safe_pct(len(cur_res_bugs), len(cur_bugs) or 1),
            previous_value=self._safe_pct(len(prev_res_bugs), len(prev_bugs) or 1),
            unit="%",
            formula="bugs_resolved / bugs_created × 100",
            interpretation="Bug throughput. <70% means defect debt is accumulating.",
            threshold_low=70.0,
            threshold_high=None,
            recommended_action="Bug resolution rate <70%: allocate dedicated bug-fix sprint.",
        ))

        # --- Reopened issues ---
        cur_reopened = [i for i in cur if i.times_reopened > 0]
        prev_reopened = [i for i in prev if i.times_reopened > 0]
        self._add(KPIValue(
            name="reopened_count",
            category="quality",
            period_label=label,
            current_value=len(cur_reopened),
            previous_value=len(prev_reopened),
            unit="count",
            formula="COUNT(issues WHERE times_reopened > 0 AND created IN period)",
            interpretation="Re-opened issues signal poor fix quality or testing gaps.",
            threshold_low=5,
            threshold_high=15,
            recommended_action="High reopens: strengthen definition of done and review gates.",
            contributing_count=len(cur_reopened),
        ))

        # --- Reopen rate ---
        self._add(KPIValue(
            name="reopen_rate",
            category="quality",
            period_label=label,
            current_value=self._safe_pct(len(cur_reopened), len(cur) or 1),
            previous_value=self._safe_pct(len(prev_reopened), len(prev) or 1),
            unit="%",
            formula="reopened_issues / total_created × 100",
            interpretation="Percentage of resolved issues that come back. >5% = systemic quality issue.",
            threshold_low=5.0,
            threshold_high=10.0,
            recommended_action="Reopen rate >10%: mandatory re-test before closing.",
        ))

        # --- Critical bugs open ---
        crit_bugs = [i for i in open_now
                     if i.issue_type == "Bug" and i.priority in ("Critical", "Blocker", "Highest")]
        prev_crit = [i for i in open_prev
                     if i.issue_type == "Bug" and i.priority in ("Critical", "Blocker", "Highest")]
        self._add(KPIValue(
            name="critical_bugs_open",
            category="quality",
            period_label=label,
            current_value=len(crit_bugs),
            previous_value=len(prev_crit),
            unit="count",
            formula="COUNT(open bugs WHERE priority IN ('Critical','Blocker','Highest'))",
            interpretation="Number of open critical defects. Each is a production risk.",
            threshold_low=3,
            threshold_high=10,
            recommended_action="Critical bugs >3: escalate to tech lead for immediate triage.",
            contributing_count=len(crit_bugs),
        ))

        # --- High bugs open ---
        high_bugs = [i for i in open_now
                     if i.issue_type == "Bug" and i.priority in ("High", "Major")]
        self._add(KPIValue(
            name="high_bugs_open",
            category="quality",
            period_label=label,
            current_value=len(high_bugs),
            previous_value=len([i for i in open_prev
                                 if i.issue_type == "Bug" and i.priority in ("High", "Major")]),
            unit="count",
            formula="COUNT(open bugs WHERE priority IN ('High','Major'))",
            interpretation="High-severity open defect exposure.",
            threshold_low=10,
            threshold_high=25,
            contributing_count=len(high_bugs),
        ))

        # --- Bugs by multiple reopens (repeat offenders) ---
        repeat = [i for i in self.issues if i.times_reopened >= 2]
        self._add(KPIValue(
            name="repeat_reopen_count",
            category="quality",
            period_label=label,
            current_value=len(repeat),
            previous_value=None,
            unit="count",
            formula="COUNT(issues WHERE times_reopened >= 2)",
            interpretation="Issues reopened 2+ times indicate deep quality or process failure.",
            threshold_low=2,
            threshold_high=5,
            recommended_action="Issues reopened 2+: enforce root-cause fix before closure.",
            contributing_count=len(repeat),
        ))

    # -----------------------------------------------------------------------
    # 3. Risk & Control
    # -----------------------------------------------------------------------

    def _risk_control(self, label: str, cur: list, prev: list) -> None:
        days = next(d for l, d in PERIODS if l == label)
        wend = self.as_of
        wstart = self.as_of - timedelta(days=days)

        open_now = self._open_as_of(wend)
        open_prev = self._open_as_of(wstart)

        # --- No assignee ---
        no_assign = [i for i in open_now if i.dq_missing_assignee]
        prev_no_assign = [i for i in open_prev if i.dq_missing_assignee]
        self._add(KPIValue(
            name="unassigned_open",
            category="risk",
            period_label=label,
            current_value=len(no_assign),
            previous_value=len(prev_no_assign),
            unit="count",
            formula="COUNT(open issues WHERE assignee IS NULL)",
            interpretation="Unassigned open issues have no owner. They will not move.",
            threshold_low=5,
            threshold_high=20,
            recommended_action="Assign owner to all open issues immediately.",
            contributing_count=len(no_assign),
        ))

        # --- No fix version ---
        no_version = [i for i in open_now if i.dq_missing_fix_version]
        self._add(KPIValue(
            name="no_fix_version_open",
            category="risk",
            period_label=label,
            current_value=len(no_version),
            previous_value=len([i for i in open_prev if i.dq_missing_fix_version]),
            unit="count",
            formula="COUNT(open issues WHERE fix_version IS NULL)",
            interpretation="Issues without fix version cannot be planned or released.",
            threshold_low=10,
            threshold_high=50,
            recommended_action="Assign fix versions during sprint planning.",
            contributing_count=len(no_version),
        ))

        # --- Stuck in same status (>14 days) ---
        stuck = [i for i in open_now if (i.current_status_age_days or 0) > 14]
        prev_stuck = [i for i in open_prev if (i.current_status_age_days or 0) > 14]
        self._add(KPIValue(
            name="stuck_issues_14d",
            category="risk",
            period_label=label,
            current_value=len(stuck),
            previous_value=len(prev_stuck),
            unit="count",
            formula="COUNT(open issues WHERE current_status_age_days > 14)",
            interpretation="Issues stuck for >14 days indicate blockers or abandonment.",
            threshold_low=5,
            threshold_high=20,
            recommended_action="Daily review stuck issues; escalate after 7 days.",
            contributing_count=len(stuck),
        ))

        # --- No update for 7 days ---
        stale = [i for i in open_now if (i.days_without_update or 0) > 7]
        self._add(KPIValue(
            name="stale_issues_7d",
            category="risk",
            period_label=label,
            current_value=len(stale),
            previous_value=None,
            unit="count",
            formula="COUNT(open issues WHERE days_since_last_update > 7)",
            interpretation="Stale issues signal invisible work and poor hygiene.",
            threshold_low=10,
            threshold_high=30,
            recommended_action="Require weekly status update on all in-progress issues.",
            contributing_count=len(stale),
        ))

        # --- Critical open (any type, critical/blocker priority) ---
        crit_open = [i for i in open_now
                     if i.priority in ("Critical", "Blocker", "Highest")]
        prev_crit = [i for i in open_prev
                     if i.priority in ("Critical", "Blocker", "Highest")]
        self._add(KPIValue(
            name="critical_open",
            category="risk",
            period_label=label,
            current_value=len(crit_open),
            previous_value=len(prev_crit),
            unit="count",
            formula="COUNT(open issues WHERE priority IN ('Critical','Blocker','Highest'))",
            interpretation="Open critical/blocker issues. Each is a production or delivery risk.",
            threshold_low=3,
            threshold_high=10,
            recommended_action="Escalate critical open issues to engineering management.",
            contributing_count=len(crit_open),
        ))

        # --- Blocked critical open ---
        blocked_crit = [i for i in open_now
                        if i.priority in ("Critical", "Blocker", "Highest")
                        and (("blocked" in (i.status or "").lower())
                             or (i.labels and any("blocked" in lbl.lower() for lbl in i.labels)))]
        prev_blocked = [i for i in open_prev
                        if i.priority in ("Critical", "Blocker", "Highest")
                        and (("blocked" in (i.status or "").lower())
                             or (i.labels and any("blocked" in lbl.lower() for lbl in i.labels)))]
        self._add(KPIValue(
            name="blocked_critical_open",
            category="risk",
            period_label=label,
            current_value=len(blocked_crit),
            previous_value=len(prev_blocked),
            unit="count",
            formula="COUNT(open critical/blocker issues WHERE status contains 'blocked' OR labels contain 'blocked')",
            interpretation="Critical issues blocked. Each is a delivery showstopper.",
            threshold_low=1,
            threshold_high=5,
            recommended_action="Unblock critical issues within 24 hours; escalate if stuck >48h.",
            contributing_count=len(blocked_crit),
        ))

        # --- Aging critical open (>14 days) ---
        aging_crit = [i for i in open_now
                      if i.priority in ("Critical", "Blocker", "Highest")
                      and (i.age_days or 0) > 14]
        prev_aging = [i for i in open_prev
                      if i.priority in ("Critical", "Blocker", "Highest")
                      and (i.age_days or 0) > 14]
        self._add(KPIValue(
            name="aging_critical_open",
            category="risk",
            period_label=label,
            current_value=len(aging_crit),
            previous_value=len(prev_aging),
            unit="count",
            formula="COUNT(open critical/blocker issues WHERE age_days > 14)",
            interpretation="Critical issues open >14 days. Extended exposure to severe defects.",
            threshold_low=2,
            threshold_high=8,
            recommended_action="Prioritise aging critical issues for immediate resolution.",
            contributing_count=len(aging_crit),
        ))

        # --- SLA at risk ---
        resolved_times = [i.resolution_time_days for i in self.issues
                          if i.resolution_time_days is not None]
        avg_resolution = statistics.mean(resolved_times) if resolved_times else None
        sla_at_risk_count = 0
        if avg_resolution and avg_resolution > 0:
            sla_at_risk_count = len([
                i for i in open_now
                if i.status_category != "Done"
                and (i.age_days or 0) > avg_resolution * 0.8
            ])
        prev_sla_count = 0
        if avg_resolution and avg_resolution > 0:
            prev_sla_count = len([
                i for i in open_prev
                if i.status_category != "Done"
                and (i.age_days or 0) > avg_resolution * 0.8
            ])
        self._add(KPIValue(
            name="sla_at_risk",
            category="risk",
            period_label=label,
            current_value=sla_at_risk_count,
            previous_value=prev_sla_count,
            unit="count",
            formula="COUNT(open issues WHERE age_days > avg_resolution_days × 0.8)",
            interpretation="Unresolved issues consuming >80% of mean resolution SLA. Risk of breach.",
            threshold_low=5,
            threshold_high=15,
            recommended_action="Review at-risk issues; re-prioritise or add resources.",
            contributing_count=sla_at_risk_count,
        ))

    # -----------------------------------------------------------------------
    # 4. Data Quality
    # -----------------------------------------------------------------------

    def _data_quality(self, label: str, cur: list, prev: list) -> None:
        days = next(d for l, d in PERIODS if l == label)
        wend = self.as_of

        open_now = self._open_as_of(wend)
        total = len(open_now) or 1

        dq_fields = {
            "dq_missing_assignee": ("missing_assignee", "Issues without an assignee."),
            "dq_missing_priority": ("missing_priority", "Issues without priority set."),
            "dq_missing_component": ("missing_component", "Issues not linked to any component."),
            "dq_missing_fix_version": ("missing_fix_version", "Issues without a target release."),
            "dq_missing_epic": ("missing_epic", "Issues not linked to an epic."),
            "dq_missing_due_date": ("missing_due_date", "Issues without a due date (where required)."),
            "dq_closed_without_resolution": ("closed_no_resolution", "Closed issues with no resolution set."),
        }

        penalties = 0
        for attr, (kpi_name, interp) in dq_fields.items():
            bad = [i for i in open_now if getattr(i, attr, False)]
            count = len(bad)
            pct = self._safe_pct(count, total)
            penalties += (pct or 0)
            self._add(KPIValue(
                name=kpi_name,
                category="data_quality",
                period_label=label,
                current_value=count,
                previous_value=None,
                unit="count",
                formula=f"COUNT(open issues WHERE {attr} = true)",
                interpretation=interp,
                threshold_low=5,
                threshold_high=20,
                recommended_action=f"Enforce {kpi_name.replace('_', ' ')} in workflow validation.",
                contributing_count=count,
            ))

        # --- Overall DQ score ---
        dq_score = max(0.0, round(100 - (penalties / len(dq_fields)), 1))
        self._add(KPIValue(
            name="dq_score",
            category="data_quality",
            period_label=label,
            current_value=dq_score,
            previous_value=None,
            unit="score",
            formula="100 - AVG(pct_missing for each DQ field)",
            interpretation="Composite data quality score 0-100. <80 = poor hygiene.",
            threshold_low=80.0,
            threshold_high=None,
            recommended_action="DQ score <80: mandatory field validation in Jira workflows.",
        ))

    # -----------------------------------------------------------------------
    # 5. Governance
    # -----------------------------------------------------------------------

    def _governance(self, label: str, cur: list, prev: list) -> None:
        days = next(d for l, d in PERIODS if l == label)
        wend = self.as_of
        wstart = self.as_of - timedelta(days=days)

        open_now = self._open_as_of(wend)
        resolved_now = self._resolved_in(wstart, wend)
        total_open = len(open_now) or 1

        # --- Delivery progress by status ---
        done = [i for i in open_now if i.status_category == "Done"]
        in_progress = [i for i in open_now if i.status_category == "In Progress"]
        todo = [i for i in open_now if i.status_category == "To Do"]

        self._add(KPIValue(
            name="pct_done",
            category="governance",
            period_label=label,
            current_value=self._safe_pct(len(done), total_open),
            previous_value=None,
            unit="%",
            formula="done_issues / total_open_issues × 100",
            interpretation="Project completion percentage by issue count.",
        ))

        # --- Story points delivered ---
        sp_delivered = sum(i.story_points or 0 for i in resolved_now)
        self._add(KPIValue(
            name="story_points_delivered",
            category="governance",
            period_label=label,
            current_value=round(sp_delivered, 1),
            previous_value=None,
            unit="points",
            formula="SUM(story_points) for resolved issues in period",
            interpretation="Delivered business value in story points.",
            contributing_count=len([i for i in resolved_now if i.story_points]),
        ))

        # --- Scope creep indicator ---
        epics = set(i.epic_key for i in self.issues if i.epic_key)
        cur_epics = set(i.epic_key for i in cur if i.epic_key)
        new_epics_pct = self._safe_pct(len(cur_epics), len(epics) or 1)
        self._add(KPIValue(
            name="new_epics_pct",
            category="governance",
            period_label=label,
            current_value=new_epics_pct,
            previous_value=None,
            unit="%",
            formula="new_epics_in_period / total_epics × 100",
            interpretation="High % of new epics in period = scope creep.",
            threshold_low=10.0,
            threshold_high=25.0,
            recommended_action="New epics >25%: review scope with product owner.",
        ))

        # --- Issues by type distribution ---
        type_counts: dict[str, int] = {}
        for i in open_now:
            t = i.issue_type or "Unknown"
            type_counts[t] = type_counts.get(t, 0) + 1

        self._add(KPIValue(
            name="issue_type_distribution",
            category="governance",
            period_label=label,
            current_value=len(type_counts),
            previous_value=None,
            unit="types",
            formula="COUNT(DISTINCT issue_type) for open issues",
            interpretation="Diversity of open issue types.",
            contributing_count=len(open_now),
        ))

    # -----------------------------------------------------------------------
    # 6. Team & Ownership
    # -----------------------------------------------------------------------

    def _team(self, label: str, cur: list, prev: list) -> None:
        days = next(d for l, d in PERIODS if l == label)
        wend = self.as_of
        wstart = self.as_of - timedelta(days=days)
        prev_end = wstart
        prev_start = wstart - timedelta(days=days)

        open_now = self._open_as_of(wend)
        open_prev = self._open_as_of(wstart)
        resolved_now = self._resolved_in(wstart, wend)
        resolved_prev = self._resolved_in(prev_start, prev_end)

        def _load_stats(open_issues):
            ao = {}
            for i in open_issues:
                if i.assignee_id:
                    ao[i.assignee_id] = ao.get(i.assignee_id, 0) + 1
            if ao:
                vals = list(ao.values())
                return max(vals), round(max(vals) / statistics.mean(vals), 2) if statistics.mean(vals) > 0 else None
            return 0, None

        cur_max, cur_imb = _load_stats(open_now)
        prev_max, prev_imb = _load_stats(open_prev)

        self._add(KPIValue(
            name="max_assignee_load",
            category="team",
            period_label=label,
            current_value=cur_max,
            previous_value=prev_max,
            unit="count",
            formula="MAX(open_issues per assignee)",
            interpretation="Highest individual workload. Indicates potential bottleneck.",
            threshold_low=15,
            threshold_high=30,
            recommended_action="Assignee load >30: redistribute work to prevent burnout.",
        ))

        self._add(KPIValue(
            name="workload_imbalance_ratio",
            category="team",
            period_label=label,
            current_value=cur_imb,
            previous_value=prev_imb,
            unit="ratio",
            formula="max_assignee_load / avg_assignee_load",
            interpretation="Ratio >3 indicates severe workload imbalance.",
            threshold_low=2.0,
            threshold_high=3.0,
            recommended_action="Imbalance >3x: immediate rebalancing required.",
        ))

        # --- Active contributors ---
        contributors = set(i.assignee_id for i in resolved_now if i.assignee_id)
        prev_contributors = set(i.assignee_id for i in resolved_prev if i.assignee_id)
        self._add(KPIValue(
            name="active_contributors",
            category="team",
            period_label=label,
            current_value=len(contributors),
            previous_value=len(prev_contributors),
            unit="count",
            formula="COUNT(DISTINCT assignee_id for issues resolved in period)",
            interpretation="Number of people actively resolving issues.",
            contributing_count=len(contributors),
        ))

        # --- Unassigned open ---
        unassigned = [i for i in open_now if not i.assignee_id]
        prev_unassigned = [i for i in open_prev if not i.assignee_id]
        self._add(KPIValue(
            name="unassigned_open_pct",
            category="team",
            period_label=label,
            current_value=self._safe_pct(len(unassigned), len(open_now) or 1),
            previous_value=self._safe_pct(len(prev_unassigned), len(open_prev) or 1),
            unit="%",
            formula="unassigned_open / total_open × 100",
            interpretation="Percentage of open work with no owner.",
            threshold_low=10.0,
            threshold_high=25.0,
            recommended_action="Unassigned >25%: assign owners in next planning session.",
            contributing_count=len(unassigned),
        ))
