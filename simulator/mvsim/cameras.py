"""Cámaras virtuales: renderizan imágenes a partir del estado del simulador.

Sirven para desarrollar y probar el pipeline de visión del edge sin cámaras reales:

- Térmica radiométrica: imagen de 16 bits donde cada píxel es temperatura en
  centésimas de Kelvin (formato "TLinear" que usan muchas cámaras FLIR).
  Muestra los 40 polines en una grilla de 2 filas x 20 columnas; un rodamiento
  trabado calienta los extremos del rodillo.
- RGB: vista superior de la banda (corre en sentido vertical en la imagen) con
  material encima. El desalineamiento corre la banda hacia un costado.

La geometría (grilla de polines, escala mm/píxel) debe coincidir con edge/config/edge.yaml.
"""

from dataclasses import dataclass

import cv2
import numpy as np

from mvsim.conveyor import ConveyorSimulator, Snapshot


@dataclass(frozen=True)
class ThermalLayout:
    width: int = 320
    height: int = 240
    rows: int = 2
    cols: int = 20
    x0: int = 0
    y0: int = 40
    cell_w: int = 16
    cell_h: int = 80
    roller_w: int = 12
    roller_h: int = 6
    bearing_w: int = 2


@dataclass(frozen=True)
class RgbLayout:
    width: int = 640
    height: int = 360
    mm_per_px: float = 3.0
    belt_width_mm: float = 1200.0


def kelvin_centi(temp_c: np.ndarray) -> np.ndarray:
    return np.clip(np.round((temp_c + 273.15) * 100.0), 0, 65535).astype(np.uint16)


def render_thermal(
    sim: ConveyorSimulator, snap: Snapshot, rng: np.random.Generator, layout: ThermalLayout = ThermalLayout()
) -> np.ndarray:
    """Devuelve una imagen radiométrica uint16 (centésimas de Kelvin)."""
    ambient = sim.config.ambient_c
    img = np.full((layout.height, layout.width), ambient, dtype=np.float32)
    img += np.linspace(0, 2.0, layout.height, dtype=np.float32)[:, None]  # gradiente suave (sol / estructura)

    for i, temp in enumerate(snap.idler_temps_c):
        row, col = divmod(i, layout.cols)
        if row >= layout.rows:
            break
        cx = layout.x0 + col * layout.cell_w + layout.cell_w // 2
        cy = layout.y0 + row * layout.cell_h + layout.cell_h // 2
        x1, x2 = cx - layout.roller_w // 2, cx + layout.roller_w // 2
        y1, y2 = cy - layout.roller_h // 2, cy + layout.roller_h // 2
        # Cuerpo del rodillo algo más frío que los extremos (donde están los rodamientos).
        img[y1:y2, x1:x2] = ambient + 0.7 * (temp - ambient)
        img[y1:y2, x1 : x1 + layout.bearing_w] = temp
        img[y1:y2, x2 - layout.bearing_w : x2] = temp

    img += rng.normal(0, 0.15, img.shape).astype(np.float32)
    return kelvin_centi(img)


def render_rgb(snap: Snapshot, rng: np.random.Generator, layout: RgbLayout = RgbLayout()) -> np.ndarray:
    """Devuelve una imagen BGR uint8 de la banda vista desde arriba."""
    h, w = layout.height, layout.width
    img = np.full((h, w, 3), 115, dtype=np.float32)  # hormigón / estructura
    img += rng.normal(0, 6, (h, w, 1)).astype(np.float32)

    belt_px = layout.belt_width_mm / layout.mm_per_px
    center = w / 2 + snap.belt_edge_offset_mm / layout.mm_per_px
    left, right = int(round(center - belt_px / 2)), int(round(center + belt_px / 2))
    lo, hi = max(0, left), min(w, right)
    if hi > lo:
        img[:, lo:hi] = (38, 38, 42)  # goma negra
        # Material transportado: manchas marrones en el centro de la banda, según la carga.
        load_frac = min(1.0, snap.load_tph / 1500.0)
        m_lo, m_hi = int(center - belt_px * 0.3), int(center + belt_px * 0.3)
        m_lo, m_hi = max(lo, m_lo), min(hi, m_hi)
        if m_hi > m_lo and load_frac > 0:
            mask = rng.random((h, m_hi - m_lo)) < 0.6 * load_frac
            region = img[:, m_lo:m_hi]
            region[mask] = (45, 75, 115)
    img += rng.normal(0, 4, img.shape).astype(np.float32)
    img = cv2.GaussianBlur(np.clip(img, 0, 255).astype(np.uint8), (3, 3), 0)
    return img


def encode_thermal(frame: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", frame)
    if not ok:
        raise RuntimeError("No se pudo codificar la imagen térmica")
    return buf.tobytes()


def encode_rgb(frame: np.ndarray, quality: int = 85) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("No se pudo codificar la imagen RGB")
    return buf.tobytes()
