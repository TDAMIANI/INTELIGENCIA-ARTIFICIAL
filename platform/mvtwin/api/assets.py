from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from mvtwin.api.common import DbSession
from mvtwin.auth import Role, require
from mvtwin.models import Asset
from mvtwin.schemas import AssetCreate, AssetDetail, AssetNode, AssetSummary, AssetUpdate

router = APIRouter(prefix="/api/v1/assets", tags=["assets"])


def _get_by_code(session: Session, code: str) -> Asset:
    asset = session.scalar(
        select(Asset)
        .where(Asset.code == code)
        .options(
            selectinload(Asset.children),
            selectinload(Asset.sensors),
            selectinload(Asset.failure_modes),
            selectinload(Asset.parent),
        )
    )
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Activo '{code}' no encontrado")
    return asset


def _to_detail(asset: Asset) -> AssetDetail:
    return AssetDetail(
        code=asset.code,
        name=asset.name,
        level=asset.level,
        health_index=asset.health_index,
        parent_code=asset.parent.code if asset.parent else None,
        attributes=asset.attributes,
        children=[AssetSummary.model_validate(c) for c in asset.children],
        sensors=asset.sensors,
        failure_modes=asset.failure_modes,
    )


@router.get("", response_model=list[AssetSummary])
def list_assets(session: DbSession, parent: str | None = None) -> list[Asset]:
    """Lista los activos raíz, o los hijos directos de `parent`."""
    if parent is None:
        query = select(Asset).where(Asset.parent_id.is_(None))
    else:
        query = select(Asset).where(Asset.parent_id == _get_by_code(session, parent).id)
    return list(session.scalars(query.order_by(Asset.code)))


@router.get("/tree", response_model=list[AssetNode])
def asset_tree(session: DbSession) -> list[AssetNode]:
    """Árbol completo de activos (una sola consulta, armado en memoria)."""
    assets = session.scalars(
        select(Asset).options(selectinload(Asset.sensors)).order_by(Asset.code)
    ).all()
    nodes = {
        a.id: AssetNode(
            code=a.code,
            name=a.name,
            level=a.level,
            health_index=a.health_index,
            sensor_count=len(a.sensors),
            children=[],
        )
        for a in assets
    }
    roots: list[AssetNode] = []
    for a in assets:
        if a.parent_id is None:
            roots.append(nodes[a.id])
        else:
            nodes[a.parent_id].children.append(nodes[a.id])
    return roots


@router.get("/{code}", response_model=AssetDetail)
def get_asset(code: str, session: DbSession) -> AssetDetail:
    return _to_detail(_get_by_code(session, code))


@router.post("", response_model=AssetDetail, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require(Role.engineer))])
def create_asset(payload: AssetCreate, session: DbSession) -> AssetDetail:
    if session.scalar(select(Asset.id).where(Asset.code == payload.code)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Ya existe el activo '{payload.code}'")
    parent = _get_by_code(session, payload.parent_code) if payload.parent_code else None
    asset = Asset(
        code=payload.code,
        name=payload.name,
        level=payload.level,
        parent=parent,
        attributes=payload.attributes,
    )
    session.add(asset)
    session.commit()
    return _to_detail(_get_by_code(session, asset.code))


@router.patch("/{code}", response_model=AssetDetail, dependencies=[Depends(require(Role.engineer))])
def update_asset(code: str, payload: AssetUpdate, session: DbSession) -> AssetDetail:
    asset = _get_by_code(session, code)
    if payload.name is not None:
        asset.name = payload.name
    if payload.attributes is not None:
        asset.attributes = payload.attributes
    session.commit()
    return _to_detail(_get_by_code(session, code))


@router.delete("/{code}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require(Role.engineer))])
def delete_asset(code: str, session: DbSession) -> Response:
    """Borra el activo y todo lo que cuelga de él (hijos, sensores, telemetría y alarmas)."""
    session.delete(_get_by_code(session, code))
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
