import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from mvtwin import __version__
from mvtwin.api import alarms, assets, auth, events, live, telemetry, twin
from mvtwin.auth import current_user
from mvtwin.settings import settings

log = logging.getLogger("mvtwin.api")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    bridge = None
    if settings.mqtt_host:
        bridge = live.start_mqtt_bridge(settings.mqtt_host, settings.mqtt_port)
    else:
        log.warning("MVT_MQTT_HOST no definido: los WebSockets en vivo no recibirán datos")
    yield
    if bridge is not None:
        bridge.loop_stop()
        bridge.disconnect()


app = FastAPI(
    title="MineVision Twin API",
    version=__version__,
    description="API del gemelo digital para mantenimiento predictivo en plantas mineras.",
    lifespan=lifespan,
)
# Todas las rutas de datos exigen sesión (si la autenticación está activada).
protected = [Depends(current_user)]
app.include_router(auth.router)
app.include_router(assets.router, dependencies=protected)
app.include_router(telemetry.router, dependencies=protected)
app.include_router(alarms.router, dependencies=protected)
app.include_router(events.router, dependencies=protected)
app.include_router(twin.router, dependencies=protected)
app.include_router(live.router)  # los WebSockets validan el token por su cuenta


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
