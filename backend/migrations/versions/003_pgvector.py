"""Enable pgvector extension and add embedding columns.

Revision ID: 003
Revises: 002
Create Date: 2026-06-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    if is_pg:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    tables = [
        ("dim_project", "dim_project"),
        ("fact_issue", "fact_issue"),
        ("kpi_result", "kpi_result"),
    ]

    for table, _ in tables:
        with op.batch_alter_table(table) as batch_op:
            if is_pg:
                batch_op.add_column(sa.Column("embedding", sa.Text(), nullable=True))
            else:
                batch_op.add_column(sa.Column("embedding", sa.Text(), nullable=True))

    if is_pg:
        op.execute(
            "CREATE INDEX ix_dim_project_embedding "
            "ON dim_project USING hnsw (embedding vector_cosine_ops)"
        )
        op.execute(
            "CREATE INDEX ix_fact_issue_embedding "
            "ON fact_issue USING hnsw (embedding vector_cosine_ops)"
        )
        op.execute(
            "CREATE INDEX ix_kpi_result_embedding "
            "ON kpi_result USING hnsw (embedding vector_cosine_ops)"
        )


def downgrade() -> None:
    is_pg = op.get_bind().dialect.name == "postgresql"

    if is_pg:
        op.execute("DROP INDEX IF EXISTS ix_dim_project_embedding")
        op.execute("DROP INDEX IF EXISTS ix_fact_issue_embedding")
        op.execute("DROP INDEX IF EXISTS ix_kpi_result_embedding")

    for table in ("dim_project", "fact_issue", "kpi_result"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column("embedding")
