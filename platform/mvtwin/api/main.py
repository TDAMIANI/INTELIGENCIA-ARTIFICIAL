from fastapi import FastAPI

from mvtwin import __version__
from mvtwin.api import assets

app = FastAPI(
    title="MineVision Twin API",
    version=__version__,
    description="API del gemelo digital para mantenimiento predictivo en plantas mineras.",
)
app.include_router(assets.router)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
