"""Fuentes de imágenes: cámaras reales (RTSP) y cámaras virtuales del simulador (MQTT)."""

import logging
import queue
import threading
import time
from typing import Any, Protocol

import cv2
import numpy as np

log = logging.getLogger("mvedge.sources")


class FrameSource(Protocol):
    def read(self, timeout: float = 5.0) -> np.ndarray | None:
        """Devuelve la imagen más reciente, o None si no llegó ninguna en `timeout` segundos."""
        ...

    def close(self) -> None: ...


class RtspSource:
    """Cámara IP por RTSP (OpenCV + FFmpeg). Se reconecta sola si se corta el stream.

    Un hilo lee continuamente y se queda solo con la última imagen, para que el análisis
    trabaje siempre sobre lo más reciente aunque sea más lento que la cámara.
    """

    def __init__(self, url: str, reconnect_s: float = 3.0):
        self.url = url
        self.reconnect_s = reconnect_s
        self._latest: queue.Queue[np.ndarray] = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"rtsp:{url}", daemon=True)
        self._thread.start()

    def _run(self) -> None:  # pragma: no cover - requiere un servidor RTSP
        while not self._stop.is_set():
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                log.warning("No se pudo abrir %s, reintento en %.0f s", self.url, self.reconnect_s)
                time.sleep(self.reconnect_s)
                continue
            log.info("Stream abierto: %s", self.url)
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok:
                    log.warning("Se cortó el stream %s", self.url)
                    break
                _replace(self._latest, frame)
            cap.release()

    def read(self, timeout: float = 5.0) -> np.ndarray | None:
        try:
            return self._latest.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self._stop.set()


class MqttFrameSource:
    """Imágenes codificadas (PNG de 16 bits para térmica, JPEG para RGB) recibidas por MQTT."""

    def __init__(self, client: Any, topic: str, flags: int = cv2.IMREAD_UNCHANGED):
        self.topic = topic
        self.flags = flags
        self._latest: queue.Queue[bytes] = queue.Queue(maxsize=1)
        client.message_callback_add(topic, lambda _c, _u, msg: _replace(self._latest, msg.payload))
        client.subscribe(topic, qos=0)

    def feed(self, data: bytes) -> None:
        """Entrada directa (tests)."""
        _replace(self._latest, data)

    def read(self, timeout: float = 5.0) -> np.ndarray | None:
        try:
            data = self._latest.get(timeout=timeout)
        except queue.Empty:
            return None
        frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), self.flags)
        if frame is None:
            log.warning("Imagen inválida en %s", self.topic)
        return frame

    def close(self) -> None:
        pass


def _replace(q: queue.Queue, item: Any) -> None:
    """Deja en la cola solo el elemento más nuevo."""
    try:
        q.get_nowait()
    except queue.Empty:
        pass
    try:
        q.put_nowait(item)
    except queue.Full:
        pass
