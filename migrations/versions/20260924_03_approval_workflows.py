"""Add durable reservation-to-workflow identity mapping.

Revision ID: 20260924_03
Revises: 20260924_02
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260924_03"
down_revision: str | None = "20260924_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the non-PII durable workflow mapping."""
    op.create_table(
        "approval_workflows",
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reservation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["reservation_id"],
            ["reservation_requests.reservation_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("workflow_id"),
        sa.UniqueConstraint("reservation_id"),
        sa.UniqueConstraint("thread_id"),
    )
    op.create_index(
        "ix_approval_workflows_reservation_id",
        "approval_workflows",
        ["reservation_id"],
    )


def downgrade() -> None:
    """Remove workflow identities without modifying reservation decisions."""
    op.drop_index("ix_approval_workflows_reservation_id", table_name="approval_workflows")
    op.drop_table("approval_workflows")
