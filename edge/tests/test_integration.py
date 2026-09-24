"""Pruebas de integración: simulador -> edge, y YOLO real sobre una foto.

- El contrato con el simulador garantiza que la geometría de las cámaras virtuales
  coincide con config/edge.yaml. Se saltea si el simulador no está disponible.
- La prueba de YOLO se saltea si ultralytics no está instalado.
"""

import sys
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest

from mvedge.app import build_pipeline
from mvedge.config import load_config
from mvedge.detection import RuleEngine, ZoneRule
from mvedge.events import EventPublisher
from mvedge.pipelines import RgbPipeline

from test_vision import FakeClock, FakeStore, Sink

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "simulator"))
sim_mod = pytest.importorskip("mvsim.conveyor")
cameras = pytest.importorskip("mvsim.cameras")


@pytest.fixture
def cams(monkeypatch):
    monkeypatch.setenv("MVT_EDGE_YOLO", "false")
    return {c["code"]: c for c in load_config(ROOT / "edge" / "config" / "edge.yaml")["cameras"]}


def test_simulated_thermal_camera_matches_edge_config(cams) -> None:
    sim = sim_mod.ConveyorSimulator(seed=3)
    sim.inject(sim_mod.Fault(sim_mod.FaultKind.idler_bearing, target=27, ramp_s=0))
    sink = Sink()
    pipe = build_pipeline(cams["CV-201-TH-01"], "mina-demo", sink, EventPublisher("mina-demo", sink, FakeStore()))
    rng = np.random.default_rng(0)
    for _ in range(60):
        snap = sim.step(10.0)
        max_temps = pipe.process(cameras.render_thermal(sim, snap, rng))
    # El edge recupera la temperatura de cada polín desde la imagen.
    for i, temp in enumerate(snap.idler_temps_c):
        assert max_temps[sim.idler_code(i + 1)] == pytest.approx(temp, abs=1.0)
    events = sink.topics("/event")
    assert {e["asset"] for e in events} == {"CV-201-IDL-27"}


def test_simulated_rgb_camera_misalignment(cams) -> None:
    sim = sim_mod.ConveyorSimulator(seed=4)
    sim.inject(sim_mod.Fault(sim_mod.FaultKind.belt_misalignment, ramp_s=0, severity=0.75))
    sink = Sink()
    pipe = build_pipeline(cams["CV-201-RGB-01"], "mina-demo", sink, EventPublisher("mina-demo", sink, FakeStore()))
    rng = np.random.default_rng(1)
    for _ in range(10):
        snap = sim.step(1.0)
        values = pipe.process(cameras.render_rgb(snap, rng))
        assert values["belt_edge_offset_mm"] == pytest.approx(snap.belt_edge_offset_mm, abs=4)


def test_yolo_detects_person_in_zone() -> None:
    pytest.importorskip("ultralytics")
    from ultralytics.utils import ASSETS

    from mvedge.detection import YoloDetector

    try:
        detector = YoloDetector("yolo11n.pt", confidence=0.4, classes=["person"])
    except Exception as exc:  # noqa: BLE001 - sin red no se pueden bajar los pesos
        pytest.skip(f"Modelo YOLO no disponible: {exc}")
    frame = cv2.imread(str(ASSETS / "bus.jpg"))
    sink, store = Sink(), FakeStore()
    rule = ZoneRule.from_dict({"type": "person_in_zone", "label": "person", "severity": "critical"})
    pipe = RgbPipeline("s", "RGB", sink, EventPublisher("s", sink, store, FakeClock()),
                       detector=detector, rules=RuleEngine([rule]))
    values = pipe.process(frame)
    assert values["count_person"] >= 3
    assert sink.topics("/event")[0]["type"] == "person_in_zone"
    assert len(store.saved) == 1
    # Solo pidió personas: no hay otras clases (el colectivo no aparece).
    assert set(values) == {"count_person"}


def test_frames_mode_hides_camera_telemetry() -> None:
    from datetime import UTC, datetime

    from mvsim.messages import snapshot_messages

    sim = sim_mod.ConveyorSimulator(seed=1)
    snap = replace(sim.step(1.0))
    topics = [t for t, _ in snapshot_messages("s", sim, snap, datetime.now(UTC),
                                              frozenset({"CV-201-TH-01", "CV-201-RGB-01"}))]
    assert topics == ["mvt/s/CV-201-PLC/telemetry", "mvt/s/CV-201-MOT-VIB/telemetry"]


def test_plc_over_opcua_roundtrip() -> None:
    """Servidor OPC UA del simulador (PLC virtual) -> lector del edge -> telemetría."""
    import socket

    pytest.importorskip("asyncua")
    from mvsim.plc_server import PlcServer

    from mvedge.config import load_config
    from mvedge.plc import PlcConfig, PlcReader

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    endpoint = f"opc.tcp://127.0.0.1:{port}/minevision/"
    server = PlcServer(endpoint).start()
    try:
        sim = sim_mod.ConveyorSimulator(seed=1)
        for _ in range(30):
            snap = sim.step(1.0)
        server.update(snap)

        cfg = load_config(ROOT / "edge" / "config" / "edge.yaml")["plc"] | {"endpoint": endpoint}
        sink = Sink()
        reader = PlcReader(PlcConfig.from_dict(cfg), "mina-demo", sink)
        from asyncua.sync import Client

        client = Client(endpoint, timeout=5)
        client.connect()
        try:
            values = reader.read_once(client)
        finally:
            client.disconnect()
        assert values["running"] is True
        assert values["belt_speed_mps"] == pytest.approx(snap.belt_speed_mps)
        assert values["motor_current_a"] == pytest.approx(snap.motor_current_a)
        (msg,) = sink.topics("/telemetry")
        assert msg["sensor"] == "CV-201-PLC" and msg["values"]["load_tph"] == pytest.approx(snap.load_tph)
    finally:
        server.stop()
