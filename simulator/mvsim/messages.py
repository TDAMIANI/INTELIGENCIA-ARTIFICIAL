"""Traduce un Snapshot del simulador a mensajes MQTT.

Convención de tópicos (ver config/plant.yaml):  mvt/{site}/{sensor_code}/telemetry
Payload JSON:  {"ts": ISO-8601 UTC, "sensor": código, "values": {...}, "simulated": true}
"""

from datetime import datetime
from typing import Any

from mvsim.conveyor import ConveyorSimulator, Snapshot


def telemetry_topic(site: str, sensor_code: str) -> str:
    return f"mvt/{site}/{sensor_code}/telemetry"


def command_topic(site: str) -> str:
    return f"mvt/{site}/sim/cmd"


def snapshot_messages(
    site: str, sim: ConveyorSimulator, snap: Snapshot, ts: datetime
) -> list[tuple[str, dict[str, Any]]]:
    code = sim.config.code
    by_sensor: dict[str, dict[str, Any]] = {
        f"{code}-PLC": {
            "running": snap.running,
            "belt_speed_mps": snap.belt_speed_mps,
            "motor_current_a": snap.motor_current_a,
            "load_tph": snap.load_tph,
        },
        f"{code}-MOT-VIB": {"vibration_rms_mm_s": snap.vibration_rms_mm_s},
        f"{code}-TH-01": {
            "idler_max_temp_c": {sim.idler_code(i + 1): t for i, t in enumerate(snap.idler_temps_c)}
        },
        f"{code}-RGB-01": {"belt_edge_offset_mm": snap.belt_edge_offset_mm},
    }
    stamp = ts.isoformat(timespec="milliseconds")
    return [
        (
            telemetry_topic(site, sensor),
            {"ts": stamp, "sensor": sensor, "values": values, "simulated": True},
        )
        for sensor, values in by_sensor.items()
    ]
