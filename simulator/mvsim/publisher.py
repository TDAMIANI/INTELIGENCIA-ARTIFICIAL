"""Publica la telemetría simulada de una correa por MQTT.

Ejemplos:
  # Ver los mensajes en consola, sin broker, 10x más rápido, con un polín fallando a los 60 s:
  python -m mvsim.publisher --dry-run --speedup 10 --duration 300 \
      --fault idler_bearing:target=12,start=60,ramp=120

  # Publicar al broker de docker compose:
  python -m mvsim.publisher --broker localhost

  # Publicar imágenes de las cámaras virtuales en lugar de la telemetría de cámaras
  # (el edge las analiza y publica esa telemetría):
  python -m mvsim.publisher --broker localhost --cameras frames

  # Exponer los tags del PLC por OPC UA (los lee el edge) en lugar de publicarlos por MQTT:
  python -m mvsim.publisher --broker localhost --cameras frames --plc opcua

Comandos en tiempo de ejecución (JSON al tópico mvt/{site}/sim/cmd):
  {"action": "inject", "kind": "idler_bearing", "target": 12, "ramp_s": 300}
  {"action": "inject", "kind": "motor_imbalance", "start_s": 30}
  {"action": "clear"}            | {"action": "clear", "kind": "motor_imbalance"}
  {"action": "stop"}             | {"action": "start"}
"""

import argparse
import json
import logging
import queue
import sys
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from mvsim.conveyor import ConveyorConfig, ConveyorSimulator, Fault, FaultKind
from mvsim.messages import camera_codes, command_topic, frame_topic, snapshot_messages

log = logging.getLogger("mvsim")


def parse_fault(spec: str, now_s: float = 0.0) -> Fault:
    """Parsea 'kind[:clave=valor,...]', p. ej. 'idler_bearing:target=12,start=60,ramp=300'."""
    kind, _, rest = spec.partition(":")
    params: dict[str, str] = {}
    for item in filter(None, rest.split(",")):
        key, sep, value = item.partition("=")
        if not sep:
            raise ValueError(f"Parámetro inválido '{item}' en '{spec}' (se espera clave=valor)")
        params[key.strip()] = value.strip()
    return fault_from_dict({"kind": kind, **params}, now_s)


def fault_from_dict(data: dict[str, Any], now_s: float = 0.0) -> Fault:
    """Crea una falla; `start`/`start_s` es relativo al instante actual de la simulación."""
    aliases = {"start": "start_s", "ramp": "ramp_s"}
    data = {aliases.get(k, k): v for k, v in data.items()}
    unknown = set(data) - {"kind", "target", "start_s", "ramp_s", "severity"}
    if unknown:
        raise ValueError(f"Parámetros desconocidos: {sorted(unknown)}")
    return Fault(
        kind=FaultKind(data["kind"]),
        target=int(data["target"]) if data.get("target") is not None else None,
        start_s=now_s + float(data.get("start_s", 0.0)),
        ramp_s=float(data.get("ramp_s", 600.0)),
        severity=float(data.get("severity", 1.0)),
    )


def apply_command(sim: ConveyorSimulator, cmd: dict[str, Any]) -> None:
    action = cmd.get("action")
    if action == "inject":
        sim.inject(fault_from_dict({k: v for k, v in cmd.items() if k != "action"}, sim.t))
    elif action == "clear":
        sim.clear_faults(FaultKind(cmd["kind"]) if cmd.get("kind") else None)
    elif action == "stop":
        sim.set_running(False)
    elif action == "start":
        sim.set_running(True)
    else:
        raise ValueError(f"Acción desconocida: {action!r}")
    log.info("Comando aplicado: %s", cmd)


def _connect_mqtt(args: argparse.Namespace, commands: "queue.Queue[dict[str, Any]]"):
    import paho.mqtt.client as mqtt

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"mvsim-{args.conveyor}")
    cmd_topic = command_topic(args.site)

    def on_connect(client, _userdata, _flags, reason_code, _props):
        log.info("Conectado a MQTT %s:%s (%s); comandos en %s", args.broker, args.port, reason_code, cmd_topic)
        client.subscribe(cmd_topic, qos=1)

    def on_message(_client, _userdata, msg):
        try:
            commands.put(json.loads(msg.payload))
        except json.JSONDecodeError:
            log.warning("Comando no es JSON válido: %r", msg.payload)

    client.on_connect = on_connect
    client.on_message = on_message
    for attempt in range(1, 31):
        try:
            client.connect(args.broker, args.port, keepalive=30)
            break
        except OSError as exc:
            log.warning("Broker no disponible (%s), reintento %d/30", exc, attempt)
            time.sleep(2)
    else:
        raise SystemExit(f"No se pudo conectar al broker {args.broker}:{args.port}")
    client.loop_start()
    return client


def run(args: argparse.Namespace) -> None:
    sim = ConveyorSimulator(ConveyorConfig(code=args.conveyor, idler_count=args.idlers), seed=args.seed)
    for spec in args.fault:
        sim.inject(parse_fault(spec))

    commands: queue.Queue[dict[str, Any]] = queue.Queue()
    client = None if args.dry_run else _connect_mqtt(args, commands)

    frames = args.cameras == "frames"
    exclude = frozenset(camera_codes(sim)) if frames else frozenset()
    plc = None
    if args.plc == "opcua":
        from mvsim.plc_server import PlcServer

        plc = PlcServer(args.opcua_endpoint, prefix=args.conveyor.replace("-", "")).start()
        exclude |= {f"{args.conveyor}-PLC"}
    if frames:
        import numpy as np

        from mvsim import cameras

        frame_rng = np.random.default_rng(args.seed)

    sim_dt = args.interval * args.speedup
    t0 = datetime.now(UTC)
    try:
        while args.duration <= 0 or sim.t < args.duration:
            while not commands.empty():
                try:
                    apply_command(sim, commands.get_nowait())
                except (ValueError, KeyError) as exc:
                    log.warning("Comando inválido: %s", exc)

            snap = sim.step(sim_dt)
            ts = t0 + timedelta(seconds=sim.t)
            if plc is not None:
                plc.update(snap)
            for topic, payload in snapshot_messages(args.site, sim, snap, ts, exclude):
                if client is None:
                    print(topic, json.dumps(payload, ensure_ascii=False), flush=True)
                else:
                    client.publish(topic, json.dumps(payload), qos=0)
            if frames:
                th_code, rgb_code = camera_codes(sim)
                images = {
                    th_code: cameras.encode_thermal(cameras.render_thermal(sim, snap, frame_rng)),
                    rgb_code: cameras.encode_rgb(cameras.render_rgb(snap, frame_rng)),
                }
                for code, data in images.items():
                    if client is None:
                        print(frame_topic(args.site, code), f"<imagen {len(data)} bytes>", flush=True)
                    else:
                        client.publish(frame_topic(args.site, code), data, qos=0)
            if not args.dry_run:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        if plc is not None:
            plc.stop()
        if client is not None:
            client.loop_stop()
            client.disconnect()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Simulador de telemetría de correa transportadora")
    p.add_argument("--broker", default="localhost")
    p.add_argument("--port", type=int, default=1883)
    p.add_argument("--site", default="mina-demo")
    p.add_argument("--conveyor", default="CV-201")
    p.add_argument("--idlers", type=int, default=40)
    p.add_argument("--interval", type=float, default=1.0, help="segundos reales entre publicaciones")
    p.add_argument("--speedup", type=float, default=1.0, help="segundos simulados por segundo real")
    p.add_argument("--duration", type=float, default=0.0, help="segundos simulados (0 = infinito)")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--fault", action="append", default=[], help="kind[:target=N,start=S,ramp=S,severity=X]")
    p.add_argument(
        "--cameras",
        choices=["telemetry", "frames"],
        default="telemetry",
        help="telemetry: publica los valores de las cámaras ya calculados; "
        "frames: publica imágenes para que las analice el edge",
    )
    p.add_argument(
        "--plc",
        choices=["mqtt", "opcua"],
        default="mqtt",
        help="mqtt: publica los tags del PLC por MQTT; opcua: los expone en un servidor OPC UA (como un PLC real)",
    )
    p.add_argument("--opcua-endpoint", default="opc.tcp://0.0.0.0:4840/minevision/")
    p.add_argument("--dry-run", action="store_true", help="imprime en consola en lugar de publicar")
    return p


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    run(build_parser().parse_args(argv))


if __name__ == "__main__":
    main()
