from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from mvtwin.auth import User, authenticate, current_user, issue_token
from mvtwin.settings import settings

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


@router.get("/config")
def auth_config() -> dict:
    """Lo consulta el tablero para saber si mostrar el inicio de sesión."""
    return {"enabled": settings.auth_enabled, "mode": settings.auth_mode}


@router.post("/login")
def login(payload: LoginIn) -> dict:
    if not settings.auth_enabled or settings.auth_mode != "local":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El inicio de sesión local no está habilitado")
    user = authenticate(payload.username, payload.password)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuario o contraseña incorrectos")
    return {"access_token": issue_token(user), "token_type": "bearer", "user": user.to_dict()}


@router.get("/me")
def me(user: User = Depends(current_user)) -> dict:
    return user.to_dict()
