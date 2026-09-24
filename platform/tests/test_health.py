from datetime import UTC, datetime, timedelta

import pytest

from mvtwin.twin.health import Indicator, IndicatorResult, evaluate, health_index, linear_trend, rollup
from mvtwin.twin.recommend import MaintenanceRule, Priority, propose

T0 = datetime(2026, 1, 1, tzinfo=UTC)

VIB = Indicator.from_dict({
    "code": "VIB", "name": "Vibración", "sensor": "S", "metric": "v", "unit": "mm/s",
    "curve": [[2.3, 0], [4.5, 30], [7.1, 70], [11, 100]], "critical_severity": 70,
})
IDL = Indicator.from_dict({
    "code": "IDL", "name": "ΔT polín", "sensor": "TH", "metric": "t", "mode": "delta_neighbours",
    "curve": [[5, 0], [15, 40], [30, 80], [45, 100]], "failure_mode": "FM-X",
})


@pytest.mark.parametrize(("value", "severity"), [(1.0, 0), (2.3, 0), (3.4, 15), (4.5, 30), (7.1, 70), (20, 100)])
def test_severity_curve_iso10816(value: float, severity: float) -> None:
    assert VIB.severity(value) == pytest.approx(severity)


def test_critical_value_inverts_curve() -> None:
    assert VIB.critical_value() == pytest.approx(7.1)
    assert IDL.critical_value() == pytest.approx(30.0)


def test_invalid_curves() -> None:
    base = {"code": "X", "sensor": "S", "metric": "m"}
    with pytest.raises(ValueError):
        Indicator.from_dict(base | {"curve": [[5, 0], [5, 10]]})
    with pytest.raises(ValueError):
        Indicator.from_dict(base | {"curve": [[1, 0], [2, 150]]})


def test_health_index_formula() -> None:
    r = [IndicatorResult("A", 0, 30, 30), IndicatorResult("B", 0, 50, 25)]
    assert health_index(r) == 45
    assert health_index(r + [IndicatorResult("C", 0, 100, 100)]) == 0
    assert health_index([]) is None


def test_rollup_worst_child_wins() -> None:
    children = {"PLANT": ["CONV"], "CONV": ["MOT", "IDL"], "IDL": ["I1", "I2"]}
    own = {"MOT": 90.0, "I1": 100.0, "I2": 35.0, "PLANT": None}
    hi = rollup(own, children, "PLANT")
    assert hi == {"MOT": 90.0, "I1": 100.0, "I2": 35.0, "IDL": 35.0, "CONV": 35.0, "PLANT": 35.0}
    assert rollup({}, {"A": ["B"]}, "A") == {"B": None, "A": None}


def test_linear_trend_estimates_time_to_critical() -> None:
    pts = [(T0 + timedelta(minutes=10 * i), 10.0 + 2.0 * i) for i in range(7)]  # +12 por hora
    trend = linear_trend(pts, current=22.0, critical=30.0)
    assert trend.per_hour == pytest.approx(12.0)
    assert trend.r2 == pytest.approx(1.0)
    assert trend.eta_critical_h == pytest.approx(8 / 12, abs=0.05)
    assert linear_trend(pts[:3], 22.0, 30.0) is None  # pocos puntos
    falling = [(ts, 30 - v) for ts, v in pts]
    assert linear_trend(falling, 10.0, 30.0).eta_critical_h is None


def test_trend_ignores_noise() -> None:
    noisy = [(T0 + timedelta(minutes=i), 10.0 + (3 if i % 2 else -3)) for i in range(20)]
    assert linear_trend(noisy, 10.0, 30.0).eta_critical_h is None


def test_evaluate_delta_neighbours_with_trend() -> None:
    values = {f"I{n:02d}": 30.0 for n in range(1, 11)} | {"I05": 55.0}
    series = {code: [(T0 + timedelta(minutes=m), 30.0) for m in range(30)] for code in values}
    series["I05"] = [(T0 + timedelta(minutes=m), 30.0 + m * (25 / 29)) for m in range(30)]
    res = evaluate(IDL, values, series)
    assert res["I05"].value == pytest.approx(25.0)
    assert res["I05"].severity == pytest.approx(66.7, abs=0.1)
    assert res["I05"].trend_per_h == pytest.approx(51.7, abs=0.5)
    assert res["I05"].eta_critical_h == pytest.approx(5 / 51.7, abs=0.01)
    assert res["I04"].severity == 0


def test_propose_picks_strongest_rule_and_advances_due_date() -> None:
    rules = [
        MaintenanceRule("R1", "IDL", 25, Priority.medium, 168, "Inspeccionar {asset}"),
        MaintenanceRule("R2", "IDL", 40, Priority.high, 72, "Cambiar {asset}"),
        MaintenanceRule("R3", "IDL", 80, Priority.urgent, 8, "Urgente {asset}"),
    ]
    results = {
        "I05": [IndicatorResult("IDL", 25.0, 66.7, 66.7, trend_per_h=10.0, eta_critical_h=0.5)],
        "I06": [IndicatorResult("IDL", 12.0, 28.0, 28.0)],
        "I07": [IndicatorResult("IDL", 2.0, 0.0, 0.0)],
    }
    props = {p.asset_code: p for p in propose(rules, {"IDL": IDL}, results, T0)}
    assert set(props) == {"I05", "I06"}
    assert (props["I05"].rule.code, props["I05"].action) == ("R2", "Cambiar I05")
    assert props["I05"].due_by == T0 + timedelta(minutes=30)  # la tendencia adelanta el plazo de 72 h
    assert "zona crítica en ~30 min" in props["I05"].reason
    assert props["I06"].due_by == T0 + timedelta(hours=168)
    assert props["I06"].failure_mode == "FM-X"
