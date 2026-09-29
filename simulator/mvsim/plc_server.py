"""PLC virtual: servidor OPC UA con los tags de la correa, como los expondría el PLC/SCADA real.

Nodos (espacio de nombres urn:minevision:sim, índice 2):
  ns=2;s=CV201.running          Boolean   correa en marcha
  ns=2;s=CV201.belt_speed_mps   Double    velocidad de la banda
  ns=2;s=CV201.motor_current_a  Double    corriente del motor
  ns=2;s=CV201.load_tph         Double    carga transportada

El servidor corre en un hilo propio con su event loop; `update()` se puede llamar desde el
hilo principal del simulador.
"""

import asyncio
import logging
import threading

from asyncua import Server, ua

from mvsim.conveyor import Snapshot

log = logging.getLogger("mvsim.plc")

NAMESPACE = "urn:minevision:sim"
TAGS = {
    "running": ua.VariantType.Boolean,
    "belt_speed_mps": ua.VariantType.Double,
    "motor_current_a": ua.VariantType.Double,
    "load_tph": ua.VariantType.Double,
}


def node_id(prefix: str, tag: str) -> str:
    return f"ns=2;s={prefix}.{tag}"


class PlcServer:
    def __init__(self, endpoint: str = "opc.tcp://0.0.0.0:4840/minevision/", prefix: str = "CV201"):
        self.endpoint = endpoint
        self.prefix = prefix
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._vars: dict[str, object] = {}
        self._error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, name="opcua-server", daemon=True)

    def start(self, timeout: float = 15.0) -> "PlcServer":
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("El servidor OPC UA no arrancó a tiempo")
        if self._error:
            raise RuntimeError(f"No se pudo iniciar el servidor OPC UA: {self._error}")
        return self

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._setup())
        except BaseException as exc:  # noqa: BLE001 - se informa en start()
            self._error = exc
            self._ready.set()
            return
        self._ready.set()
        self._loop.run_forever()

    async def _setup(self) -> None:
        self.server = Server()
        await self.server.init()
        self.server.set_endpoint(self.endpoint)
        self.server.set_server_name("MineVision PLC simulado")
        idx = await self.server.register_namespace(NAMESPACE)
        obj = await self.server.nodes.objects.add_object(ua.NodeId(self.prefix, idx), self.prefix)
        for tag, vtype in TAGS.items():
            initial = False if vtype == ua.VariantType.Boolean else 0.0
            var = await obj.add_variable(ua.NodeId(f"{self.prefix}.{tag}", idx), tag, ua.Variant(initial, vtype))
            self._vars[tag] = var
        await self.server.start()
        log.info("Servidor OPC UA en %s", self.endpoint)

    async def _write(self, values: dict[str, object]) -> None:
        for tag, value in values.items():
            await self._vars[tag].write_value(ua.Variant(value, TAGS[tag]))

    def update(self, snap: Snapshot) -> None:
        values = {
            "running": bool(snap.running),
            "belt_speed_mps": float(snap.belt_speed_mps),
            "motor_current_a": float(snap.motor_current_a),
            "load_tph": float(snap.load_tph),
        }
        asyncio.run_coroutine_threadsafe(self._write(values), self._loop).result(timeout=5)

    def stop(self) -> None:
        if not self._loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(self.server.stop(), self._loop).result(timeout=5)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
