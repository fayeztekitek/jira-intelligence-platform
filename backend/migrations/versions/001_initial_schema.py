"""Initial star schema — all fact, dimension, and audit tables.

Revision ID: 001
Revises:
Create Date: 2026-06-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Enums ─────────────────────────────────────────────────────
    sa.Enum("improving", "stable", "degrading", "unknown", name="trenddirection").create(op.get_bind())
    sa.Enum("low", "medium", "high", "critical", name="risklevel").create(op.get_bind())
    sa.Enum("running", "success", "failed", "partial", name="runstatus").create(op.get_bind())

    # ── DimProject ─────────────────────────────────────────────────
    op.create_table(
        "dim_project",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("project_type", sa.String(64)),
        sa.Column("lead_account_id", sa.String(128)),
        sa.Column("lead_display_name", sa.String(255)),
        sa.Column("description", sa.Text),
        sa.Column("url", sa.String(512)),
        sa.Column("is_active", sa.Boolean, default=True),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
    )

    # ── DimUser ────────────────────────────────────────────────────
    op.create_table(
        "dim_user",
        sa.Column("account_id", sa.String(128), primary_key=True),
        sa.Column("display_name", sa.String(255)),
        sa.Column("email", sa.String(255)),
        sa.Column("is_active", sa.Boolean, default=True),
        sa.Column("timezone", sa.String(64)),
        sa.Column("updated_at", sa.DateTime),
    )

    # ── DimComponent ───────────────────────────────────────────────
    op.create_table(
        "dim_component",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_key", sa.String(64), sa.ForeignKey("dim_project.id")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("lead_account_id", sa.String(128)),
        sa.Column("description", sa.Text),
    )

    # ── DimVersion ─────────────────────────────────────────────────
    op.create_table(
        "dim_version",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_key", sa.String(64), sa.ForeignKey("dim_project.id")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("release_date", sa.Date),
        sa.Column("is_released", sa.Boolean, default=False),
        sa.Column("is_archived", sa.Boolean, default=False),
        sa.Column("is_overdue", sa.Boolean, default=False),
    )

    # ── DimSprint ──────────────────────────────────────────────────
    op.create_table(
        "dim_sprint",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("board_id", sa.Integer),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("state", sa.String(32)),
        sa.Column("start_date", sa.DateTime),
        sa.Column("end_date", sa.DateTime),
        sa.Column("complete_date", sa.DateTime),
        sa.Column("goal", sa.Text),
    )

    # ── FactIssue ──────────────────────────────────────────────────
    op.create_table(
        "fact_issue",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("jira_id", sa.String(64), unique=True, nullable=False, index=True),
        sa.Column("jira_key", sa.String(64), unique=True, nullable=False, index=True),
        sa.Column("project_key", sa.String(64), sa.ForeignKey("dim_project.id"), index=True),
        sa.Column("assignee_id", sa.String(128), sa.ForeignKey("dim_user.account_id"), nullable=True),
        sa.Column("reporter_id", sa.String(128), sa.ForeignKey("dim_user.account_id"), nullable=True),
        sa.Column("component_ids", sa.Text),
        sa.Column("fix_version_ids", sa.Text),
        sa.Column("sprint_ids", sa.Text),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("issue_type", sa.String(64)),
        sa.Column("status", sa.String(64)),
        sa.Column("status_category", sa.String(32)),
        sa.Column("priority", sa.String(32)),
        sa.Column("resolution", sa.String(64)),
        sa.Column("labels", sa.Text),
        sa.Column("epic_key", sa.String(64)),
        sa.Column("parent_key", sa.String(64)),
        sa.Column("story_points", sa.Float),
        sa.Column("original_estimate_seconds", sa.Integer),
        sa.Column("time_spent_seconds", sa.Integer),
        sa.Column("created_date", sa.DateTime, index=True),
        sa.Column("updated_date", sa.DateTime, index=True),
        sa.Column("due_date", sa.Date),
        sa.Column("resolved_date", sa.DateTime),
        sa.Column("first_response_date", sa.DateTime),
        sa.Column("age_days", sa.Integer),
        sa.Column("resolution_time_days", sa.Float),
        sa.Column("cycle_time_days", sa.Float),
        sa.Column("lead_time_days", sa.Float),
        sa.Column("times_reopened", sa.Integer, default=0),
        sa.Column("is_overdue", sa.Boolean, default=False),
        sa.Column("days_without_update", sa.Integer),
        sa.Column("current_status_age_days", sa.Integer),
        sa.Column("dq_missing_assignee", sa.Boolean, default=False),
        sa.Column("dq_missing_priority", sa.Boolean, default=False),
        sa.Column("dq_missing_component", sa.Boolean, default=False),
        sa.Column("dq_missing_fix_version", sa.Boolean, default=False),
        sa.Column("dq_missing_epic", sa.Boolean, default=False),
        sa.Column("dq_missing_due_date", sa.Boolean, default=False),
        sa.Column("dq_closed_without_resolution", sa.Boolean, default=False),
        sa.Column("last_synced_at", sa.DateTime),
        sa.Column("raw_json", sa.Text),
    )
    op.create_index("ix_fact_issue_project_status", "fact_issue", ["project_key", "status"])
    op.create_index("ix_fact_issue_project_type", "fact_issue", ["project_key", "issue_type"])
    op.create_index("ix_fact_issue_created", "fact_issue", ["created_date"])

    # ── FactTransition ─────────────────────────────────────────────
    op.create_table(
        "fact_transition",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("jira_key", sa.String(64), sa.ForeignKey("fact_issue.jira_key"), index=True),
        sa.Column("changelog_id", sa.String(64), unique=True),
        sa.Column("field", sa.String(64)),
        sa.Column("from_value", sa.String(255)),
        sa.Column("from_string", sa.String(255)),
        sa.Column("to_value", sa.String(255)),
        sa.Column("to_string", sa.String(255)),
        sa.Column("changed_by_id", sa.String(128)),
        sa.Column("changed_by_name", sa.String(255)),
        sa.Column("changed_at", sa.DateTime, index=True),
    )
    op.create_index("ix_fact_transition_key_field", "fact_transition", ["jira_key", "field"])

    # ── FactSnapshot ───────────────────────────────────────────────
    op.create_table(
        "fact_snapshot",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_key", sa.String(64), sa.ForeignKey("dim_project.id"), index=True),
        sa.Column("snapshot_date", sa.Date, index=True),
        sa.Column("period_type", sa.String(16)),
        sa.Column("total_open", sa.Integer, default=0),
        sa.Column("total_created", sa.Integer, default=0),
        sa.Column("total_resolved", sa.Integer, default=0),
        sa.Column("resolution_rate", sa.Float, default=0.0),
        sa.Column("avg_resolution_days", sa.Float),
        sa.Column("median_resolution_days", sa.Float),
        sa.Column("avg_cycle_time_days", sa.Float),
        sa.Column("avg_lead_time_days", sa.Float),
        sa.Column("throughput", sa.Integer, default=0),
        sa.Column("backlog_size", sa.Integer, default=0),
        sa.Column("wip", sa.Integer, default=0),
        sa.Column("overdue_count", sa.Integer, default=0),
        sa.Column("bugs_created", sa.Integer, default=0),
        sa.Column("bugs_resolved", sa.Integer, default=0),
        sa.Column("bug_resolution_rate", sa.Float, default=0.0),
        sa.Column("reopened_count", sa.Integer, default=0),
        sa.Column("reopen_rate", sa.Float, default=0.0),
        sa.Column("regression_count", sa.Integer, default=0),
        sa.Column("critical_bugs_open", sa.Integer, default=0),
        sa.Column("high_bugs_open", sa.Integer, default=0),
        sa.Column("dq_missing_assignee", sa.Integer, default=0),
        sa.Column("dq_missing_priority", sa.Integer, default=0),
        sa.Column("dq_missing_component", sa.Integer, default=0),
        sa.Column("dq_missing_fix_version", sa.Integer, default=0),
        sa.Column("dq_score", sa.Float, default=100.0),
        sa.Column("risk_score", sa.Float, default=0.0),
        sa.Column("risk_level", sa.Enum("low", "medium", "high", "critical", name="risklevel"), default="low"),
        sa.Column("sprint_velocity", sa.Float),
        sa.Column("sprint_predictability", sa.Float),
        sa.Column("spillover_rate", sa.Float),
        sa.UniqueConstraint("project_key", "snapshot_date", "period_type", name="uq_snapshot_project_date_period"),
    )

    # ── ExtractionRun ──────────────────────────────────────────────
    op.create_table(
        "extraction_run",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(64), unique=True, nullable=False),
        sa.Column("run_type", sa.String(32)),
        sa.Column("triggered_by", sa.String(64)),
        sa.Column("started_at", sa.DateTime),
        sa.Column("completed_at", sa.DateTime),
        sa.Column("status", sa.Enum("running", "success", "failed", "partial", name="runstatus"), default="running"),
        sa.Column("projects_processed", sa.Integer, default=0),
        sa.Column("issues_extracted", sa.Integer, default=0),
        sa.Column("issues_updated", sa.Integer, default=0),
        sa.Column("transitions_extracted", sa.Integer, default=0),
        sa.Column("error_count", sa.Integer, default=0),
        sa.Column("error_details", sa.Text),
        sa.Column("duration_seconds", sa.Float),
        sa.Column("jira_api_calls", sa.Integer, default=0),
    )

    # ── KPIResult ──────────────────────────────────────────────────
    op.create_table(
        "kpi_result",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_key", sa.String(64), sa.ForeignKey("dim_project.id"), index=True),
        sa.Column("kpi_name", sa.String(128), index=True),
        sa.Column("kpi_category", sa.String(64)),
        sa.Column("calculation_date", sa.Date, index=True),
        sa.Column("period_label", sa.String(32)),
        sa.Column("current_value", sa.Float),
        sa.Column("previous_value", sa.Float),
        sa.Column("delta", sa.Float),
        sa.Column("delta_pct", sa.Float),
        sa.Column("trend", sa.Enum("improving", "stable", "degrading", "unknown", name="trenddirection"), default="unknown"),
        sa.Column("risk_level", sa.Enum("low", "medium", "high", "critical", name="risklevel"), default="low"),
        sa.Column("formula", sa.Text),
        sa.Column("interpretation", sa.Text),
        sa.Column("recommended_action", sa.Text),
        sa.UniqueConstraint("project_key", "kpi_name", "calculation_date", "period_label",
                            name="uq_kpi_project_name_date_period"),
    )

    # ── RiskScore ──────────────────────────────────────────────────
    op.create_table(
        "risk_score",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_key", sa.String(64), sa.ForeignKey("dim_project.id"), index=True),
        sa.Column("calculation_date", sa.Date, index=True),
        sa.Column("delivery_risk", sa.Float, default=0.0),
        sa.Column("quality_risk", sa.Float, default=0.0),
        sa.Column("compliance_risk", sa.Float, default=0.0),
        sa.Column("operational_risk", sa.Float, default=0.0),
        sa.Column("composite_risk", sa.Float, default=0.0),
        sa.Column("risk_level", sa.Enum("low", "medium", "high", "critical", name="risklevel"), default="low"),
        sa.Column("risk_drivers", sa.Text),
        sa.Column("recommended_actions", sa.Text),
        sa.UniqueConstraint("project_key", "calculation_date", name="uq_risk_project_date"),
    )


def downgrade() -> None:
    op.drop_table("risk_score")
    op.drop_table("kpi_result")
    op.drop_table("extraction_run")
    op.drop_table("fact_snapshot")
    op.drop_table("fact_transition")
    op.drop_table("fact_issue")
    op.drop_table("dim_sprint")
    op.drop_table("dim_version")
    op.drop_table("dim_component")
    op.drop_table("dim_user")
    op.drop_table("dim_project")
    sa.Enum(name="trenddirection").drop(op.get_bind(), if_exists=True)
    sa.Enum(name="risklevel").drop(op.get_bind(), if_exists=True)
    sa.Enum(name="runstatus").drop(op.get_bind(), if_exists=True)
