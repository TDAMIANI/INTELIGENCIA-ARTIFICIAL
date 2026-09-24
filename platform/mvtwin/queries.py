"""Consultas de telemetría reutilizables (PostgreSQL/TimescaleDB, con alternativa para SQLite)."""

from datetime import UTC, datetime

from sqlalchemy import func, literal_column, select, text
from sqlalchemy.orm import Session

from mvtwin.models import Telemetry


def as_utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts


def window_means(session: Session, sensor_id: int, metric: str, start: datetime, end: datetime) -> dict[int, float]:
    """Promedio por activo de una métrica de un sensor en [start, end)."""
    rows = session.execute(
        select(Telemetry.asset_id, func.avg(Telemetry.value))
        .where(Telemetry.sensor_id == sensor_id, Telemetry.metric == metric,
               Telemetry.ts >= start, Telemetry.ts < end)
        .group_by(Telemetry.asset_id)
    ).all()
    return {asset_id: float(avg) for asset_id, avg in rows}


def bucketed_means(
    session: Session, sensor_id: int, metric: str, start: datetime, end: datetime, bucket_s: int
) -> dict[int, list[tuple[datetime, float]]]:
    """Serie promediada por intervalos de `bucket_s` segundos, por activo."""
    where = (Telemetry.sensor_id == sensor_id, Telemetry.metric == metric, Telemetry.ts >= start, Telemetry.ts < end)
    out: dict[int, list[tuple[datetime, float]]] = {}
    if session.get_bind().dialect.name == "postgresql":
        bucket = func.date_bin(
            text(f"INTERVAL '{int(bucket_s)} seconds'"), Telemetry.ts,
            literal_column("TIMESTAMPTZ '1970-01-01 00:00:00+00'"),
        ).label("bucket")
        rows = session.execute(
            select(Telemetry.asset_id, bucket, func.avg(Telemetry.value))
            .where(*where).group_by(Telemetry.asset_id, bucket).order_by(Telemetry.asset_id, bucket)
        ).all()
        for asset_id, ts, avg in rows:
            out.setdefault(asset_id, []).append((as_utc(ts), float(avg)))
        return out

    groups: dict[tuple[int, int], list[float]] = {}
    for asset_id, ts, value in session.execute(select(Telemetry.asset_id, Telemetry.ts, Telemetry.value).where(*where)):
        key = (asset_id, int(as_utc(ts).timestamp()) // bucket_s * bucket_s)
        groups.setdefault(key, []).append(value)
    for (asset_id, epoch), values in sorted(groups.items()):
        out.setdefault(asset_id, []).append((datetime.fromtimestamp(epoch, UTC), sum(values) / len(values)))
    return out
