"""Detección de objetos (YOLO) y reglas sobre las detecciones.

Con el modelo preentrenado (clases COCO) ya se puede detectar personas en zonas de riesgo.
El mismo mecanismo sirve para el modelo propio de daños en banda (Sprint de ML): se cambia
el archivo del modelo y se agregan reglas para sus clases (rotura, empalme, objeto extraño...).
"""

from dataclasses import dataclass
from typing import Any, Protocol

import cv2
import numpy as np


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    box: tuple[float, float, float, float]  # x1, y1, x2, y2 en píxeles

    @property
    def foot(self) -> tuple[float, float]:
        """Punto de apoyo (centro del borde inferior): define si una persona "está" en la zona."""
        x1, _, x2, y2 = self.box
        return ((x1 + x2) / 2, y2)


class Detector(Protocol):
    def detect(self, frame_bgr: np.ndarray) -> list[Detection]: ...


class YoloDetector:
    """Detector con Ultralytics YOLO. En un Jetson se usa el modelo exportado a TensorRT (.engine)."""

    def __init__(self, model: str = "yolo11n.pt", confidence: float = 0.4, classes: list[str] | None = None):
        from ultralytics import YOLO  # import diferido: es una dependencia pesada y opcional

        self.model = YOLO(model)
        self.confidence = confidence
        names = self.model.names
        self.class_ids = [i for i, n in names.items() if n in classes] if classes else None

    def detect(self, frame_bgr: np.ndarray) -> list[Detection]:
        result = self.model.predict(frame_bgr, conf=self.confidence, classes=self.class_ids, verbose=False)[0]
        names = result.names
        return [
            Detection(names[int(c)], round(float(p), 3), tuple(round(float(v), 1) for v in xyxy))
            for xyxy, p, c in zip(result.boxes.xyxy.tolist(), result.boxes.conf.tolist(), result.boxes.cls.tolist())
        ]


@dataclass(frozen=True)
class ZoneRule:
    """Genera un evento cuando un objeto de la clase `label` entra en la zona (polígono)."""

    event_type: str
    label: str
    zone: tuple[tuple[int, int], ...] | None = None  # None = toda la imagen
    severity: str = "warning"
    min_confidence: float = 0.0
    message: str = "{label} detectado ({confidence:.0%})"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ZoneRule":
        zone = tuple((int(x), int(y)) for x, y in d["zone"]) if d.get("zone") else None
        return cls(
            event_type=d["type"],
            label=d["label"],
            zone=zone,
            severity=d.get("severity", "warning"),
            min_confidence=float(d.get("min_confidence", 0.0)),
            message=d.get("message", cls.message),
        )

    def matches(self, det: Detection) -> bool:
        if det.label != self.label or det.confidence < self.min_confidence:
            return False
        if self.zone is None:
            return True
        polygon = np.array(self.zone, dtype=np.int32)
        return cv2.pointPolygonTest(polygon, det.foot, False) >= 0


@dataclass
class RuleEngine:
    rules: list[ZoneRule]

    def evaluate(self, detections: list[Detection]) -> dict[ZoneRule, list[Detection]]:
        """{regla: detecciones que la cumplen} (solo reglas con al menos una)."""
        out: dict[ZoneRule, list[Detection]] = {}
        for rule in self.rules:
            hits = [d for d in detections if rule.matches(d)]
            if hits:
                out[rule] = hits
        return out


def draw_detections(
    frame_bgr: np.ndarray, detections: list[Detection], zones: list[tuple[tuple[int, int], ...]] = ()
) -> np.ndarray:
    img = frame_bgr.copy()
    for zone in zones:
        cv2.polylines(img, [np.array(zone, dtype=np.int32)], True, (0, 200, 255), 2)
    for d in detections:
        x1, y1, x2, y2 = (int(v) for v in d.box)
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 255), 2)
        cv2.putText(img, f"{d.label} {d.confidence:.2f}", (x1, max(12, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, (0, 0, 255), 1, cv2.LINE_AA)
    return img
