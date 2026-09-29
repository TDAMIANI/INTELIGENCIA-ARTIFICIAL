"""Motor del gemelo digital: calcula la salud de cada activo y genera recomendaciones.

Cada `cycle_s` segundos:
1. Promedia la telemetría reciente de cada indicador (y su tendencia de la última hora).
2. Calcula el índice de salud de cada componente y lo propaga hacia arriba en la jerarquía.
3. Guarda el HI actual (con su explicación) y el historial.
4. Crea o actualiza recomendaciones de mantenimiento (sin duplicarlas).
5. Publica los cambios en mvt/{site}/twin (la API los retransmite por /ws/twin).

Uso:  python -m mvtwin.twin
"""

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from mvtwin.models import (
    Asset,
    FailureMode,
    HealthHistory,
    Recommendation,
    RecommendationStatus,
    Sensor,
)
from mvtwin.queries import bucketed_means, window_means
from mvtwin.schemas import RecommendationOut
from mvtwin.seed import load_plant_config
from mvtwin.twin.health import Indicator, IndicatorResult, evaluate, health_index, load_indicators, rollup
from mvtwin.twin.recommend import MaintenanceRule, Priority, Proposal, load_rules, propose

log = logging.getLogger("mvtwin.twin")

Publisher = Callable[[str, dict[str, Any]], None]
ACTIVE = (RecommendationStatus.open, RecommendationStatus.accepted)


def twin_topic(site: str) -> str:
    return f"mvt/{site}/twin"


@dataclass
class TwinEngine:
    session_factory: sessionmaker[Session]
    indicators: list[Indicator]
    rules: list[MaintenanceRule]
    publish: Publisher
    site: str
    window_s: int = 120
    trend_window_s: int = 3600
    trend_bucket_s: int = 60
    # Tras descartar una recomendación, no se vuelve a proponer igual o menor prioridad por este tiempo.
    dismiss_suppress_h: float = 24.0
    _by_code: dict[str, Indicator] = field(init=False)

    def __post_init__(self) -> None:
        self._by_code = {i.code: i for i in self.indicators}

    # --- ciclo ----------------------------------------------------------------------------

    def cycle(self, now: datetime | None = None) -> dict[str, float | None]:
        now = now or datetime.now(UTC)
        with self.session_factory() as s:
            assets = s.scalars(select(Asset).options(selectinload(Asset.failure_modes))).all()
            by_id = {a.id: a for a in assets}
            by_code = {a.code: a for a in assets}
            sensors = {code: (sid, aid) for code, sid, aid in s.execute(select(Sensor.code, Sensor.id, Sensor.asset_id))}

            fresh = self._evaluate(s, sensors, by_id, now)
            results = self._with_stale(fresh, assets)

            own = {code: health_index(res) for code, res in results.items()}
            children: dict[str, list[str]] = {}
            for a in assets:
                if a.parent_id is not None:
                    children.setdefault(by_id[a.parent_id].code, []).append(a.code)
            hi: dict[str, float | None] = {}
            for root in (a for a in assets if a.parent_id is None):
                hi.update(rollup(own, children, root.code))

            changed = self._store_health(s, by_code, children, results, own, hi, now)
            recs = self._sync_recommendations(s, by_code, fresh, now)
            s.commit()
            for rec in recs:
                s.refresh(rec, ["asset"])
            rec_payloads = [RecommendationOut.from_model(r).model_dump(mode="json") for r in recs]

        if changed:
            self.publish(twin_topic(self.site), {"type": "health", "ts": now.isoformat(), "assets": changed})
        for payload in rec_payloads:
            self.publish(twin_topic(self.site), {"type": "recommendation", "recommendation": payload})
        return hi

    def _evaluate(self, s: Session, sensors, by_id, now: datetime) -> dict[str, list[IndicatorResult]]:
        """Resultados "frescos" por activo (solo indicadores con datos y contexto válido)."""
        out: dict[str, list[IndicatorResult]] = {}
        start = now - timedelta(seconds=self.window_s)
        for ind in self.indicators:
            if ind.sensor not in sensors:
                log.warning("Indicador %s: sensor %s desconocido", ind.code, ind.sensor)
                continue
            if ind.context and not self._context_ok(s, sensors, ind, start, now):
                continue
            sensor_id, _ = sensors[ind.sensor]
            means = window_means(s, sensor_id, ind.metric, start, now)
            if not means:
                continue
            series = bucketed_means(s, sensor_id, ind.metric, now - timedelta(seconds=self.trend_window_s), now,
                                    self.trend_bucket_s)
            values = {by_id[aid].code: v for aid, v in means.items() if aid in by_id}
            trends = {by_id[aid].code: pts for aid, pts in series.items() if aid in by_id}
            for code, res in evaluate(ind, values, trends).items():
                out.setdefault(code, []).append(res)
        return out

    def _context_ok(self, s: Session, sensors, ind: Indicator, start: datetime, now: datetime) -> bool:
        ctx = ind.context
        if ctx.sensor not in sensors:
            return False
        means = window_means(s, sensors[ctx.sensor][0], ctx.metric, start, now)
        return bool(means) and min(means.values()) >= ctx.min

    def _with_stale(self, fresh: dict[str, list[IndicatorResult]], assets: list[Asset]):
        """Completa con el último valor conocido los indicadores que no se evaluaron en este ciclo."""
        merged = {code: list(res) for code, res in fresh.items()}
        for a in assets:
            done = {r.indicator for r in merged.get(a.code, [])}
            for d in (a.health_details or {}).get("indicators", []):
                if d["indicator"] not in done and d["indicator"] in self._by_code:
                    merged.setdefault(a.code, []).append(IndicatorResult.from_dict(d))
        return merged

    def _store_health(self, s, by_code, children, results, own, hi, now) -> list[dict[str, Any]]:
        changed = []
        for code, asset in by_code.items():
            new = hi.get(code)
            details: dict[str, Any] = {
                "own_health_index": own.get(code),
                "indicators": [r.to_dict() for r in results.get(code, [])],
            }
            kids = [(hi.get(c), c) for c in children.get(code, []) if hi.get(c) is not None]
            if kids:
                details["worst_child"] = min(kids)[1]
            old = asset.health_index
            asset.health_details = details
            if new is None:
                continue
            asset.health_index = new
            asset.health_updated_at = now
            s.merge(HealthHistory(asset_id=asset.id, ts=now, health_index=new))
            if old is None or abs(old - new) >= 0.5:
                changed.append({"code": code, "health_index": new, "previous": old})
        return changed

    def _sync_recommendations(self, s: Session, by_code, fresh, now: datetime) -> list[Recommendation]:
        touched: list[Recommendation] = []
        proposals = {(p.asset_code, p.indicator): p for p in propose(self.rules, self._by_code, fresh, now)}

        for (code, indicator), p in proposals.items():
            asset = by_code[code]
            history = s.scalars(
                select(Recommendation)
                .where(Recommendation.asset_id == asset.id, Recommendation.indicator == indicator)
                .order_by(Recommendation.created_at.desc(), Recommendation.id.desc())
            ).all()
            active = next((r for r in history if r.status in ACTIVE), None)
            if active is not None:
                if self._update_active(active, p, now):
                    touched.append(active)
                continue
            last = history[0] if history else None
            if (
                last is not None
                and last.status is RecommendationStatus.dismissed
                and _aware(last.updated_at) > now - timedelta(hours=self.dismiss_suppress_h)
                and Priority(last.priority).rank >= p.rule.priority.rank
            ):
                continue
            rec = Recommendation(
                asset_id=asset.id,
                failure_mode_id=self._failure_mode_id(asset, by_code, p.failure_mode),
                indicator=indicator,
                rule_code=p.rule.code,
                priority=p.rule.priority.value,
                status=RecommendationStatus.open,
                action=p.action,
                reason=p.reason,
                due_by=p.due_by,
                condition_active=True,
                created_at=now,
                updated_at=now,
            )
            s.add(rec)
            touched.append(rec)
            log.info("Nueva recomendación [%s] %s", rec.priority, rec.action)

        # Indicadores evaluados sin propuesta: la condición se normalizó.
        evaluated = {(code, r.indicator) for code, res in fresh.items() for r in res}
        for code, indicator in evaluated - set(proposals):
            for rec in s.scalars(
                select(Recommendation).where(
                    Recommendation.asset_id == by_code[code].id,
                    Recommendation.indicator == indicator,
                    Recommendation.status.in_(ACTIVE),
                    Recommendation.condition_active.is_(True),
                )
            ):
                rec.condition_active = False
                rec.updated_at = now
                touched.append(rec)
        s.flush()
        return touched

    @staticmethod
    def _update_active(rec: Recommendation, p: Proposal, now: datetime) -> bool:
        changed = False
        if p.rule.priority.rank > Priority(rec.priority).rank:
            rec.priority, rec.rule_code, rec.action = p.rule.priority.value, p.rule.code, p.action
            changed = True
        if p.due_by < _aware(rec.due_by) - timedelta(minutes=30):  # solo cambios relevantes de plazo
            rec.due_by = p.due_by
            changed = True
        if not rec.condition_active:
            rec.condition_active = True
            changed = True
        if changed:
            rec.reason = p.reason
            rec.updated_at = now
        return changed

    @staticmethod
    def _failure_mode_id(asset: Asset, by_code: dict[str, Asset], fm_code: str | None) -> int | None:
        """Busca el modo de falla en el activo o en sus ancestros (el FMEA suele estar en la subunidad)."""
        if fm_code is None:
            return None
        node: Asset | None = asset
        while node is not None:
            fm: FailureMode | None = next((f for f in node.failure_modes if f.code == fm_code), None)
            if fm is not None:
                return fm.id
            node = node.parent
        return None


def _aware(ts: datetime) -> datetime:
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts


def build_engine(session_factory, publish: Publisher, config: dict[str, Any]) -> TwinEngine:
    twin_cfg = config.get("twin", {})
    return TwinEngine(
        session_factory=session_factory,
        indicators=load_indicators(config),
        rules=load_rules(config),
        publish=publish,
        site=config.get("site", "default"),
        window_s=int(twin_cfg.get("window_s", 120)),
        trend_window_s=int(twin_cfg.get("trend_window_s", 3600)),
        trend_bucket_s=int(twin_cfg.get("trend_bucket_s", 60)),
    )


def main() -> None:  # pragma: no cover - glue con MQTT, se prueba con docker compose
    import paho.mqtt.client as mqtt

    from mvtwin.db import SessionLocal
    from mvtwin.settings import settings

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = load_plant_config(settings.plant_config)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="mvtwin-twin")
    client.connect_async(settings.mqtt_host or "localhost", settings.mqtt_port, keepalive=30)
    client.loop_start()

    def publish(topic: str, payload: dict[str, Any]) -> None:
        client.publish(topic, json.dumps(payload, ensure_ascii=False, default=str), qos=1)

    engine = build_engine(SessionLocal, publish, config)
    cycle_s = float(config.get("twin", {}).get("cycle_s", 30))
    log.info("Motor del gemelo: %d indicadores, %d reglas, ciclo de %.0f s",
             len(engine.indicators), len(engine.rules), cycle_s)
    while True:
        started = time.monotonic()
        try:
            hi = engine.cycle()
            worst = sorted((v, k) for k, v in hi.items() if v is not None)[:3]
            log.info("Ciclo OK. Peores HI: %s", ", ".join(f"{k}={v:.0f}" for v, k in worst) or "sin datos")
        except Exception:  # noqa: BLE001 - un ciclo fallido no debe detener el servicio
            log.exception("Error en el ciclo del gemelo")
        time.sleep(max(1.0, cycle_s - (time.monotonic() - started)))
