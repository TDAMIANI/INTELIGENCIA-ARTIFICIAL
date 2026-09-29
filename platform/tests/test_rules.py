from datetime import UTC, datetime

import pytest

from mvtwin.ingestion.rules import AlarmEngine, AlarmRule, TransitionKind, load_rules
from mvtwin.models import AlarmSeverity
from mvtwin.seed import load_plant_config
from mvtwin.settings import settings

TS = datetime(2026, 1, 1, tzinfo=UTC)

VIB = AlarmRule.from_dict(
    {
        "code": "VIB",
        "sensor": "S",
        "metric": "vib",
        "warning": 4.5,
        "critical": 7.1,
        "clear_below": 4.0,
        "confirm": 3,
    }
)


def feed(engine: AlarmEngine, values: list[float], sensor="S", metric="vib", asset="M"):
    out = []
    for v in values:
        out += engine.evaluate(sensor, metric, TS, {asset: v})
    return out


def test_single_spike_does_not_alarm() -> None:
    assert feed(AlarmEngine([VIB]), [1.5, 9.0, 1.5, 9.0, 9.0, 1.5]) == []


def test_raise_escalate_and_clear_with_hysteresis() -> None:
    engine = AlarmEngine([VIB])
    (raised,) = feed(engine, [5.0, 5.0, 5.0])
    assert (raised.kind, raised.severity, raised.threshold) == (TransitionKind.raised, AlarmSeverity.warning, 4.5)

    (escalated,) = feed(engine, [8.0, 8.0, 9.5])
    assert escalated.kind is TransitionKind.escalated
    assert escalated.severity is AlarmSeverity.critical
    assert escalated.peak_value == 9.5

    # Entre clear_below (4.0) y warning (4.5) la alarma sigue activa.
    assert feed(engine, [4.2] * 10) == []
    (cleared,) = feed(engine, [3.0, 3.0, 3.0])
    assert cleared.kind is TransitionKind.cleared
    assert cleared.peak_value == 9.5
    # Y puede volver a dispararse.
    assert feed(engine, [5.0] * 3)[0].kind is TransitionKind.raised


def test_raise_directly_as_critical() -> None:
    (raised,) = feed(AlarmEngine([VIB]), [9.0, 9.0, 9.0])
    assert raised.severity is AlarmSeverity.critical
    assert raised.threshold == 7.1


def test_restore_active_avoids_duplicate_raise() -> None:
    engine = AlarmEngine([VIB])
    engine.restore_active("VIB", "M", AlarmSeverity.warning, 5.0)
    assert feed(engine, [5.0] * 5) == []
    assert feed(engine, [3.0] * 3)[0].kind is TransitionKind.cleared


def test_delta_neighbours_flags_only_hot_idler() -> None:
    rule = AlarmRule.from_dict(
        {"code": "HOT", "sensor": "TH", "metric": "t", "mode": "delta_neighbours",
         "warning": 15, "critical": 30, "clear_below": 10, "neighbours": 3}
    )
    engine = AlarmEngine([rule])
    # Día caluroso: todos a ~45 °C, pero solo el polín 12 está 35 °C por encima de sus vecinos.
    temps = {f"IDL-{i:02d}": 45.0 + (i % 3) * 0.5 for i in range(1, 41)}
    temps["IDL-12"] = 80.0
    (t,) = engine.evaluate("TH", "t", TS, temps)
    assert t.asset_code == "IDL-12"
    assert t.severity is AlarmSeverity.critical
    assert t.value == pytest.approx(34.5)


def test_abs_mode_alarms_on_both_sides() -> None:
    rule = AlarmRule.from_dict(
        {"code": "MIS", "sensor": "C", "metric": "off", "mode": "abs", "warning": 30, "critical": 50}
    )
    assert AlarmEngine([rule]).evaluate("C", "off", TS, {"B": -35.0})[0].severity is AlarmSeverity.warning


def test_invalid_rule_thresholds() -> None:
    with pytest.raises(ValueError):
        AlarmRule.from_dict({"code": "X", "sensor": "S", "metric": "m", "warning": 10, "critical": 5})


def test_plant_config_rules_load() -> None:
    rules = load_rules(load_plant_config(settings.plant_config))
    assert {r.code for r in rules} == {"MOT-VIB-ISO10816", "IDL-HOTSPOT", "BELT-MISALIGN"}
