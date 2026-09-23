from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, literal_column, select, text
from sqlalchemy.orm import Session

from mvtwin.api.common import DbSession, asset_id_by_code, subtree_ids
from mvtwin.models import Asset, Telemetry
from mvtwin.schemas import LatestValue, TelemetryPoint, TelemetrySeries

router = APIRouter(prefix="/api/v1/telemetry", tags=["telemetry"])

MAX_RANGE = timedelta(days=90)


def _as_utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts


@router.get("", response_model=TelemetrySeries)
def get_series(
    session: DbSession,
    asset: str,
    metric: str,
    start: datetime | None = None,
    end: datetime | None = None,
    bucket: int | None = Query(None, ge=1, le=86400, description="Agrega por intervalos de N segundos"),
    limit: int = Query(5000, ge=1, le=50000),
) -> TelemetrySeries:
    """Serie de tiempo de una métrica. Por defecto, la última hora.

    Sin `bucket` devuelve las mediciones crudas; con `bucket` devuelve promedio, mínimo
    y máximo por intervalo (lo que conviene para gráficos de días o semanas).
    """
    end = _as_utc(end) if end else datetime.now(UTC)
    start = _as_utc(start) if start else end - timedelta(hours=1)
    if start >= end:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "start debe ser anterior a end")
    if end - start > MAX_RANGE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"El rango máximo es {MAX_RANGE.days} días")

    asset_id = asset_id_by_code(session, asset)
    where = (Telemetry.asset_id == asset_id, Telemetry.metric == metric, Telemetry.ts >= start, Telemetry.ts < end)

    if bucket is None:
        rows = session.execute(
            select(Telemetry.ts, Telemetry.value).where(*where).order_by(Telemetry.ts).limit(limit)
        ).all()
        points = [TelemetryPoint(ts=_as_utc(ts), value=v) for ts, v in rows]
    elif session.get_bind().dialect.name == "postgresql":
        points = _bucketed_sql(session, where, bucket, limit)
    else:
        points = _bucketed_python(session, where, bucket, limit)
    return TelemetrySeries(asset_code=asset, metric=metric, bucket_s=bucket, points=points)


def _bucketed_sql(session: Session, where: tuple, bucket: int, limit: int) -> list[TelemetryPoint]:
    # date_bin es PostgreSQL estándar (>= 14); en TimescaleDB equivale a time_bucket.
    bucket_ts = func.date_bin(
        text(f"INTERVAL '{int(bucket)} seconds'"), Telemetry.ts, literal_column("TIMESTAMPTZ '1970-01-01 00:00:00+00'")
    ).label("bucket")
    rows = session.execute(
        select(
            bucket_ts,
            func.avg(Telemetry.value),
            func.min(Telemetry.value),
            func.max(Telemetry.value),
            func.count(),
        )
        .where(*where)
        .group_by(bucket_ts)
        .order_by(bucket_ts)
        .limit(limit)
    ).all()
    return [TelemetryPoint(ts=ts, value=avg, min=lo, max=hi, count=n) for ts, avg, lo, hi, n in rows]


def _bucketed_python(session: Session, where: tuple, bucket: int, limit: int) -> list[TelemetryPoint]:
    """Misma agregación que _bucketed_sql, para bases sin date_bin (SQLite en tests)."""
    groups: dict[int, list[float]] = {}
    for ts, value in session.execute(select(Telemetry.ts, Telemetry.value).where(*where)).all():
        key = int(_as_utc(ts).timestamp()) // bucket * bucket
        groups.setdefault(key, []).append(value)
    return [
        TelemetryPoint(
            ts=datetime.fromtimestamp(key, UTC), value=sum(v) / len(v), min=min(v), max=max(v), count=len(v)
        )
        for key, v in sorted(groups.items())[:limit]
    ]


@router.get("/latest", response_model=list[LatestValue])
def get_latest(
    session: DbSession,
    asset: str,
    subtree: bool = Query(False, description="Incluir los activos hijos (p. ej. todos los polines)"),
    max_age_s: int = Query(86400, ge=1, le=90 * 86400, description="Ignorar valores más viejos que esto"),
) -> list[LatestValue]:
    """Último valor de cada métrica del activo (para tarjetas y la vista del gemelo)."""
    root_id = asset_id_by_code(session, asset)
    ids = subtree_ids(session, root_id) if subtree else [root_id]
    since = datetime.now(UTC) - timedelta(seconds=max_age_s)
    last = (
        select(Telemetry.asset_id, Telemetry.metric, func.max(Telemetry.ts).label("ts"))
        .where(Telemetry.asset_id.in_(ids), Telemetry.ts >= since)
        .group_by(Telemetry.asset_id, Telemetry.metric)
        .subquery()
    )
    rows = session.execute(
        select(Asset.code, Telemetry.metric, Telemetry.ts, func.max(Telemetry.value))
        .join(
            last,
            (Telemetry.asset_id == last.c.asset_id)
            & (Telemetry.metric == last.c.metric)
            & (Telemetry.ts == last.c.ts),
        )
        .join(Asset, Asset.id == Telemetry.asset_id)
        .group_by(Asset.code, Telemetry.metric, Telemetry.ts)
        .order_by(Asset.code, Telemetry.metric)
    ).all()
    return [LatestValue(asset_code=c, metric=m, ts=_as_utc(ts), value=v) for c, m, ts, v in rows]
