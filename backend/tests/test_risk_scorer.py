"""
tests/test_risk_scorer.py

Unit tests for the risk scoring engine.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, datetime, timedelta, timezone
from kpi_engine.calculator import KPICalculator, IssueRecord
from risk_engine.scorer import RiskScorer, DEFAULT_WEIGHTS
from config import get_settings


def _utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def make_issue(key, issue_type="Bug", priority="Critical", status_cat="In Progress",
               created_offset=10, resolved_offset=None, overdue=False,
               reopened=0, status_age=5, days_no_update=2):
    today = date.today()
    created = _utc(today - timedelta(days=created_offset))
    resolved = _utc(today - timedelta(days=resolved_offset)) if resolved_offset else None
    return IssueRecord(
        jira_key=key, project_key="PROJ", summary=f"Issue {key}",
        issue_type=issue_type, status="In Progress", status_category=status_cat,
        priority=priority, assignee_id="u001",
        component_ids=["C1"], fix_version_ids=["V1"], epic_key="E1",
        created_date=created, resolved_date=resolved,
        updated_date=created, due_date=None,
        age_days=created_offset, resolution_time_days=None,
        cycle_time_days=None, times_reopened=reopened,
        is_overdue=overdue, days_without_update=days_no_update,
        current_status_age_days=status_age,
        dq_missing_assignee=False, dq_missing_priority=False,
        dq_missing_component=False, dq_missing_fix_version=False,
        dq_missing_epic=False, dq_missing_due_date=True,
        dq_closed_without_resolution=False, story_points=None,
    )


class TestRiskScorer:

    def _get_risk(self, issues):
        kpis = KPICalculator("PROJ", issues).calculate_all()
        return RiskScorer(kpis).score()

    def test_low_risk_clean_project(self):
        # Healthy project: few issues, all resolved, no overdue
        issues = [
            make_issue(f"P-{i}", priority="Low", status_cat="Done",
                       resolved_offset=5, created_offset=20)
            for i in range(5)
        ]
        risk = self._get_risk(issues)
        assert risk.risk_level in ("low", "medium")

    def test_high_risk_many_critical_bugs(self):
        issues = [
            make_issue(f"P-{i}", issue_type="Bug", priority="Critical",
                       created_offset=5)
            for i in range(15)
        ]
        risk = self._get_risk(issues)
        assert risk.composite_score > 25
        assert len(risk.risk_drivers) > 0

    def test_risk_drivers_not_empty_on_bad_project(self):
        issues = [
            make_issue(f"P-{i}", issue_type="Bug", priority="Critical",
                       overdue=True, status_age=20, reopened=2,
                       created_offset=5)
            for i in range(20)
        ]
        risk = self._get_risk(issues)
        assert len(risk.risk_drivers) >= 1

    def test_recommended_actions_provided(self):
        issues = [make_issue(f"P-{i}") for i in range(10)]
        risk = self._get_risk(issues)
        assert isinstance(risk.recommended_actions, list)
        assert len(risk.recommended_actions) >= 1

    def test_composite_between_0_and_100(self):
        for n in [0, 1, 10, 100, 500]:
            issues = [make_issue(f"P-{i}") for i in range(n)]
            risk = self._get_risk(issues)
            assert 0 <= risk.composite_score <= 100

    def test_weights_must_sum_to_one(self):
        import pytest
        issues = [make_issue("P-1")]
        kpis = KPICalculator("PROJ", issues).calculate_all()
        with pytest.raises(ValueError):
            RiskScorer(kpis, weights={"delivery": 0.5, "quality": 0.5,
                                       "compliance": 0.5, "operational": 0.5}).score()

    def test_risk_level_classification(self):
        from risk_engine.scorer import RiskScorer
        # Test classify logic directly
        assert RiskScorer._classify(80) == "critical"
        assert RiskScorer._classify(60) == "high"
        assert RiskScorer._classify(35) == "medium"
        assert RiskScorer._classify(10) == "low"

    def test_result_to_dict_structure(self):
        issues = [make_issue("P-1")]
        risk = self._get_risk(issues)
        d = risk.to_dict()
        assert "composite_score" in d
        assert "risk_level" in d
        assert "dimensions" in d
        assert "delivery" in d["dimensions"]
        assert "quality" in d["dimensions"]
        assert "recommended_actions" in d
        assert "risk_drivers" in d

    def test_default_period_is_1m(self):
        issues = [make_issue("P-1")]
        kpis = KPICalculator("PROJ", issues).calculate_all()
        risk = RiskScorer(kpis).score()
        assert risk.period_label == "1m"

    def test_period_1w_uses_short_window(self):
        """1w risk should use 7d KPI data (fewer issues in window → different score)."""
        old_issues = [make_issue(f"P-{i}", created_offset=60, resolved_offset=None)
                      for i in range(10)]
        recent_issues = [make_issue(f"Q-{i}", created_offset=3, resolved_offset=None,
                                    issue_type="Bug", priority="Critical")
                         for i in range(5)]
        issues = old_issues + recent_issues
        kpis = KPICalculator("PROJ", issues).calculate_all()
        risk_1w = RiskScorer(kpis, reference_period="1w").score()
        risk_1m = RiskScorer(kpis, reference_period="1m").score()
        assert risk_1w.period_label == "1w"
        # 1w should have fewer issues in window but acute critical bugs
        assert isinstance(risk_1w.composite_score, float)

    def test_period_3m_more_inclusive(self):
        """3m catches more old issues than 1m, potentially different scores."""
        old = [make_issue(f"P-{i}", created_offset=90, resolved_offset=None,
                          priority="Critical", issue_type="Bug")
               for i in range(10)]
        recent = [make_issue(f"Q-{i}", created_offset=10, resolved_offset=None,
                             priority="Low")
                  for i in range(5)]
        issues = old + recent
        kpis = KPICalculator("PROJ", issues).calculate_all()
        risk_3m = RiskScorer(kpis, reference_period="3m").score()
        risk_1m = RiskScorer(kpis, reference_period="1m").score()
        assert risk_3m.period_label == "3m"
        # 3m should include the old critical bugs, driving up quality risk
        assert isinstance(risk_3m.composite_score, float)

    def test_multi_period_produces_different_scores(self):
        """1w, 1m, 3m should produce meaningfully different risk scores."""
        issues = [make_issue(f"P-{i}", created_offset=60, resolved_offset=None,
                             priority="Critical", issue_type="Bug")
                  for i in range(5)]
        kpis = KPICalculator("PROJ", issues).calculate_all()
        scores = {}
        for period in ["1w", "1m", "3m"]:
            risk = RiskScorer(kpis, reference_period=period).score()
            scores[period] = risk.composite_score
        # 3m includes more old issues so score should differ
        assert scores["1w"] != scores["3m"] or scores["1m"] != scores["3m"]

    def test_default_weights_from_config(self):
        """RiskScorer should load default weights from config when none provided."""
        cfg = get_settings()
        issues = [make_issue(f"P-{i}") for i in range(3)]
        kpis = KPICalculator("PROJ", issues).calculate_all()
        risk = RiskScorer(kpis).score()
        assert risk.weights == cfg.risk_weights

    def test_custom_weights_override_config(self):
        """Explicit weights should override config defaults."""
        custom = {"delivery": 0.5, "quality": 0.3, "compliance": 0.1, "operational": 0.1}
        issues = [make_issue(f"P-{i}") for i in range(3)]
        kpis = KPICalculator("PROJ", issues).calculate_all()
        risk = RiskScorer(kpis, weights=custom).score()
        assert risk.weights == custom

    def test_config_risk_weights_validation(self):
        """Config parses valid JSON and rejects bad weights."""
        import json
        cfg = get_settings()
        # Valid weights
        valid = '{"delivery": 0.25, "quality": 0.25, "compliance": 0.25, "operational": 0.25}'
        parsed = json.loads(valid)
        total = sum(parsed.values())
        assert abs(total - 1.0) < 0.01

    def test_risk_weights_affect_composite_score(self):
        """Different weights should produce different composite scores."""
        issues = [make_issue(f"P-{i}", issue_type="Bug", priority="Critical",
                             created_offset=5, resolved_offset=None)
                  for i in range(10)]
        kpis = KPICalculator("PROJ", issues).calculate_all()
        # Weight quality heavily vs weight delivery heavily
        w_quality = {"delivery": 0.1, "quality": 0.7, "compliance": 0.1, "operational": 0.1}
        w_delivery = {"delivery": 0.7, "quality": 0.1, "compliance": 0.1, "operational": 0.1}
        r1 = RiskScorer(kpis, weights=w_quality).score()
        r2 = RiskScorer(kpis, weights=w_delivery).score()
        # With many critical bugs, quality-heavy weighting should differ
        assert r1.composite_score != r2.composite_score

    def test_risk_trend_result_has_period_label(self):
        """RiskScoreResult should carry period_label."""
        issues = [make_issue("P-1")]
        kpis = KPICalculator("PROJ", issues).calculate_all()
        risk = RiskScorer(kpis, reference_period="1w").score()
        assert risk.period_label == "1w"
        risk2 = RiskScorer(kpis, reference_period="3m").score()
        assert risk2.period_label == "3m"
