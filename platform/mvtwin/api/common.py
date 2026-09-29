from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from mvtwin.db import get_session
from mvtwin.models import Asset

DbSession = Annotated[Session, Depends(get_session)]


def asset_id_by_code(session: Session, code: str) -> int:
    asset_id = session.scalar(select(Asset.id).where(Asset.code == code))
    if asset_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Activo '{code}' no encontrado")
    return asset_id


def subtree_ids(session: Session, root_id: int) -> list[int]:
    """IDs del activo y todos sus descendientes.

    La jerarquía completa de una planta son miles de filas como mucho, así que
    se arma en memoria en lugar de usar una CTE recursiva.
    """
    children: dict[int, list[int]] = {}
    for asset_id, parent_id in session.execute(select(Asset.id, Asset.parent_id)).tuples():
        if parent_id is not None:
            children.setdefault(parent_id, []).append(asset_id)
    ids, stack = [], [root_id]
    while stack:
        current = stack.pop()
        ids.append(current)
        stack.extend(children.get(current, []))
    return ids
