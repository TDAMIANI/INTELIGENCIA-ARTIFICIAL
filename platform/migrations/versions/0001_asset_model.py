"""Modelo de activos: assets, sensors, failure_modes

Revision ID: 0001
Revises:
Create Date: 2026-09-23
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, length=32)


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "level",
            _enum("asset_level", "plant", "area", "system", "subunit", "component"),
            nullable=False,
        ),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("assets.id", ondelete="CASCADE")),
        sa.Column("attributes", JSONType, nullable=False),
        sa.Column("health_index", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_assets_code", "assets", ["code"], unique=True)
    op.create_index("ix_assets_parent_id", "assets", ["parent_id"])

    op.create_table(
        "sensors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column(
            "asset_id", sa.Integer(), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "type",
            _enum("sensor_type", "rgb_camera", "thermal_camera", "vibration", "temperature", "plc_tags"),
            nullable=False,
        ),
        sa.Column("source", _enum("sensor_source", "edge", "plc", "manual"), nullable=False),
        sa.Column("config", JSONType, nullable=False),
    )
    op.create_index("ix_sensors_code", "sensors", ["code"], unique=True)
    op.create_index("ix_sensors_asset_id", "sensors", ["asset_id"])

    op.create_table(
        "failure_modes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "asset_id", sa.Integer(), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("criticality", sa.Integer(), nullable=False),
        sa.Column("detection", sa.String(200)),
        sa.UniqueConstraint("asset_id", "code"),
    )
    op.create_index("ix_failure_modes_asset_id", "failure_modes", ["asset_id"])


def downgrade() -> None:
    op.drop_table("failure_modes")
    op.drop_table("sensors")
    op.drop_table("assets")
