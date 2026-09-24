"""Referencia a la orden de trabajo del CMMS y políticas de retención de TimescaleDB

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-24
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_timescale() -> bool:
    bind = op.get_bind()
    return bind.dialect.name == "postgresql" and bind.execute(
        sa.text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
    ).scalar() is not None


def upgrade() -> None:
    op.add_column("recommendations", sa.Column("work_order_ref", sa.String(100)))
    if _has_timescale():
        # Telemetría: se comprime a los 7 días (≈10x menos disco) y se borra al año.
        # El historial de salud se conserva 3 años (sirve para estudios de confiabilidad).
        op.execute(
            "ALTER TABLE telemetry SET (timescaledb.compress, "
            "timescaledb.compress_segmentby = 'asset_id, metric', timescaledb.compress_orderby = 'ts DESC')"
        )
        op.execute("SELECT add_compression_policy('telemetry', INTERVAL '7 days', if_not_exists => true)")
        op.execute("SELECT add_retention_policy('telemetry', INTERVAL '365 days', if_not_exists => true)")
        op.execute("SELECT add_retention_policy('health_history', INTERVAL '3 years', if_not_exists => true)")


def downgrade() -> None:
    if _has_timescale():
        op.execute("SELECT remove_retention_policy('health_history', if_exists => true)")
        op.execute("SELECT remove_retention_policy('telemetry', if_exists => true)")
        op.execute("SELECT remove_compression_policy('telemetry', if_exists => true)")
    op.drop_column("recommendations", "work_order_ref")
