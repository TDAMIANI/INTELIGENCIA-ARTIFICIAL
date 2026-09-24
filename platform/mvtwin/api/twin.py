from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import case, select
from sqlalchemy.orm import selectinload

from mvtwin.api.common import DbSession, asset_id_by_code, subtree_ids
from mvtwin.models import Asset, HealthHistory, Recommendation, RecommendationStatus
from mvtwin.queries import as_utc
from mvtwin.schemas import AssetSummary, HealthOut, HealthPoint, RecommendationOut, RecommendationUpdate

router = APIRouter(prefix="/api/v1", tags=["twin"])

PRIORITY_ORDER = case(
    {"urgent": 0, "high": 1, "medium": 2, "low": 3}, value=Recommendation.priority, else_=4
)


@router.get("/assets/{code}/health", response_model=HealthOut)
def get_health(code: str, session: DbSession) -> HealthOut:
    """Índice de salud del activo con su explicación (indicador por indicador) y el de sus hijos."""
    asset = session.scalar(select(Asset).where(Asset.code == code).options(selectinload(Asset.children)))
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Activo '{code}' no encontrado")
    details = asset.health_details or {}
    children = sorted(asset.children, key=lambda c: (c.health_index is None, c.health_index or 0, c.code))
    return HealthOut(
        asset_code=asset.code,
        asset_name=asset.name,
        health_index=asset.health_index,
        updated_at=as_utc(asset.health_updated_at) if asset.health_updated_at else None,
        own_health_index=details.get("own_health_index"),
        worst_child=details.get("worst_child"),
        indicators=details.get("indicators", []),
        children=[AssetSummary.model_validate(c) for c in children],
    )


@router.get("/assets/{code}/health/history", response_model=list[HealthPoint])
def get_health_history(
    code: str,
    session: DbSession,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(5000, ge=1, le=50000),
) -> list[HealthPoint]:
    end = as_utc(end) if end else datetime.now(UTC)
    start = as_utc(start) if start else end - timedelta(hours=24)
    rows = session.execute(
        select(HealthHistory.ts, HealthHistory.health_index)
        .where(HealthHistory.asset_id == asset_id_by_code(session, code),
               HealthHistory.ts >= start, HealthHistory.ts < end)
        .order_by(HealthHistory.ts)
        .limit(limit)
    ).all()
    return [HealthPoint(ts=as_utc(ts), health_index=v) for ts, v in rows]


@router.get("/recommendations", response_model=list[RecommendationOut])
def list_recommendations(
    session: DbSession,
    status_: list[RecommendationStatus] | None = Query(None, alias="status"),
    asset: str | None = Query(None, description="Filtra por activo, incluyendo sus descendientes"),
    limit: int = Query(200, ge=1, le=1000),
) -> list[RecommendationOut]:
    """Recomendaciones ordenadas por prioridad y plazo. Por defecto, las pendientes (open y accepted)."""
    statuses = status_ or [RecommendationStatus.open, RecommendationStatus.accepted]
    query = (
        select(Recommendation)
        .options(selectinload(Recommendation.asset), selectinload(Recommendation.failure_mode))
        .where(Recommendation.status.in_(statuses))
    )
    if asset is not None:
        query = query.where(Recommendation.asset_id.in_(subtree_ids(session, asset_id_by_code(session, asset))))
    recs = session.scalars(query.order_by(PRIORITY_ORDER, Recommendation.due_by).limit(limit))
    return [RecommendationOut.from_model(r) for r in recs]


@router.patch("/recommendations/{rec_id}", response_model=RecommendationOut)
def update_recommendation(rec_id: int, payload: RecommendationUpdate, session: DbSession) -> RecommendationOut:
    """El planificador acepta (genera la OT), descarta o cierra la recomendación."""
    rec = session.get(
        Recommendation, rec_id,
        options=[selectinload(Recommendation.asset), selectinload(Recommendation.failure_mode)],
    )
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Recomendación {rec_id} no encontrada")
    rec.status = payload.status
    rec.updated_by = payload.user
    rec.updated_at = datetime.now(UTC)
    if payload.note is not None:
        rec.note = payload.note
    session.commit()
    return RecommendationOut.from_model(rec)
