from datetime import UTC, datetime

import pytest

from mvsim.conveyor import ConveyorSimulator, Fault, FaultKind
from mvsim.messages import snapshot_messages
from mvsim.publisher import apply_command, parse_fault


def run_for(sim: ConveyorSimulator, seconds: float, dt: float = 5.0):
    snap = None
    for _ in range(int(seconds / dt)):
        snap = sim.step(dt)
    return snap


def test_healthy_conveyor_is_within_normal_ranges() -> None:
    snap = run_for(ConveyorSimulator(seed=1), 1800)
    assert snap.running and snap.belt_speed_mps == pytest.approx(3.5)
    assert 40 < snap.motor_current_a < 95
    assert snap.vibration_rms_mm_s < 2.3  # zona A de ISO 10816 clase III
    assert max(snap.idler_temps_c) - min(snap.idler_temps_c) < 8
    assert abs(snap.belt_edge_offset_mm) < 12


def test_idler_bearing_fault_heats_only_target_idler() -> None:
    sim = ConveyorSimulator(seed=2)
    sim.inject(Fault(FaultKind.idler_bearing, target=12, start_s=60, ramp_s=300))
    snap = run_for(sim, 1800)
    temps = snap.idler_temps_c
    neighbours = temps[9:11] + temps[12:14]
    assert temps[11] - max(neighbours) > 40
    assert max(temps[:11] + temps[12:]) < 40


def test_motor_imbalance_raises_vibration_progressively() -> None:
    sim = ConveyorSimulator(seed=3)
    sim.inject(Fault(FaultKind.motor_imbalance, start_s=0, ramp_s=600))
    mid = run_for(sim, 300)
    end = run_for(sim, 600)
    assert 2.3 < mid.vibration_rms_mm_s < end.vibration_rms_mm_s
    assert end.vibration_rms_mm_s > 7.1  # zona D


def test_belt_misalignment_shifts_edge() -> None:
    sim = ConveyorSimulator(seed=4)
    sim.inject(Fault(FaultKind.belt_misalignment, ramp_s=0, severity=0.5))
    assert run_for(sim, 60).belt_edge_offset_mm > 25


def test_planned_stop_ramps_down_to_zero() -> None:
    sim = ConveyorSimulator(seed=5)
    run_for(sim, 60)
    sim.set_running(False)
    snap = run_for(sim, 60)
    assert snap.belt_speed_mps == 0
    assert snap.motor_current_a == 0
    assert snap.vibration_rms_mm_s == 0
    assert snap.load_tph == 0


def test_idler_fault_requires_valid_target() -> None:
    with pytest.raises(ValueError):
        ConveyorSimulator().inject(Fault(FaultKind.idler_bearing, target=99))


def test_parse_fault_spec() -> None:
    fault = parse_fault("idler_bearing:target=12,start=60,ramp=120", now_s=10)
    assert (fault.kind, fault.target, fault.start_s, fault.ramp_s) == (FaultKind.idler_bearing, 12, 70, 120)
    with pytest.raises(ValueError):
        parse_fault("motor_imbalance:bogus=1")
    with pytest.raises(ValueError):
        parse_fault("not_a_fault")


def test_runtime_commands() -> None:
    sim = ConveyorSimulator(seed=6)
    apply_command(sim, {"action": "inject", "kind": "motor_imbalance", "ramp_s": 0})
    apply_command(sim, {"action": "inject", "kind": "belt_misalignment"})
    apply_command(sim, {"action": "clear", "kind": "motor_imbalance"})
    assert [f.kind for f in sim.faults] == [FaultKind.belt_misalignment]
    apply_command(sim, {"action": "stop"})
    assert not sim.running
    apply_command(sim, {"action": "clear"})
    assert sim.faults == []
    with pytest.raises(ValueError):
        apply_command(sim, {"action": "explode"})


def test_messages_match_plant_sensor_codes() -> None:
    sim = ConveyorSimulator(seed=7)
    msgs = dict(snapshot_messages("mina-demo", sim, sim.step(1.0), datetime(2026, 1, 1, tzinfo=UTC)))
    assert set(msgs) == {
        "mvt/mina-demo/CV-201-PLC/telemetry",
        "mvt/mina-demo/CV-201-MOT-VIB/telemetry",
        "mvt/mina-demo/CV-201-TH-01/telemetry",
        "mvt/mina-demo/CV-201-RGB-01/telemetry",
    }
    thermal = msgs["mvt/mina-demo/CV-201-TH-01/telemetry"]
    assert thermal["ts"] == "2026-01-01T00:00:00.000+00:00"
    assert len(thermal["values"]["max_temp_c"]) == 40
    assert "CV-201-IDL-40" in thermal["values"]["max_temp_c"]
