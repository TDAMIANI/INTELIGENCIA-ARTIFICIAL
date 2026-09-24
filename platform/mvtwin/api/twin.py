from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import case, select
from sqlalchemy.orm import selectinload

from mvtwin.api.common import DbSession, asset_id_by_code, subtree_ids
from mvtwin.auth import Role, User, actor_name, require
from mvtwin.integrations import cmms
from mvtwin.models import Asset, HealthHistory, Recommendation, RecommendationStatus
from mvtwin.queries import as_utc
from mvtwin.schemas import AssetSummary, HealthOut, HealthPoint, RecommendationOut, RecommendationUpdate
from mvtwin.settings import settings

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
def update_recommendation(
    rec_id: int, payload: RecommendationUpdate, session: DbSession, user: User = Depends(require(Role.planner))
) -> RecommendationOut:
    """El planificador acepta (genera la OT), descarta o cierra la recomendación.

    Si hay un CMMS configurado, aceptar crea la orden allí; si el CMMS falla, la
    recomendación no cambia de estado (502) para que no quede "planificada" sin orden.
    """
    rec = session.get(
        Recommendation, rec_id,
        options=[selectinload(Recommendation.asset), selectinload(Recommendation.failure_mode)],
    )
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Recomendación {rec_id} no encontrada")
    rec.status = payload.status
    rec.updated_by = actor_name(user, payload.user)
    rec.updated_at = datetime.now(UTC)
    if payload.note is not None:
        rec.note = payload.note
    if payload.status is RecommendationStatus.accepted and settings.cmms_webhook_url and not rec.work_order_ref:
        try:
            rec.work_order_ref = cmms.send_work_order(rec, settings.cmms_webhook_url, cmms.http_json_sender)
        except cmms.CmmsError as exc:
            session.rollback()
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    session.commit()
    return RecommendationOut.from_model(rec)


@router.get(
    "/work-orders/export.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}},
    dependencies=[Depends(require(Role.planner))],
)
def export_work_orders(
    session: DbSession,
    status_: list[RecommendationStatus] | None = Query(None, alias="status"),
) -> Response:
    """Órdenes de trabajo en CSV para cargar en el CMMS. Por defecto, las recomendaciones planificadas."""
    statuses = status_ or [RecommendationStatus.accepted]
    recs = session.scalars(
        select(Recommendation)
        .options(selectinload(Recommendation.asset), selectinload(Recommendation.failure_mode))
        .where(Recommendation.status.in_(statuses))
        .order_by(PRIORITY_ORDER, Recommendation.due_by)
    ).all()
    filename = f"ordenes-de-trabajo-{datetime.now(UTC):%Y%m%d-%H%M}.csv"
    return Response(
        content=cmms.to_csv(list(recs)),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
