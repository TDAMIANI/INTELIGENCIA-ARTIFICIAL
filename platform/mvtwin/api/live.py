"""WebSockets en vivo: puente MQTT -> navegador.

- /ws/alarms                    cambios de alarmas (raised / escalated / cleared)
- /ws/events                    eventos de visión del edge (con link a la imagen de evidencia)
- /ws/telemetry/{sensor_code}   telemetría de un sensor, tal como llega del edge

La API escucha MQTT en un hilo de paho y reparte cada mensaje a las colas asyncio
de los WebSockets suscritos a ese canal.
"""

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = logging.getLogger("mvtwin.live")

router = APIRouter(tags=["live"])

QUEUE_SIZE = 200


class LiveHub:
    def __init__(self) -> None:
        self._subs: dict[str, set[tuple[asyncio.Queue[dict[str, Any]], asyncio.AbstractEventLoop]]] = {}

    def subscribe(self, channel: str) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subs.setdefault(channel, set()).add((q, asyncio.get_running_loop()))
        return q

    def unsubscribe(self, channel: str, q: asyncio.Queue[dict[str, Any]]) -> None:
        subs = self._subs.get(channel, set())
        for entry in [e for e in subs if e[0] is q]:
            subs.discard(entry)
        if not subs:
            self._subs.pop(channel, None)

    def has_subscribers(self, channel: str) -> bool:
        return bool(self._subs.get(channel))

    def publish(self, channel: str, message: dict[str, Any]) -> None:
        """Se puede llamar desde cualquier hilo (p. ej. el callback de paho)."""
        for q, loop in list(self._subs.get(channel, ())):
            loop.call_soon_threadsafe(_put_dropping_oldest, q, message)


def _put_dropping_oldest(q: asyncio.Queue[dict[str, Any]], message: dict[str, Any]) -> None:
    # Si un cliente lento se atrasa, se descartan los mensajes más viejos y no los nuevos.
    if q.full():
        q.get_nowait()
    q.put_nowait(message)


hub = LiveHub()


def channel_for_topic(topic: str) -> str | None:
    parts = topic.split("/")
    if len(parts) == 3 and parts[0] == "mvt" and parts[2] in ("alarms", "events"):
        return parts[2]
    if len(parts) == 4 and parts[0] == "mvt" and parts[3] == "telemetry":
        return f"telemetry:{parts[2]}"
    return None


def start_mqtt_bridge(host: str, port: int):  # pragma: no cover - se prueba con docker compose
    import paho.mqtt.client as mqtt

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=None)

    def on_connect(c, _u, _f, reason_code, _p):
        log.info("Puente MQTT conectado a %s:%s (%s)", host, port, reason_code)
        c.subscribe([("mvt/+/alarms", 1), ("mvt/+/events", 1), ("mvt/+/+/telemetry", 0)])

    def on_message(_c, _u, msg):
        channel = channel_for_topic(msg.topic)
        if channel is None or not hub.has_subscribers(channel):
            return
        try:
            hub.publish(channel, json.loads(msg.payload))
        except json.JSONDecodeError:
            log.warning("Mensaje no JSON en %s", msg.topic)

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect_async(host, port, keepalive=30)  # reintenta solo si el broker no está listo
    client.loop_start()
    return client


async def _pump(websocket: WebSocket, channel: str) -> None:
    await websocket.accept()
    q = hub.subscribe(channel)
    try:
        await websocket.send_json({"type": "subscribed", "channel": channel})
        receiver = asyncio.create_task(websocket.receive_text())  # detecta el cierre del cliente
        while True:
            getter = asyncio.create_task(q.get())
            done, _ = await asyncio.wait({getter, receiver}, return_when=asyncio.FIRST_COMPLETED)
            if receiver in done:
                getter.cancel()
                receiver.result()  # lanza WebSocketDisconnect si el cliente cerró
                receiver = asyncio.create_task(websocket.receive_text())
                continue
            await websocket.send_json({"type": "message", "channel": channel, "data": getter.result()})
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(channel, q)


@router.websocket("/ws/alarms")
async def ws_alarms(websocket: WebSocket) -> None:
    await _pump(websocket, "alarms")


@router.websocket("/ws/events")
async def ws_events(websocket: WebSocket) -> None:
    await _pump(websocket, "events")


@router.websocket("/ws/telemetry/{sensor_code}")
async def ws_telemetry(websocket: WebSocket, sensor_code: str) -> None:
    await _pump(websocket, f"telemetry:{sensor_code}")
