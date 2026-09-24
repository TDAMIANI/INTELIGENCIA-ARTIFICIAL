import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from mvtwin import __version__
from mvtwin.api import alarms, assets, events, live, telemetry, twin
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
app.include_router(assets.router)
app.include_router(telemetry.router)
app.include_router(alarms.router)
app.include_router(events.router)
app.include_router(twin.router)
app.include_router(live.router)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
