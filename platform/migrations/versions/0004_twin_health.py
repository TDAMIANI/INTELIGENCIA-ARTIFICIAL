"""Gemelo digital: índice de salud, historial y recomendaciones

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-24
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _has_timescale() -> bool:
    bind = op.get_bind()
    return bind.dialect.name == "postgresql" and bind.execute(
        sa.text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
    ).scalar() is not None


def upgrade() -> None:
    op.add_column("assets", sa.Column("health_details", JSONType, nullable=False, server_default="{}"))
    op.alter_column("assets", "health_details", server_default=None)
    op.add_column("assets", sa.Column("health_updated_at", sa.DateTime(timezone=True)))

    op.create_table(
        "health_history",
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("health_index", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("asset_id", "ts"),
    )
    if _has_timescale():
        op.execute(
            "SELECT create_hypertable('health_history', 'ts', chunk_time_interval => INTERVAL '7 days',"
            " create_default_indexes => false)"
        )

    op.create_table(
        "recommendations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("failure_mode_id", sa.Integer(), sa.ForeignKey("failure_modes.id", ondelete="SET NULL")),
        sa.Column("indicator", sa.String(64), nullable=False),
        sa.Column("rule_code", sa.String(64), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column(
            "status",
            sa.Enum("open", "accepted", "dismissed", "done", name="recommendation_status",
                    native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("action", sa.String(300), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("due_by", sa.DateTime(timezone=True), nullable=False),
        sa.Column("condition_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(100)),
        sa.Column("note", sa.String(500)),
    )
    op.create_index("ix_recommendations_status", "recommendations", ["status"])
    op.create_index("ix_recommendations_asset_indicator", "recommendations", ["asset_id", "indicator"])


def downgrade() -> None:
    op.drop_table("recommendations")
    op.drop_table("health_history")
    op.drop_column("assets", "health_updated_at")
    op.drop_column("assets", "health_details")
