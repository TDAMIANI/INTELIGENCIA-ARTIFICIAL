from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from mvtwin.api.common import DbSession, asset_id_by_code, subtree_ids
from mvtwin.auth import Role, User, actor_name, require
from mvtwin.models import Alarm, AlarmSeverity, AlarmStatus
from mvtwin.schemas import AlarmAck, AlarmOut

router = APIRouter(prefix="/api/v1/alarms", tags=["alarms"])


@router.get("", response_model=list[AlarmOut])
def list_alarms(
    session: DbSession,
    status_: AlarmStatus | None = Query(None, alias="status"),
    severity: AlarmSeverity | None = None,
    asset: str | None = Query(None, description="Filtra por activo, incluyendo sus descendientes"),
    limit: int = Query(100, ge=1, le=1000),
) -> list[AlarmOut]:
    query = select(Alarm).options(selectinload(Alarm.asset))
    if status_ is not None:
        query = query.where(Alarm.status == status_)
    if severity is not None:
        query = query.where(Alarm.severity == severity)
    if asset is not None:
        query = query.where(Alarm.asset_id.in_(subtree_ids(session, asset_id_by_code(session, asset))))
    alarms = session.scalars(query.order_by(Alarm.raised_at.desc(), Alarm.id.desc()).limit(limit))
    return [AlarmOut.from_model(a) for a in alarms]


@router.post("/{alarm_id}/ack", response_model=AlarmOut)
def acknowledge_alarm(
    alarm_id: int, payload: AlarmAck, session: DbSession, user: User = Depends(require(Role.operator))
) -> AlarmOut:
    """El operador confirma que vio la alarma. No la cierra: se cierra sola al normalizarse."""
    alarm = session.get(Alarm, alarm_id, options=[selectinload(Alarm.asset)])
    if alarm is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Alarma {alarm_id} no encontrada")
    if alarm.acknowledged_at is None:
        alarm.acknowledged_at = datetime.now(UTC)
        alarm.acknowledged_by = actor_name(user, payload.user)
        session.commit()
    return AlarmOut.from_model(alarm)
