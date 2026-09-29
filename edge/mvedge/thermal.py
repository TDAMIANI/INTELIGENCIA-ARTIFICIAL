"""Análisis de termografía radiométrica: temperatura máxima por polín y puntos calientes.

La cámara entrega una imagen donde cada píxel es temperatura. Para cada polín se define
una región de interés (ROI) y se toma la temperatura máxima dentro de ella: es donde se
ve el rodamiento cuando empieza a fallar.

Un punto caliente se detecta comparando cada polín con la mediana de sus vecinos
(ΔT relativo): así el sol, la hora del día o la estación no generan falsas alarmas.
"""

import statistics
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np


@dataclass(frozen=True)
class Roi:
    code: str  # código del activo (p. ej. CV-201-IDL-12)
    x: int
    y: int
    w: int
    h: int


def rois_from_config(cfg: dict[str, Any]) -> list[Roi]:
    """Acepta una lista explícita (`items`) o una grilla regular (`grid`)."""
    rois = [Roi(r["code"], int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"])) for r in cfg.get("items", [])]
    grid = cfg.get("grid")
    if grid:
        n = int(grid.get("start", 1))
        for row in range(int(grid["rows"])):
            for col in range(int(grid["cols"])):
                rois.append(
                    Roi(
                        code=grid["code"].format(n=n),
                        x=int(grid["x0"]) + col * int(grid["cell_w"]),
                        y=int(grid["y0"]) + row * int(grid["cell_h"]),
                        w=int(grid["cell_w"]),
                        h=int(grid["cell_h"]),
                    )
                )
                n += 1
    if len({r.code for r in rois}) != len(rois):
        raise ValueError("Hay ROIs con el mismo código")
    return rois


def decode_radiometric(raw: np.ndarray, scale: float = 0.01, offset_c: float = -273.15) -> np.ndarray:
    """Convierte la imagen cruda (p. ej. centésimas de Kelvin) a °C."""
    if raw.dtype != np.uint16:
        raise ValueError(f"Se esperaba una imagen radiométrica uint16, llegó {raw.dtype}")
    return raw.astype(np.float32) * scale + offset_c


def max_temp_per_roi(temps_c: np.ndarray, rois: list[Roi]) -> dict[str, float]:
    h, w = temps_c.shape
    out: dict[str, float] = {}
    for r in rois:
        patch = temps_c[max(0, r.y) : min(h, r.y + r.h), max(0, r.x) : min(w, r.x + r.w)]
        if patch.size:
            out[r.code] = round(float(patch.max()), 1)
    return out


def delta_vs_neighbours(values: dict[str, float], neighbours: int = 3) -> dict[str, float]:
    """ΔT de cada activo contra la mediana de sus vecinos (ordenados por código)."""
    codes = sorted(values)
    out = {}
    for i, code in enumerate(codes):
        near = [values[c] for c in codes[max(0, i - neighbours) : i] + codes[i + 1 : i + neighbours + 1]]
        if near:
            out[code] = round(values[code] - statistics.median(near), 1)
    return out


@dataclass
class HotspotDetector:
    """Confirma un punto caliente tras `confirm` imágenes seguidas sobre el umbral."""

    delta_c: float = 15.0
    confirm: int = 3
    neighbours: int = 3
    _streak: dict[str, int] = field(default_factory=dict)

    def update(self, max_temps: dict[str, float]) -> list[tuple[str, float]]:
        """Devuelve [(activo, ΔT)] de los puntos calientes confirmados en esta imagen."""
        deltas = delta_vs_neighbours(max_temps, self.neighbours)
        confirmed = []
        for code, d in deltas.items():
            self._streak[code] = self._streak.get(code, 0) + 1 if d >= self.delta_c else 0
            if self._streak[code] >= self.confirm:
                confirmed.append((code, d))
        return confirmed


def render_snapshot(
    temps_c: np.ndarray,
    rois: list[Roi],
    max_temps: dict[str, float],
    highlight: set[str] = frozenset(),
    upscale: int = 3,
) -> np.ndarray:
    """Imagen de evidencia: paleta de colores, ROIs y temperatura de los polines resaltados."""
    lo, hi = float(np.percentile(temps_c, 1)), float(temps_c.max())
    norm = np.clip((temps_c - lo) / max(hi - lo, 1e-3) * 255, 0, 255).astype(np.uint8)
    img = cv2.applyColorMap(norm, cv2.COLORMAP_INFERNO)
    img = cv2.resize(img, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_NEAREST)
    for r in rois:
        hot = r.code in highlight
        color = (255, 255, 255) if hot else (90, 90, 90)
        p1, p2 = (r.x * upscale, r.y * upscale), ((r.x + r.w) * upscale - 1, (r.y + r.h) * upscale - 1)
        cv2.rectangle(img, p1, p2, color, 2 if hot else 1)
        if hot and r.code in max_temps:
            label = f"{r.code.rsplit('-', 1)[-1]}: {max_temps[r.code]:.0f}C"
            cv2.putText(img, label, (p1[0] + 2, p1[1] + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
    cv2.putText(img, f"{lo:.0f}-{hi:.0f} C", (6, img.shape[0] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                (255, 255, 255), 1, cv2.LINE_AA)
    return img
