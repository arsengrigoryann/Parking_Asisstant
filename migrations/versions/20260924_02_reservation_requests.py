"""Add durable reservation requests.

Revision ID: 20260924_02
Revises: 20260924_01
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260924_02"
down_revision: str | None = "20260924_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

reservation_status = postgresql.ENUM(
    "PENDING_APPROVAL",
    "APPROVED",
    "REJECTED",
    "CANCELLED",
    name="reservation_request_status",
    create_type=False,
)


def upgrade() -> None:
    """Create the private transactional reservation-request table."""
    reservation_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "reservation_requests",
        sa.Column("reservation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("first_name", sa.String(length=100), nullable=False),
        sa.Column("last_name", sa.String(length=100), nullable=False),
        sa.Column("car_number", sa.String(length=12), nullable=False),
        sa.Column("start_datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("facility_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", reservation_status, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("decision_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_by", sa.String(length=100), nullable=True),
        sa.Column("rejection_reason", sa.String(length=500), nullable=True),
        sa.Column("idempotency_digest", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "end_datetime > start_datetime", name="ck_reservation_valid_interval"
        ),
        sa.CheckConstraint(
            "(status = 'PENDING_APPROVAL' AND decision_at IS NULL AND decision_by IS NULL) OR "
            "(status <> 'PENDING_APPROVAL' AND decision_at IS NOT NULL "
            "AND decision_by IS NOT NULL)",
            name="ck_reservation_decision_metadata",
        ),
        sa.CheckConstraint(
            "status = 'REJECTED' OR rejection_reason IS NULL",
            name="ck_reservation_reason_only_for_rejection",
        ),
        sa.ForeignKeyConstraint(
            ["facility_id"], ["parking_facilities.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("reservation_id"),
        sa.UniqueConstraint("idempotency_digest"),
    )
    op.create_index(
        "ix_reservation_requests_facility_id",
        "reservation_requests",
        ["facility_id"],
    )
    op.create_index(
        "ix_reservation_requests_status", "reservation_requests", ["status"]
    )


def downgrade() -> None:
    """Remove durable reservation requests."""
    op.drop_index("ix_reservation_requests_status", table_name="reservation_requests")
    op.drop_index("ix_reservation_requests_facility_id", table_name="reservation_requests")
    op.drop_table("reservation_requests")
    reservation_status.drop(op.get_bind(), checkfirst=True)
