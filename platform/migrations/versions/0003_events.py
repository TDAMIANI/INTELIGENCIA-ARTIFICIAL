"""Eventos del edge con imagen de evidencia

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-24
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sensor_id", sa.Integer(), sa.ForeignKey("sensors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column(
            "severity",
            sa.Enum("info", "warning", "critical", name="event_severity", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("message", sa.String(300), nullable=False),
        sa.Column("value", sa.Float()),
        sa.Column("snapshot_bucket", sa.String(100)),
        sa.Column("snapshot_key", sa.String(500)),
        sa.Column("data", JSONType, nullable=False),
    )
    op.create_index("ix_events_ts", "events", ["ts"])
    op.create_index("ix_events_asset_ts", "events", ["asset_id", "ts"])


def downgrade() -> None:
    op.drop_table("events")
