"""Pipelines por cámara: imagen -> telemetría + eventos con evidencia.

Publican exactamente el mismo formato de telemetría que el simulador
(mvt/{site}/{sensor}/telemetry), así que la plataforma no distingue el origen.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np

from mvedge.belt import BeltConfig, measure_belt
from mvedge.detection import Detector, RuleEngine, draw_detections
from mvedge.events import Event, EventPublisher
from mvedge.thermal import HotspotDetector, Roi, decode_radiometric, max_temp_per_roi, render_snapshot

Publish = Callable[[str, str], None]


def telemetry_topic(site: str, sensor: str) -> str:
    return f"mvt/{site}/{sensor}/telemetry"


def publish_telemetry(publish: Publish, site: str, sensor: str, ts: datetime, values: dict[str, Any]) -> None:
    payload = {"ts": ts.isoformat(timespec="milliseconds"), "sensor": sensor, "values": values, "simulated": False}
    publish(telemetry_topic(site, sensor), json.dumps(payload, ensure_ascii=False))


@dataclass
class ThermalPipeline:
    site: str
    sensor: str
    rois: list[Roi]
    publish: Publish
    events: EventPublisher
    hotspot: HotspotDetector = field(default_factory=HotspotDetector)
    cooldown_s: float = 600.0
    raw_scale: float = 0.01
    raw_offset_c: float = -273.15

    def process(self, raw: np.ndarray, ts: datetime | None = None) -> dict[str, float]:
        ts = ts or datetime.now(UTC)
        temps = decode_radiometric(raw, self.raw_scale, self.raw_offset_c)
        max_temps = max_temp_per_roi(temps, self.rois)
        publish_telemetry(self.publish, self.site, self.sensor, ts, {"max_temp_c": max_temps})

        hot = self.hotspot.update(max_temps)
        if hot:
            snapshot = render_snapshot(temps, self.rois, max_temps, highlight={code for code, _ in hot})
            for code, delta in hot:
                self.events.emit(
                    Event(
                        sensor=self.sensor,
                        type="thermal_hotspot",
                        severity="critical" if delta >= 2 * self.hotspot.delta_c else "warning",
                        message=f"{code}: {max_temps[code]:.0f} °C, {delta:.0f} °C sobre sus vecinos",
                        asset=code,
                        value=delta,
                        data={"max_temp_c": max_temps[code], "delta_c": delta},
                        ts=ts,
                    ),
                    snapshot,
                    self.cooldown_s,
                )
        return max_temps


@dataclass
class RgbPipeline:
    site: str
    sensor: str
    publish: Publish
    events: EventPublisher
    belt: BeltConfig | None = None
    detector: Detector | None = None
    rules: RuleEngine = field(default_factory=lambda: RuleEngine([]))
    cooldown_s: float = 60.0

    def process(self, frame: np.ndarray, ts: datetime | None = None) -> dict[str, Any]:
        ts = ts or datetime.now(UTC)
        values: dict[str, Any] = {}
        if self.belt is not None:
            m = measure_belt(frame, self.belt)
            values["belt_detected"] = m.detected
            if m.detected:
                values["belt_edge_offset_mm"] = m.offset_mm
                values["belt_width_mm"] = m.width_mm

        if self.detector is not None:
            detections = self.detector.detect(frame)
            for label in {d.label for d in detections}:
                values[f"count_{label}"] = sum(d.label == label for d in detections)
            for rule, hits in self.rules.evaluate(detections).items():
                best = max(hits, key=lambda d: d.confidence)
                zones = [rule.zone] if rule.zone else []
                self.events.emit(
                    Event(
                        sensor=self.sensor,
                        type=rule.event_type,
                        severity=rule.severity,
                        message=rule.message.format(label=best.label, confidence=best.confidence, count=len(hits)),
                        value=float(len(hits)),
                        data={"detections": [{"label": d.label, "confidence": d.confidence, "box": d.box}
                                             for d in hits]},
                        ts=ts,
                    ),
                    draw_detections(frame, hits, zones),
                    self.cooldown_s,
                )

        if values:
            publish_telemetry(self.publish, self.site, self.sensor, ts, values)
        return values
