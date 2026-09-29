"""Integración con el CMMS (SAP PM, IBM Maximo, GMAO local).

Una recomendación aceptada se convierte en un aviso u orden de trabajo del CMMS. Hay dos caminos:
- Exportación CSV (siempre disponible): el planificador la carga en el CMMS. Es el camino
  del piloto mientras se tramita el acceso a la API del CMMS.
- Webhook (MVT_CMMS_WEBHOOK_URL): al aceptar la recomendación se envía un JSON a un servicio
  intermedio (SAP PI/CPI, Maximo Integration Framework, Power Automate...). Si responde con
  {"work_order_ref": "..."}, ese número queda guardado en la recomendación.

Los campos siguen la estructura de un aviso de mantenimiento de SAP PM (tipo M2).
"""

import csv
import io
import json
import urllib.request
from collections.abc import Callable
from datetime import datetime
from typing import Any

from mvtwin.models import Asset, Recommendation

# Prioridad de SAP PM: 1 muy alta, 2 alta, 3 media, 4 baja
SAP_PRIORITY = {"urgent": "1", "high": "2", "medium": "3", "low": "4"}

CSV_COLUMNS = [
    "referencia_mvt",
    "tipo_aviso",
    "prioridad_sap",
    "prioridad",
    "equipo",
    "ubicacion_tecnica",
    "texto_breve",
    "texto_largo",
    "modo_de_falla",
    "fin_requerido",
    "estado",
    "planificado_por",
    "orden_cmms",
]


def functional_location(asset: Asset) -> str:
    """Ubicación técnica: códigos de los ancestros (planta > área > sistema)."""
    chain = []
    node = asset.parent
    while node is not None:
        chain.append(node.code)
        node = node.parent
    return "/".join(reversed(chain))


def work_order_fields(rec: Recommendation) -> dict[str, Any]:
    fm = rec.failure_mode
    long_text = rec.reason
    if fm is not None:
        long_text += f" | Modo de falla: {fm.name} ({fm.code})"
    long_text += f" | Generado por MineVision Twin (recomendación {rec.id}, regla {rec.rule_code})"
    return {
        "referencia_mvt": f"MVT-REC-{rec.id}",
        "tipo_aviso": "M2",
        "prioridad_sap": SAP_PRIORITY.get(rec.priority, "3"),
        "prioridad": rec.priority,
        "equipo": rec.asset.code,
        "ubicacion_tecnica": functional_location(rec.asset),
        "texto_breve": rec.action[:40],  # SAP limita el texto breve a 40 caracteres
        "texto_largo": f"{rec.action}. {long_text}",
        "modo_de_falla": fm.code if fm else "",
        "fin_requerido": _iso(rec.due_by),
        "estado": rec.status.value,
        "planificado_por": rec.updated_by or "",
        "orden_cmms": rec.work_order_ref or "",
    }


def _iso(ts: datetime) -> str:
    return ts.isoformat(timespec="minutes")


def to_csv(recs: list[Recommendation]) -> str:
    """CSV con ';' y BOM UTF-8: Excel en español lo abre bien sin configurar nada."""
    buf = io.StringIO()
    buf.write("﻿")
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, delimiter=";", lineterminator="\r\n")
    writer.writeheader()
    for rec in recs:
        writer.writerow(work_order_fields(rec))
    return buf.getvalue()


Sender = Callable[[str, dict[str, Any]], dict[str, Any]]


def http_json_sender(url: str, payload: dict[str, Any], timeout_s: float = 10.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as res:  # noqa: S310 - URL de configuración
        body = res.read()
    try:
        return json.loads(body) if body else {}
    except json.JSONDecodeError:
        return {}


class CmmsError(RuntimeError):
    pass


def send_work_order(rec: Recommendation, url: str, sender: Sender = http_json_sender) -> str | None:
    """Envía la orden al CMMS; devuelve su número si el CMMS lo informa."""
    try:
        response = sender(url, work_order_fields(rec))
    except Exception as exc:  # noqa: BLE001 - errores de red o del CMMS
        raise CmmsError(f"No se pudo crear la orden en el CMMS: {exc}") from exc
    ref = response.get("work_order_ref") or response.get("id") or response.get("number")
    return str(ref) if ref else None
