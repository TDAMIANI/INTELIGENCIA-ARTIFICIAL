"""Medición del desalineamiento de la banda con visión clásica (sin deep learning).

La cámara mira la banda desde arriba y la banda corre en sentido vertical en la imagen.
La goma es más oscura que la estructura, así que en cada fila se buscan los bordes:
- borde izquierdo: primer salto fuerte de claro a oscuro, recorriendo desde la izquierda;
- borde derecho: primer salto fuerte de oscuro a claro, recorriendo desde la derecha.
Recorrer desde afuera hacia adentro evita confundir el borde con el material transportado.
El desplazamiento es la distancia entre el centro medido y el centro de referencia.
"""

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class BeltConfig:
    mm_per_px: float = 3.0
    ref_center_px: float = 320.0
    row_start: int = 20
    row_end: int = 340
    row_step: int = 8
    min_gradient: float = 40.0
    # Fracción mínima de filas donde se encontraron ambos bordes para confiar en la medición.
    min_valid_rows: float = 0.5


@dataclass(frozen=True)
class BeltMeasurement:
    detected: bool
    offset_mm: float | None = None
    width_mm: float | None = None
    left_px: float | None = None
    right_px: float | None = None
    valid_rows: float = 0.0


def measure_belt(frame_bgr: np.ndarray, cfg: BeltConfig = BeltConfig()) -> BeltMeasurement:
    gray = cv2.GaussianBlur(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY), (5, 5), 0).astype(np.float32)
    grad = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3) / 4.0  # derivada horizontal ~ salto de intensidad
    rows = range(max(0, cfg.row_start), min(gray.shape[0], cfg.row_end), cfg.row_step)

    lefts, rights = [], []
    for y in rows:
        g = grad[y]
        falling = np.flatnonzero(g <= -cfg.min_gradient)  # claro -> oscuro
        rising = np.flatnonzero(g >= cfg.min_gradient)  # oscuro -> claro
        if falling.size and rising.size and rising[-1] > falling[0]:
            lefts.append(_refine(g, falling[0], -1))
            rights.append(_refine(g, rising[-1], +1))

    valid = len(lefts) / max(1, len(rows))
    if valid < cfg.min_valid_rows:
        return BeltMeasurement(detected=False, valid_rows=valid)
    left, right = float(np.median(lefts)), float(np.median(rights))
    center = (left + right) / 2
    return BeltMeasurement(
        detected=True,
        offset_mm=round((center - cfg.ref_center_px) * cfg.mm_per_px, 1),
        width_mm=round((right - left) * cfg.mm_per_px, 1),
        left_px=round(left, 1),
        right_px=round(right, 1),
        valid_rows=round(valid, 2),
    )


def _refine(g: np.ndarray, idx: int, sign: int) -> float:
    """Ubica el máximo del salto cerca del cruce del umbral, con precisión sub-píxel.

    sign = -1 para bordes claro->oscuro (gradiente negativo), +1 para oscuro->claro.
    """
    s = sign * g
    lo, hi = max(0, idx - 3), min(len(g), idx + 4)
    peak = lo + int(np.argmax(s[lo:hi]))
    if 0 < peak < len(g) - 1:
        a, b, c = s[peak - 1], s[peak], s[peak + 1]
        denom = a - 2 * b + c
        if denom != 0:
            return peak + 0.5 * (a - c) / denom
    return float(peak)
