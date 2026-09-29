from datetime import datetime
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from mvtwin.api.common import DbSession, asset_id_by_code, subtree_ids
from mvtwin.models import Event, EventSeverity
from mvtwin.schemas import EventOut
from mvtwin.settings import settings

router = APIRouter(prefix="/api/v1/events", tags=["events"])


@lru_cache
def s3_client() -> Any:
    if not settings.s3_endpoint:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Almacenamiento S3 no configurado")
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name="us-east-1",
    )


@router.get("", response_model=list[EventOut])
def list_events(
    session: DbSession,
    asset: str | None = Query(None, description="Filtra por activo, incluyendo sus descendientes"),
    type_: str | None = Query(None, alias="type"),
    severity: EventSeverity | None = None,
    since: datetime | None = None,
    limit: int = Query(100, ge=1, le=1000),
) -> list[EventOut]:
    query = select(Event).options(selectinload(Event.asset), selectinload(Event.sensor))
    if asset is not None:
        query = query.where(Event.asset_id.in_(subtree_ids(session, asset_id_by_code(session, asset))))
    if type_ is not None:
        query = query.where(Event.type == type_)
    if severity is not None:
        query = query.where(Event.severity == severity)
    if since is not None:
        query = query.where(Event.ts >= since)
    events = session.scalars(query.order_by(Event.ts.desc(), Event.id.desc()).limit(limit))
    return [EventOut.from_model(e) for e in events]


@router.get(
    "/{event_id}/snapshot",
    responses={200: {"content": {"image/jpeg": {}}}},
    response_class=Response,
)
def get_snapshot(event_id: int, session: DbSession) -> Response:
    """Imagen de evidencia del evento (se lee de S3 a través de la API)."""
    event = session.get(Event, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Evento {event_id} no encontrado")
    if not event.snapshot_key or not event.snapshot_bucket:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "El evento no tiene imagen")
    try:
        obj = s3_client().get_object(Bucket=event.snapshot_bucket, Key=event.snapshot_key)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - errores de red o de S3
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"No se pudo leer la imagen: {exc}") from exc
    return Response(
        content=obj["Body"].read(),
        media_type=obj.get("ContentType", "image/jpeg"),
        headers={"Cache-Control": "private, max-age=86400"},  # la evidencia no cambia
    )
