"""
risk_engine/scorer.py

Risk scoring model: 4 independent dimensions, each 0-100.
Composite = weighted average (configurable weights).
Risk = Impact × Probability × Trend factor, normalized.

Dimensions:
  - Delivery Risk  (30%): overdue, backlog growth, low resolution rate
  - Quality Risk   (35%): critical bugs, reopen rate, regression
  - Compliance Risk(20%): data quality, unassigned, missing fields
  - Operational    (15%): stuck issues, stale, high WIP

Each dimension produces a 0-100 score.
Composite risk level: Low <25, Medium 25-50, High 50-75, Critical >75
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import structlog

from kpi_engine.calculator import ProjectKPIs, KPIValue

logger = structlog.get_logger(__name__)

# Default dimension weights (must sum to 1.0)
DEFAULT_WEIGHTS = {
    "delivery": 0.30,
    "quality":  0.35,
    "compliance": 0.20,
    "operational": 0.15,
}

TREND_MULTIPLIER = {
    "improving": 0.85,
    "stable":    1.00,
    "degrading": 1.20,
    "unknown":   1.00,
}


@dataclass
class DimensionScore:
    name: str
    raw_score: float        # 0–100
    trend_adjusted: float   # raw × trend_multiplier
    contributing_kpis: list[str]
    drivers: list[str]      # human-readable risk drivers


@dataclass
class RiskScoreResult:
    project_key: str
    calculated_at: date
    delivery: DimensionScore
    quality: DimensionScore
    compliance: DimensionScore
    operational: DimensionScore
    composite_score: float
    risk_level: str         # low | medium | high | critical
    risk_drivers: list[str]
    recommended_actions: list[str]
    weights: dict[str, float]

    def to_dict(self) -> dict:
        return {
            "project_key": self.project_key,
            "calculated_at": self.calculated_at.isoformat(),
            "composite_score": round(self.composite_score, 1),
            "risk_level": self.risk_level,
            "dimensions": {
                "delivery": {
                    "score": round(self.delivery.raw_score, 1),
                    "trend_adjusted": round(self.delivery.trend_adjusted, 1),
                    "drivers": self.delivery.drivers,
                },
                "quality": {
                    "score": round(self.quality.raw_score, 1),
                    "trend_adjusted": round(self.quality.trend_adjusted, 1),
                    "drivers": self.quality.drivers,
                },
                "compliance": {
                    "score": round(self.compliance.raw_score, 1),
                    "trend_adjusted": round(self.compliance.trend_adjusted, 1),
                    "drivers": self.compliance.drivers,
                },
                "operational": {
                    "score": round(self.operational.raw_score, 1),
                    "trend_adjusted": round(self.operational.trend_adjusted, 1),
                    "drivers": self.operational.drivers,
                },
            },
            "risk_drivers": self.risk_drivers,
            "recommended_actions": self.recommended_actions,
            "weights": self.weights,
        }


class RiskScorer:
    """
    Derives risk scores from pre-calculated KPIs.
    Requires a ProjectKPIs object with at least the '1m' period.
    """

    def __init__(
        self,
        kpis: ProjectKPIs,
        weights: dict[str, float] | None = None,
        reference_period: str = "1m",
    ):
        self.kpis = kpis
        self.weights = weights or DEFAULT_WEIGHTS
        self.period = reference_period
        self._validate_weights()

    def _validate_weights(self) -> None:
        total = sum(self.weights.values())
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"Risk weights must sum to 1.0, got {total:.2f}")

    def score(self) -> RiskScoreResult:
        delivery = self._score_delivery()
        quality = self._score_quality()
        compliance = self._score_compliance()
        operational = self._score_operational()

        composite = (
            delivery.trend_adjusted  * self.weights["delivery"] +
            quality.trend_adjusted   * self.weights["quality"] +
            compliance.trend_adjusted * self.weights["compliance"] +
            operational.trend_adjusted * self.weights["operational"]
        )
        composite = min(100.0, max(0.0, round(composite, 1)))

        risk_level = self._classify(composite)
        drivers = (
            delivery.drivers + quality.drivers +
            compliance.drivers + operational.drivers
        )[:10]  # top 10

        actions = self._build_actions(delivery, quality, compliance, operational)

        logger.info("risk_scored", project=self.kpis.project_key,
                    composite=composite, level=risk_level)

        return RiskScoreResult(
            project_key=self.kpis.project_key,
            calculated_at=self.kpis.calculated_at,
            delivery=delivery,
            quality=quality,
            compliance=compliance,
            operational=operational,
            composite_score=composite,
            risk_level=risk_level,
            risk_drivers=drivers,
            recommended_actions=actions,
            weights=self.weights,
        )

    # -----------------------------------------------------------------------
    # Dimension scorers
    # -----------------------------------------------------------------------

    def _score_delivery(self) -> DimensionScore:
        kv = lambda name: self.kpis.by_name(name, self.period)
        drivers = []
        signals = []

        # Resolution rate (<80% = bad)
        rr = kv("resolution_rate")
        if rr and rr.current_value is not None:
            if rr.current_value < 80:
                gap = 80 - rr.current_value
                signals.append(min(gap * 1.5, 40))
                drivers.append(f"Resolution rate {rr.current_value:.0f}% (target ≥80%)")

        # Overdue issues
        od = kv("overdue_count")
        if od and od.current_value:
            signals.append(min(od.current_value * 3, 30))
            if od.current_value >= 5:
                drivers.append(f"{od.current_value} overdue issues")

        # Aging issues
        aging = kv("aging_issues_30d")
        if aging and aging.current_value:
            signals.append(min(aging.current_value * 0.5, 20))
            if aging.current_value >= 10:
                drivers.append(f"{aging.current_value} issues open >30 days")

        # WIP too high
        wip = kv("wip")
        if wip and (wip.current_value or 0) > 50:
            signals.append(15)
            drivers.append(f"WIP at {wip.current_value} (target ≤50)")

        raw = min(sum(signals), 100)
        trend = self._dominant_trend([rr, od, aging])
        return DimensionScore(
            name="delivery",
            raw_score=raw,
            trend_adjusted=min(raw * TREND_MULTIPLIER[trend], 100),
            contributing_kpis=["resolution_rate", "overdue_count", "aging_issues_30d", "wip"],
            drivers=drivers,
        )

    def _score_quality(self) -> DimensionScore:
        kv = lambda name: self.kpis.by_name(name, self.period)
        drivers = []
        signals = []

        crit = kv("critical_bugs_open")
        if crit and crit.current_value:
            signals.append(min(crit.current_value * 8, 40))
            if crit.current_value >= 3:
                drivers.append(f"{crit.current_value} critical bugs open")

        rr = kv("reopen_rate")
        if rr and rr.current_value:
            signals.append(min(rr.current_value * 3, 30))
            if rr.current_value >= 5:
                drivers.append(f"Reopen rate {rr.current_value:.1f}%")

        bugs = kv("bugs_created")
        if bugs and bugs.trend == "degrading":
            signals.append(15)
            drivers.append("Bug creation rate is degrading")

        repeat = kv("repeat_reopen_count")
        if repeat and repeat.current_value:
            signals.append(min(repeat.current_value * 5, 20))
            if repeat.current_value >= 2:
                drivers.append(f"{repeat.current_value} issues reopened ≥2 times")

        raw = min(sum(signals), 100)
        trend = self._dominant_trend([crit, rr, bugs])
        return DimensionScore(
            name="quality",
            raw_score=raw,
            trend_adjusted=min(raw * TREND_MULTIPLIER[trend], 100),
            contributing_kpis=["critical_bugs_open", "reopen_rate", "bugs_created"],
            drivers=drivers,
        )

    def _score_compliance(self) -> DimensionScore:
        kv = lambda name: self.kpis.by_name(name, self.period)
        drivers = []
        signals = []

        dq = kv("dq_score")
        if dq and dq.current_value is not None:
            gap = 100 - dq.current_value
            signals.append(min(gap * 1.2, 40))
            if dq.current_value < 80:
                drivers.append(f"Data quality score {dq.current_value:.0f}/100")

        unassigned = kv("unassigned_open")
        if unassigned and unassigned.current_value:
            signals.append(min(unassigned.current_value * 2, 25))
            if unassigned.current_value >= 5:
                drivers.append(f"{unassigned.current_value} open issues unassigned")

        no_ver = kv("no_fix_version_open")
        if no_ver and no_ver.current_value:
            signals.append(min(no_ver.current_value * 0.5, 20))
            if no_ver.current_value >= 10:
                drivers.append(f"{no_ver.current_value} issues without fix version")

        raw = min(sum(signals), 100)
        trend = self._dominant_trend([dq, unassigned])
        return DimensionScore(
            name="compliance",
            raw_score=raw,
            trend_adjusted=min(raw * TREND_MULTIPLIER[trend], 100),
            contributing_kpis=["dq_score", "unassigned_open", "no_fix_version_open"],
            drivers=drivers,
        )

    def _score_operational(self) -> DimensionScore:
        kv = lambda name: self.kpis.by_name(name, self.period)
        drivers = []
        signals = []

        stuck = kv("stuck_issues_14d")
        if stuck and stuck.current_value:
            signals.append(min(stuck.current_value * 3, 35))
            if stuck.current_value >= 5:
                drivers.append(f"{stuck.current_value} issues stuck >14 days")

        stale = kv("stale_issues_7d")
        if stale and stale.current_value:
            signals.append(min(stale.current_value * 1.5, 30))
            if stale.current_value >= 10:
                drivers.append(f"{stale.current_value} stale issues (no update >7d)")

        crit = kv("critical_open")
        if crit and crit.current_value:
            signals.append(min(crit.current_value * 5, 30))
            if crit.current_value >= 3:
                drivers.append(f"{crit.current_value} critical/blocker issues open")

        raw = min(sum(signals), 100)
        trend = self._dominant_trend([stuck, stale, crit])
        return DimensionScore(
            name="operational",
            raw_score=raw,
            trend_adjusted=min(raw * TREND_MULTIPLIER[trend], 100),
            contributing_kpis=["stuck_issues_14d", "stale_issues_7d", "critical_open"],
            drivers=drivers,
        )

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _classify(score: float) -> str:
        if score >= 75:
            return "critical"
        if score >= 50:
            return "high"
        if score >= 25:
            return "medium"
        return "low"

    @staticmethod
    def _dominant_trend(kpis: list[KPIValue | None]) -> str:
        trends = [k.trend for k in kpis if k is not None]
        if "degrading" in trends:
            return "degrading"
        if "improving" in trends and "stable" not in trends:
            return "improving"
        return "stable"

    @staticmethod
    def _build_actions(d: DimensionScore, q: DimensionScore,
                       c: DimensionScore, o: DimensionScore) -> list[str]:
        actions = []
        if d.raw_score >= 50:
            actions.append("DELIVERY: Immediate backlog triage and capacity review required.")
        elif d.raw_score >= 25:
            actions.append("DELIVERY: Monitor resolution rate weekly; address overdue issues.")

        if q.raw_score >= 50:
            actions.append("QUALITY: Critical bug count exceeds threshold — escalate to tech lead.")
        elif q.raw_score >= 25:
            actions.append("QUALITY: Review reopen patterns; strengthen testing before closure.")

        if c.raw_score >= 50:
            actions.append("COMPLIANCE: Data quality critical — enforce mandatory fields in workflow.")
        elif c.raw_score >= 25:
            actions.append("COMPLIANCE: Assign owners to all open issues this sprint.")

        if o.raw_score >= 50:
            actions.append("OPERATIONAL: Multiple blockers detected — run blocker review meeting.")
        elif o.raw_score >= 25:
            actions.append("OPERATIONAL: Review and unblock stuck issues in daily standup.")

        if not actions:
            actions.append("Project health is good. Maintain current practices.")

        return actions
