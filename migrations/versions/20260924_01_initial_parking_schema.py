"""Create the initial parking operational schema.

Revision ID: 20260924_01
Revises: None
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260924_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

space_type = postgresql.ENUM(
    "regular", "accessible", "ev", "motorcycle", name="space_type", create_type=False
)
space_status = postgresql.ENUM(
    "available", "occupied", "out_of_service", name="space_status", create_type=False
)
weekday = postgresql.ENUM(
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
    name="weekday",
    create_type=False,
)
pricing_unit = postgresql.ENUM(
    "per_hour", "daily_maximum", name="pricing_unit", create_type=False
)


def upgrade() -> None:
    """Create facilities, spaces, opening hours, and pricing rules."""
    bind = op.get_bind()
    for enum_type in (space_type, space_status, weekday, pricing_unit):
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "parking_facilities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("address", sa.String(length=500), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "parking_spaces",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("facility_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_number", sa.String(length=20), nullable=False),
        sa.Column("space_type", space_type, nullable=False),
        sa.Column("status", space_status, nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["facility_id"], ["parking_facilities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("facility_id", "space_number", name="uq_space_facility_number"),
    )
    op.create_table(
        "opening_hours",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("facility_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("weekday", weekday, nullable=False),
        sa.Column("opens_at", sa.Time(), nullable=True),
        sa.Column("closes_at", sa.Time(), nullable=True),
        sa.Column("is_closed", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "(is_closed AND opens_at IS NULL AND closes_at IS NULL) OR "
            "(NOT is_closed AND opens_at IS NOT NULL AND closes_at IS NOT NULL "
            "AND opens_at < closes_at)",
            name="ck_opening_hours_valid_interval",
        ),
        sa.ForeignKeyConstraint(["facility_id"], ["parking_facilities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("facility_id", "weekday", name="uq_opening_hours_facility_weekday"),
    )
    op.create_table(
        "pricing_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("facility_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("space_type", space_type, nullable=True),
        sa.Column("unit", pricing_unit, nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.CheckConstraint("amount >= 0", name="ck_pricing_rule_nonnegative_amount"),
        sa.CheckConstraint("duration_minutes > 0", name="ck_pricing_rule_positive_duration"),
        sa.CheckConstraint("char_length(currency) = 3", name="ck_pricing_rule_currency_length"),
        sa.ForeignKeyConstraint(["facility_id"], ["parking_facilities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("facility_id", "name", name="uq_pricing_rule_facility_name"),
    )


def downgrade() -> None:
    """Remove the initial parking operational schema."""
    op.drop_table("pricing_rules")
    op.drop_table("opening_hours")
    op.drop_table("parking_spaces")
    op.drop_table("parking_facilities")

    bind = op.get_bind()
    for enum_type in (pricing_unit, weekday, space_status, space_type):
        enum_type.drop(bind, checkfirst=True)

