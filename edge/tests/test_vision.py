import json

import numpy as np
import pytest

from mvedge.belt import BeltConfig, measure_belt
from mvedge.config import as_bool, expand_env
from mvedge.detection import Detection, RuleEngine, ZoneRule
from mvedge.events import Event, EventPublisher, snapshot_key
from mvedge.pipelines import RgbPipeline, ThermalPipeline
from mvedge.thermal import (
    HotspotDetector,
    Roi,
    decode_radiometric,
    delta_vs_neighbours,
    max_temp_per_roi,
    rois_from_config,
)

GRID = {"grid": {"code": "IDL-{n:02d}", "rows": 2, "cols": 20, "x0": 0, "y0": 40, "cell_w": 16, "cell_h": 80}}


class Sink:
    def __init__(self):
        self.messages: list[tuple[str, dict]] = []

    def __call__(self, topic: str, payload: str) -> None:
        self.messages.append((topic, json.loads(payload)))

    def topics(self, suffix: str) -> list[dict]:
        return [p for t, p in self.messages if t.endswith(suffix)]


class FakeStore:
    bucket = "test"

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.saved: list[str] = []

    def put_jpeg(self, key: str, image) -> None:
        if self.fail:
            raise ConnectionError("S3 caído")
        assert image.ndim == 3
        self.saved.append(key)


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def thermal_frame(temps: dict[int, float], ambient: float = 20.0) -> np.ndarray:
    """Imagen radiométrica sintética: un rodillo por celda de la grilla."""
    img = np.full((240, 320), ambient, dtype=np.float32)
    for n in range(1, 41):
        row, col = divmod(n - 1, 20)
        cx, cy = col * 16 + 8, 40 + row * 80 + 40
        img[cy - 3 : cy + 3, cx - 6 : cx + 6] = temps.get(n, 32.0)
    return np.round((img + 273.15) * 100).astype(np.uint16)


def belt_frame(offset_mm: float, mm_per_px: float = 3.0) -> np.ndarray:
    img = np.full((360, 640, 3), 120, dtype=np.uint8)
    center = 320 + offset_mm / mm_per_px
    left, right = int(round(center - 200)), int(round(center + 200))
    img[:, max(0, left) : min(640, right)] = 40
    img[:, int(center - 100) : int(center + 100)] = 75  # material
    return img


# --- térmica ------------------------------------------------------------------------------


def test_grid_rois() -> None:
    rois = rois_from_config(GRID)
    assert len(rois) == 40
    assert rois[0] == Roi("IDL-01", 0, 40, 16, 80)
    assert rois[39] == Roi("IDL-40", 304, 120, 16, 80)
    with pytest.raises(ValueError):
        rois_from_config({"items": [{"code": "A", "x": 0, "y": 0, "w": 1, "h": 1}] * 2})


def test_max_temp_per_roi_and_delta() -> None:
    temps = decode_radiometric(thermal_frame({12: 75.0}))
    max_temps = max_temp_per_roi(temps, rois_from_config(GRID))
    assert max_temps["IDL-12"] == pytest.approx(75.0, abs=0.1)
    assert max_temps["IDL-11"] == pytest.approx(32.0, abs=0.1)
    deltas = delta_vs_neighbours(max_temps)
    assert deltas["IDL-12"] == pytest.approx(43.0, abs=0.2)
    assert abs(deltas["IDL-11"]) < 0.2


def test_decode_rejects_non_radiometric() -> None:
    with pytest.raises(ValueError):
        decode_radiometric(np.zeros((2, 2), dtype=np.uint8))


def test_hotspot_needs_confirmation() -> None:
    det = HotspotDetector(delta_c=15, confirm=3)
    hot = {f"IDL-{n:02d}": 30.0 for n in range(1, 41)} | {"IDL-12": 60.0}
    cold = {f"IDL-{n:02d}": 30.0 for n in range(1, 41)}
    assert det.update(hot) == [] and det.update(cold) == [] and det.update(hot) == []
    assert det.update(hot) == []
    assert det.update(hot) == [("IDL-12", 30.0)]


def test_thermal_pipeline_publishes_telemetry_and_one_event() -> None:
    sink, store, clock = Sink(), FakeStore(), FakeClock()
    events = EventPublisher("site", sink, store, clock)
    pipe = ThermalPipeline("site", "TH-01", rois_from_config(GRID), sink, events,
                           HotspotDetector(delta_c=15, confirm=2), cooldown_s=600)
    for _ in range(5):
        pipe.process(thermal_frame({12: 80.0}))
    telemetry = sink.topics("/telemetry")
    assert len(telemetry) == 5
    assert len(telemetry[0]["values"]["max_temp_c"]) == 40
    (event,) = sink.topics("/event")  # anti-rebote: un solo evento
    assert (event["type"], event["asset"], event["severity"]) == ("thermal_hotspot", "IDL-12", "critical")
    assert event["snapshot"]["key"] == store.saved[0]
    clock.t = 601
    pipe.process(thermal_frame({12: 80.0}))
    assert len(sink.topics("/event")) == 2


# --- banda --------------------------------------------------------------------------------


@pytest.mark.parametrize("offset", [-120, -30, 0, 45, 120])
def test_belt_offset(offset: float) -> None:
    m = measure_belt(belt_frame(offset))
    assert m.detected
    assert m.offset_mm == pytest.approx(offset, abs=3.5)
    assert m.width_mm == pytest.approx(1200, abs=6)


def test_no_belt_in_view() -> None:
    assert not measure_belt(np.full((360, 640, 3), 120, dtype=np.uint8)).detected


# --- detección ----------------------------------------------------------------------------


def test_zone_rule_uses_foot_point() -> None:
    rule = ZoneRule.from_dict({"type": "person_in_zone", "label": "person",
                               "zone": [[100, 100], [300, 100], [300, 300], [100, 300]]})
    inside = Detection("person", 0.9, (150, 50, 200, 250))  # cabeza fuera, pies dentro
    outside = Detection("person", 0.9, (150, 10, 200, 90))
    truck = Detection("truck", 0.9, (150, 50, 200, 250))
    assert rule.matches(inside) and not rule.matches(outside) and not rule.matches(truck)
    assert RuleEngine([rule]).evaluate([outside, truck]) == {}


class FakeDetector:
    def __init__(self, detections):
        self.detections = detections

    def detect(self, frame):
        return self.detections


def test_rgb_pipeline_belt_and_detections() -> None:
    sink = Sink()
    events = EventPublisher("site", sink, FakeStore(), FakeClock())
    rule = ZoneRule.from_dict({"type": "person_in_zone", "label": "person", "severity": "critical"})
    pipe = RgbPipeline("site", "RGB-01", sink, events, belt=BeltConfig(),
                       detector=FakeDetector([Detection("person", 0.8, (10, 10, 50, 100))]),
                       rules=RuleEngine([rule]))
    values = pipe.process(belt_frame(60))
    assert values["belt_detected"] is True
    assert values["belt_edge_offset_mm"] == pytest.approx(60, abs=3.5)
    assert values["count_person"] == 1
    (event,) = sink.topics("/event")
    assert event["type"] == "person_in_zone"
    assert event["data"]["detections"][0]["label"] == "person"


# --- eventos y configuración --------------------------------------------------------------


def test_event_is_published_even_if_s3_fails() -> None:
    sink = Sink()
    events = EventPublisher("site", sink, FakeStore(fail=True), FakeClock())
    assert events.emit(Event("S", "t", "warning", "m"), np.zeros((4, 4, 3), np.uint8), cooldown_s=0)
    assert sink.topics("/event")[0]["snapshot"] is None


def test_snapshot_key_layout() -> None:
    from datetime import UTC, datetime

    ev = Event("CV-201-TH-01", "thermal_hotspot", "warning", "m", asset="CV-201-IDL-12",
               ts=datetime(2026, 5, 4, 3, 2, 1, tzinfo=UTC))
    assert snapshot_key("mina", ev) == (
        "mina/CV-201-TH-01/2026/05/04/20260504T030201000000Z_thermal_hotspot_CV-201-IDL-12.jpg"
    )


def test_env_expansion(monkeypatch) -> None:
    monkeypatch.setenv("MVT_X", "broker")
    assert expand_env({"a": "${MVT_X}:1883", "b": ["${NO_EXISTE:-def}"], "c": 3}) == {
        "a": "broker:1883", "b": ["def"], "c": 3}
    assert as_bool("false") is False and as_bool("true") is True and as_bool(True) is True
