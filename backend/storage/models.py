"""
storage/models.py — Star schema database models.

Star Schema:
  Facts:    FactIssue, FactSnapshot, FactTransition
  Dims:     DimProject, DimSprint, DimVersion, DimUser, DimComponent
  Audit:    ExtractionRun, KPIResult, RiskScore
"""
from datetime import datetime, date, timezone
from typing import Optional
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime, Date,
    Text, ForeignKey, Index, UniqueConstraint, Enum as SAEnum
)
from sqlalchemy.orm import DeclarativeBase, relationship
import enum


class Base(DeclarativeBase):
    pass


# ─── Enums ────────────────────────────────────────────────────────────────────

class TrendDirection(str, enum.Enum):
    IMPROVING = "improving"
    STABLE = "stable"
    DEGRADING = "degrading"
    UNKNOWN = "unknown"


class RiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RunStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"


# ─── Dimensions ───────────────────────────────────────────────────────────────

class DimProject(Base):
    """Jira project / product dimension."""
    __tablename__ = "dim_project"

    id = Column(String(64), primary_key=True)          # Jira project key
    name = Column(String(255), nullable=False)
    project_type = Column(String(64))                   # software | business | service_desk
    lead_account_id = Column(String(128))
    lead_display_name = Column(String(255))
    description = Column(Text)
    url = Column(String(512))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    issues = relationship("FactIssue", back_populates="project")
    snapshots = relationship("FactSnapshot", back_populates="project")


class DimUser(Base):
    """Jira user dimension."""
    __tablename__ = "dim_user"

    account_id = Column(String(128), primary_key=True)
    display_name = Column(String(255))
    email = Column(String(255))
    is_active = Column(Boolean, default=True)
    timezone = Column(String(64))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class DimComponent(Base):
    """Jira component dimension."""
    __tablename__ = "dim_component"

    id = Column(String(64), primary_key=True)
    project_key = Column(String(64), ForeignKey("dim_project.id"))
    name = Column(String(255), nullable=False)
    lead_account_id = Column(String(128))
    description = Column(Text)


class DimVersion(Base):
    """Jira fix version / release dimension."""
    __tablename__ = "dim_version"

    id = Column(String(64), primary_key=True)
    project_key = Column(String(64), ForeignKey("dim_project.id"))
    name = Column(String(255), nullable=False)
    description = Column(Text)
    release_date = Column(Date)
    is_released = Column(Boolean, default=False)
    is_archived = Column(Boolean, default=False)
    is_overdue = Column(Boolean, default=False)


class DimSprint(Base):
    """Jira sprint dimension."""
    __tablename__ = "dim_sprint"

    id = Column(Integer, primary_key=True)
    board_id = Column(Integer)
    name = Column(String(255), nullable=False)
    state = Column(String(32))          # active | closed | future
    start_date = Column(DateTime)
    end_date = Column(DateTime)
    complete_date = Column(DateTime)
    goal = Column(Text)


# ─── Facts ────────────────────────────────────────────────────────────────────

class FactIssue(Base):
    """
    Core issue fact table. One row per Jira issue (latest state).
    For history, see FactTransition.
    """
    __tablename__ = "fact_issue"

    id = Column(Integer, primary_key=True, autoincrement=True)
    jira_id = Column(String(64), unique=True, nullable=False, index=True)
    jira_key = Column(String(64), unique=True, nullable=False, index=True)

    # Dimensions FK
    project_key = Column(String(64), ForeignKey("dim_project.id"), index=True)
    assignee_id = Column(String(128), ForeignKey("dim_user.account_id"), nullable=True)
    reporter_id = Column(String(128), ForeignKey("dim_user.account_id"), nullable=True)
    component_ids = Column(Text)        # JSON array of component IDs
    fix_version_ids = Column(Text)      # JSON array of version IDs
    sprint_ids = Column(Text)           # JSON array of sprint IDs

    # Descriptors
    summary = Column(Text, nullable=False)
    description = Column(Text)
    issue_type = Column(String(64))     # Bug | Story | Epic | Task | Sub-task
    status = Column(String(64))
    status_category = Column(String(32))  # To Do | In Progress | Done
    priority = Column(String(32))
    resolution = Column(String(64))
    labels = Column(Text)               # JSON array
    epic_key = Column(String(64))
    parent_key = Column(String(64))
    story_points = Column(Float)
    original_estimate_seconds = Column(Integer)
    time_spent_seconds = Column(Integer)

    # Dates
    created_date = Column(DateTime, index=True)
    updated_date = Column(DateTime, index=True)
    due_date = Column(Date)
    resolved_date = Column(DateTime)
    first_response_date = Column(DateTime)

    # Computed fields (updated on each sync)
    age_days = Column(Integer)                  # days since created
    resolution_time_days = Column(Float)        # if resolved
    cycle_time_days = Column(Float)             # In Progress → Done
    lead_time_days = Column(Float)              # Created → Done
    times_reopened = Column(Integer, default=0)
    is_overdue = Column(Boolean, default=False)
    days_without_update = Column(Integer)
    current_status_age_days = Column(Integer)   # days in current status

    # Data quality flags
    dq_missing_assignee = Column(Boolean, default=False)
    dq_missing_priority = Column(Boolean, default=False)
    dq_missing_component = Column(Boolean, default=False)
    dq_missing_fix_version = Column(Boolean, default=False)
    dq_missing_epic = Column(Boolean, default=False)
    dq_missing_due_date = Column(Boolean, default=False)
    dq_closed_without_resolution = Column(Boolean, default=False)

    # Metadata
    last_synced_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    raw_json = Column(Text)             # Compressed raw payload for audit

    project = relationship("DimProject", back_populates="issues")
    transitions = relationship("FactTransition", back_populates="issue",
                               foreign_keys="FactTransition.jira_key",
                               primaryjoin="FactIssue.jira_key == FactTransition.jira_key")

    __table_args__ = (
        Index("ix_fact_issue_project_status", "project_key", "status"),
        Index("ix_fact_issue_project_type", "project_key", "issue_type"),
        Index("ix_fact_issue_created", "created_date"),
    )


class FactTransition(Base):
    """
    Issue changelog / status transition history.
    Every status change, assignee change, priority change is recorded.
    Enables: cycle time, lead time, time-in-status, reopen detection.
    """
    __tablename__ = "fact_transition"

    id = Column(Integer, primary_key=True, autoincrement=True)
    jira_key = Column(String(64), ForeignKey("fact_issue.jira_key"), index=True)
    changelog_id = Column(String(64), unique=True)

    field = Column(String(64))          # status | assignee | priority | resolution
    from_value = Column(String(255))
    from_string = Column(String(255))
    to_value = Column(String(255))
    to_string = Column(String(255))
    changed_by_id = Column(String(128))
    changed_by_name = Column(String(255))
    changed_at = Column(DateTime, index=True)

    issue = relationship("FactIssue", back_populates="transitions",
                         foreign_keys=[jira_key])

    __table_args__ = (
        Index("ix_fact_transition_key_field", "jira_key", "field"),
    )


class FactSnapshot(Base):
    """
    Periodic aggregate snapshot of project KPIs.
    One row per (project, snapshot_date, period_type).
    This is what powers the trend engine.
    """
    __tablename__ = "fact_snapshot"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_key = Column(String(64), ForeignKey("dim_project.id"), index=True)
    snapshot_date = Column(Date, index=True)
    period_type = Column(String(16))    # daily | weekly | monthly | quarterly | yearly

    # Delivery
    total_open = Column(Integer, default=0)
    total_created = Column(Integer, default=0)
    total_resolved = Column(Integer, default=0)
    resolution_rate = Column(Float, default=0.0)
    avg_resolution_days = Column(Float)
    median_resolution_days = Column(Float)
    avg_cycle_time_days = Column(Float)
    avg_lead_time_days = Column(Float)
    throughput = Column(Integer, default=0)
    backlog_size = Column(Integer, default=0)
    wip = Column(Integer, default=0)
    overdue_count = Column(Integer, default=0)

    # Quality
    bugs_created = Column(Integer, default=0)
    bugs_resolved = Column(Integer, default=0)
    bug_resolution_rate = Column(Float, default=0.0)
    reopened_count = Column(Integer, default=0)
    reopen_rate = Column(Float, default=0.0)
    regression_count = Column(Integer, default=0)
    critical_bugs_open = Column(Integer, default=0)
    high_bugs_open = Column(Integer, default=0)

    # Data Quality
    dq_missing_assignee = Column(Integer, default=0)
    dq_missing_priority = Column(Integer, default=0)
    dq_missing_component = Column(Integer, default=0)
    dq_missing_fix_version = Column(Integer, default=0)
    dq_score = Column(Float, default=100.0)

    # Risk
    risk_score = Column(Float, default=0.0)
    risk_level = Column(SAEnum(RiskLevel), default=RiskLevel.LOW)

    # Sprint (if applicable)
    sprint_velocity = Column(Float)
    sprint_predictability = Column(Float)
    spillover_rate = Column(Float)

    project = relationship("DimProject", back_populates="snapshots")

    __table_args__ = (
        UniqueConstraint("project_key", "snapshot_date", "period_type",
                         name="uq_snapshot_project_date_period"),
    )


# ─── Audit & Operations ───────────────────────────────────────────────────────

class ExtractionRun(Base):
    """
    Audit log for every extraction run.
    Provides full traceability for compliance and internal audit.
    """
    __tablename__ = "extraction_run"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), unique=True, nullable=False)
    run_type = Column(String(32))       # full | incremental | snapshot
    triggered_by = Column(String(64))   # scheduler | api | manual
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime)
    status = Column(SAEnum(RunStatus), default=RunStatus.RUNNING)
    projects_processed = Column(Integer, default=0)
    issues_extracted = Column(Integer, default=0)
    issues_updated = Column(Integer, default=0)
    transitions_extracted = Column(Integer, default=0)
    error_count = Column(Integer, default=0)
    error_details = Column(Text)
    duration_seconds = Column(Float)
    jira_api_calls = Column(Integer, default=0)


class KPIResult(Base):
    """
    Stored KPI calculation results for every project/period.
    Enables: trend comparison, audit trail, executive reporting.
    """
    __tablename__ = "kpi_result"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_key = Column(String(64), ForeignKey("dim_project.id"), index=True)
    kpi_name = Column(String(128), index=True)
    kpi_category = Column(String(64))
    calculation_date = Column(Date, index=True)
    period_label = Column(String(32))   # 1d | 1w | 2w | 4w | 1m | 3m | 6m | 1y
    current_value = Column(Float)
    previous_value = Column(Float)
    delta = Column(Float)
    delta_pct = Column(Float)
    trend = Column(SAEnum(TrendDirection), default=TrendDirection.UNKNOWN)
    risk_level = Column(SAEnum(RiskLevel), default=RiskLevel.LOW)
    formula = Column(Text)
    interpretation = Column(Text)
    recommended_action = Column(Text)

    __table_args__ = (
        UniqueConstraint("project_key", "kpi_name", "calculation_date", "period_label",
                         name="uq_kpi_project_name_date_period"),
    )


class RiskScore(Base):
    """
    Risk score per project, calculated as Impact × Probability × Trend.
    One row per (project, date, period_label).
    """
    __tablename__ = "risk_score"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_key = Column(String(64), ForeignKey("dim_project.id"), index=True)
    calculation_date = Column(Date, index=True)
    period_label = Column(String(16), default="1m")  # 1w | 1m | 3m
    delivery_risk = Column(Float, default=0.0)
    quality_risk = Column(Float, default=0.0)
    compliance_risk = Column(Float, default=0.0)
    operational_risk = Column(Float, default=0.0)
    composite_risk = Column(Float, default=0.0)
    risk_level = Column(SAEnum(RiskLevel), default=RiskLevel.LOW)
    risk_drivers = Column(Text)         # JSON: list of contributing factors
    recommended_actions = Column(Text)  # JSON: list of recommendations

    __table_args__ = (
        UniqueConstraint("project_key", "calculation_date", "period_label",
                         name="uq_risk_project_date_period"),
    )
