"""Notificaciones: avisa por email y/o chat (Teams, Slack, Google Chat) lo que no puede esperar.

Qué se notifica:
- alarmas críticas (nuevas o escaladas a crítica);
- recomendaciones urgentes (nuevas o que suben a urgente);
- detecciones de visión críticas (p. ej. persona en zona de riesgo).

Para no saturar a nadie: la misma alarma, activo o recomendación no se repite antes de
`notify_cooldown_min` minutos.

Uso:  python -m mvtwin.integrations.notify   (escucha MQTT: mvt/+/alarms, mvt/+/twin, mvt/+/events)
"""

import json
import logging
import smtplib
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any

from mvtwin.settings import settings

log = logging.getLogger("mvtwin.notify")


@dataclass(frozen=True)
class Notification:
    key: str  # para el anti-rebote
    title: str
    body: str
    link: str | None = None


Channel = Callable[[Notification], None]


@dataclass
class Notifier:
    channels: list[Channel]
    cooldown_s: float = 900.0
    base_url: str | None = None  # URL del tablero, para incluir links
    clock: Callable[[], float] = time.monotonic
    _sent: dict[str, float] = field(default_factory=dict)

    def build(self, topic: str, payload: dict[str, Any]) -> Notification | None:
        kind = topic.rsplit("/", 1)[-1]
        if kind == "alarms":
            alarm = payload.get("alarm", {})
            if payload.get("event") in ("raised", "escalated") and alarm.get("severity") == "critical":
                return Notification(
                    key=f"alarm:{alarm.get('asset_code')}:{alarm.get('rule_code')}",
                    title=f"ALARMA CRÍTICA · {alarm.get('asset_name')} ({alarm.get('asset_code')})",
                    body=alarm.get("message", ""),
                    link=self._link("alarmas"),
                )
        elif kind == "twin" and payload.get("type") == "recommendation":
            rec = payload.get("recommendation", {})
            if rec.get("priority") == "urgent" and rec.get("status") == "open":
                return Notification(
                    key=f"rec:{rec.get('id')}",
                    title=f"MANTENIMIENTO URGENTE · {rec.get('asset_code')}",
                    body=f"{rec.get('action')}\n{rec.get('reason')}\nVence: {rec.get('due_by')}",
                    link=self._link("recomendaciones"),
                )
        elif kind == "events" and payload.get("severity") == "critical":
            return Notification(
                key=f"event:{payload.get('asset_code')}:{payload.get('type')}",
                title=f"DETECCIÓN CRÍTICA · {payload.get('asset_code')}",
                body=payload.get("message", ""),
                link=self._link(f"activo/{payload.get('asset_code')}"),
            )
        return None

    def _link(self, path: str) -> str | None:
        return f"{self.base_url.rstrip('/')}/#/{path}" if self.base_url else None

    def handle(self, topic: str, payload: dict[str, Any]) -> Notification | None:
        note = self.build(topic, payload)
        if note is None:
            return None
        now = self.clock()
        last = self._sent.get(note.key)
        if last is not None and now - last < self.cooldown_s:
            return None
        self._sent[note.key] = now
        for channel in self.channels:
            try:
                channel(note)
            except Exception:  # noqa: BLE001 - un canal caído no debe frenar a los demás
                log.exception("Falló el envío de la notificación por %s", getattr(channel, "__name__", channel))
        return note


# --- canales ---------------------------------------------------------------------------------


def log_channel(note: Notification) -> None:
    log.warning("NOTIFICACIÓN: %s | %s", note.title, note.body.replace("\n", " | "))


def webhook_channel(url: str) -> Channel:
    """Webhook entrante de Teams, Slack o Google Chat (todos aceptan {"text": ...})."""

    def send(note: Notification) -> None:
        text = f"**{note.title}**\n{note.body}" + (f"\n{note.link}" if note.link else "")
        req = urllib.request.Request(
            url, data=json.dumps({"text": text}).encode(), headers={"Content-Type": "application/json"}, method="POST"
        )
        urllib.request.urlopen(req, timeout=10).read()  # noqa: S310 - URL de configuración

    send.__name__ = "webhook"
    return send


def email_channel(host: str, port: int, sender: str, recipients: list[str], user: str | None = None,
                  password: str | None = None, starttls: bool = True) -> Channel:
    def send(note: Notification) -> None:
        msg = EmailMessage()
        msg["Subject"] = f"[MineVision] {note.title}"
        msg["From"] = sender
        msg["To"] = ", ".join(recipients)
        msg.set_content(note.body + (f"\n\nVer en el tablero: {note.link}" if note.link else ""))
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            if starttls:
                smtp.starttls()
            if user:
                smtp.login(user, password or "")
            smtp.send_message(msg)

    send.__name__ = "email"
    return send


def channels_from_settings() -> list[Channel]:
    channels: list[Channel] = [log_channel]
    if settings.notify_webhook_url:
        channels.append(webhook_channel(settings.notify_webhook_url))
    if settings.smtp_host and settings.notify_email_to:
        channels.append(
            email_channel(
                settings.smtp_host,
                settings.smtp_port,
                settings.smtp_from,
                [r.strip() for r in settings.notify_email_to.split(",") if r.strip()],
                settings.smtp_user,
                settings.smtp_password,
                settings.smtp_starttls,
            )
        )
    return channels


def main() -> None:  # pragma: no cover - glue con MQTT, se prueba con docker compose
    import paho.mqtt.client as mqtt

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    notifier = Notifier(channels_from_settings(), settings.notify_cooldown_min * 60, settings.dashboard_url)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="mvtwin-notify")
    client.on_connect = lambda c, *_: c.subscribe([("mvt/+/alarms", 1), ("mvt/+/twin", 1), ("mvt/+/events", 1)])

    def on_message(_c, _u, msg):
        try:
            notifier.handle(msg.topic, json.loads(msg.payload))
        except json.JSONDecodeError:
            log.warning("Mensaje no JSON en %s", msg.topic)

    client.on_message = on_message
    client.connect_async(settings.mqtt_host or "localhost", settings.mqtt_port, keepalive=30)
    log.info("Notificaciones: %d canales", len(notifier.channels))
    client.loop_forever(retry_first_connection=True)


if __name__ == "__main__":  # pragma: no cover
    main()
