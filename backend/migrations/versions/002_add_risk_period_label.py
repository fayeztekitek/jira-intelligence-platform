"""Add period_label to risk_score for multi-period risk scoring.

Revision ID: 002
Revises: 001
Create Date: 2026-06-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite requires batch mode for constraint changes
    with op.batch_alter_table("risk_score") as batch_op:
        batch_op.add_column(sa.Column("period_label", sa.String(16), default="1m"))
        batch_op.drop_constraint("uq_risk_project_date", type_="unique")
        batch_op.create_unique_constraint(
            "uq_risk_project_date_period",
            ["project_key", "calculation_date", "period_label"],
        )


def downgrade() -> None:
    with op.batch_alter_table("risk_score") as batch_op:
        batch_op.drop_constraint("uq_risk_project_date_period", type_="unique")
        batch_op.create_unique_constraint(
            "uq_risk_project_date",
            ["project_key", "calculation_date"],
        )
        batch_op.drop_column("period_label")
