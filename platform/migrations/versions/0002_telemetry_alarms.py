"""Telemetría (hypertable de TimescaleDB) y alarmas

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-24
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timescale_available() -> bool:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return False
    return bind.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
    ).scalar() is not None


def upgrade() -> None:
    op.create_table(
        "telemetry",
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sensor_id", sa.Integer(), sa.ForeignKey("sensors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("asset_id", "metric", "ts", "sensor_id"),
    )
    # Con TimescaleDB, la tabla se particiona por tiempo (chunks de 1 día).
    # En PostgreSQL sin la extensión (tests, CI simple) queda como tabla común.
    if _timescale_available():
        op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
        op.execute(
            "SELECT create_hypertable('telemetry', 'ts', chunk_time_interval => INTERVAL '1 day',"
            " create_default_indexes => false)"
        )

    op.create_table(
        "alarms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sensor_id", sa.Integer(), sa.ForeignKey("sensors.id", ondelete="SET NULL")),
        sa.Column("rule_code", sa.String(64), nullable=False),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column(
            "severity",
            sa.Enum("warning", "critical", name="alarm_severity", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("active", "cleared", name="alarm_status", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("message", sa.String(300), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("peak_value", sa.Float(), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("raised_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cleared_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_by", sa.String(100)),
    )
    op.create_index("ix_alarms_asset_id", "alarms", ["asset_id"])
    op.create_index("ix_alarms_status_raised_at", "alarms", ["status", "raised_at"])


def downgrade() -> None:
    op.drop_table("alarms")
    op.drop_table("telemetry")
