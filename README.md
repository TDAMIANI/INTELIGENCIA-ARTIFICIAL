# INTELIGENCIA-ARTIFICIAL

## MineVision Twin

Visión artificial + gemelos digitales para mantenimiento predictivo en plantas mineras.

- Plan del proyecto: [docs/PLAN_VISION_GEMELO_DIGITAL.md](docs/PLAN_VISION_GEMELO_DIGITAL.md)
- Estado: **Sprint 3 completo** (visión artificial en el edge: termografía por polín, desalineamiento de banda, YOLO y eventos con imagen de evidencia)

### Estructura

```
config/plant.yaml   Jerarquía de activos de la planta demo (correa CV-201 con 40 polines)
platform/           API FastAPI, modelo de datos, ingesta MQTT y alarmas           [paquete mvtwin]
simulator/          Simulador de la correa con fallas inyectables y cámaras virtuales [paquete mvsim]
edge/               Gateway edge: visión artificial sobre las cámaras            [paquete mvedge]
infra/              docker-compose del entorno de desarrollo
web/ ml/            Se desarrollan en los próximos sprints
```

### Levantar el entorno

Requiere Docker.

```bash
make up        # o: docker compose -f infra/docker-compose.yml up -d --build
```

| Servicio | URL | Qué es |
|---|---|---|
| API | http://localhost:8000/docs | Swagger de la API del gemelo |
| EMQX | http://localhost:18083 (admin / public) | Broker MQTT; la telemetría llega a `mvt/#` |
| RTSP | `rtsp://localhost:8554/cv-201-rgb-01` y `.../cv-201-th-01` | Cámaras simuladas (se ven con VLC) |
| S3 | http://localhost:8333 (`mvt-dev` / `mvt-dev-secret`) | Almacenamiento de objetos (SeaweedFS) |
| PostgreSQL + TimescaleDB | `localhost:5432` (mvt / mvt) | Base de datos |

Al arrancar, el servicio `migrate` aplica las migraciones y carga `config/plant.yaml`.
El servicio `ingestion` guarda toda la telemetría en TimescaleDB y evalúa las alarmas.

### Flujo de datos

```
simulador ──imágenes──► edge (visión) ──telemetría + eventos──┐
    │                       └─ foto de evidencia ──► S3       │
    └────── PLC y vibración ─────────────────────────MQTT─────┤
                                                              ▼
                              ingestion ──► TimescaleDB (telemetry, alarms, events)
                                  └─ alarmas y eventos ──MQTT──► API ──WebSocket──► navegador
```

### Visión artificial en el edge

| Cámara | Qué analiza | Técnica | Publica |
|---|---|---|---|
| Térmica `CV-201-TH-01` | Temperatura de cada uno de los 40 polines | Imagen radiométrica, máximo por ROI, ΔT contra vecinos | `max_temp_c` por polín y evento `thermal_hotspot` con foto |
| RGB `CV-201-RGB-01` | Posición de la banda | Visión clásica (bordes con gradiente) | `belt_edge_offset_mm`, `belt_width_mm` |
| RGB `CV-201-RGB-01` | Personas en zona de riesgo (opcional) | YOLO preentrenado | `count_person` y evento `person_in_zone` con foto |

Ejemplo de imagen de evidencia generada por el edge al detectar el polín 12 caliente:

![Evidencia térmica](docs/img/evidencia_termica.jpg)

La configuración (cámaras, ROIs, umbrales, zonas) está en `edge/config/edge.yaml`. Detalle en [edge/README.md](edge/README.md).

### API (detalle en http://localhost:8000/docs)

| Método | Ruta | Para qué |
|---|---|---|
| GET | `/api/v1/assets`, `/assets/tree`, `/assets/{code}` | Jerarquía y ficha de activos |
| POST / PATCH / DELETE | `/api/v1/assets[/{code}]` | Alta, edición y baja de activos |
| GET | `/api/v1/telemetry?asset=CV-201-MOT&metric=vibration_rms_mm_s&bucket=60` | Serie de tiempo (cruda o promediada por intervalo) |
| GET | `/api/v1/telemetry/latest?asset=CV-201-IDL&subtree=true` | Último valor de cada métrica (p. ej. los 40 polines) |
| GET | `/api/v1/alarms?status=active&asset=CV-201` | Alarmas (incluye las de los activos hijos) |
| POST | `/api/v1/alarms/{id}/ack` | El operador confirma que vio la alarma |
| GET | `/api/v1/events?asset=CV-201&type=thermal_hotspot` | Eventos de visión del edge |
| GET | `/api/v1/events/{id}/snapshot` | Imagen de evidencia del evento (JPEG) |
| WS | `/ws/alarms` | Alarmas en vivo (raised / escalated / cleared) |
| WS | `/ws/events` | Eventos de visión en vivo |
| WS | `/ws/telemetry/{sensor_code}` | Telemetría en vivo de un sensor |

### Alarmas

Las reglas están en `config/plant.yaml` (`alarm_rules`). Cada una tiene umbrales de advertencia y críticos, **confirmación** (N muestras seguidas, para no alarmar por un pico de ruido) e **histéresis** (`clear_below`, para que la alarma no se prenda y apague sin parar). Reglas incluidas:

| Regla | Qué detecta |
|---|---|
| `MOT-VIB-ISO10816` | Vibración del motor en zonas C/D de ISO 10816 |
| `IDL-HOTSPOT` | Polín más caliente que sus vecinos (ΔT contra la mediana de los 3 polines de cada lado; no depende de la temperatura ambiente) |
| `BELT-MISALIGN` | Desplazamiento del borde de la banda |

### Simular fallas

El simulador escucha comandos JSON en `mvt/mina-demo/sim/cmd`:

```bash
# Polín 12 con rodamiento trabado (se calienta en 5 minutos)
mosquitto_pub -t mvt/mina-demo/sim/cmd -m '{"action":"inject","kind":"idler_bearing","target":12,"ramp_s":300}'
# Desbalance del motor, desalineamiento de banda, parada programada, limpiar
mosquitto_pub -t mvt/mina-demo/sim/cmd -m '{"action":"inject","kind":"motor_imbalance"}'
mosquitto_pub -t mvt/mina-demo/sim/cmd -m '{"action":"inject","kind":"belt_misalignment","severity":0.6}'
mosquitto_pub -t mvt/mina-demo/sim/cmd -m '{"action":"stop"}'
mosquitto_pub -t mvt/mina-demo/sim/cmd -m '{"action":"clear"}'
```

Sin Docker ni broker, podés ver la telemetría en la consola con `make sim-dry`.

Para usar videos reales de correas, copialos a `simulator/video/samples/` con el nombre de la cámara (por ejemplo, `cv-201-rgb-01.mp4`).

### Tópicos MQTT

`mvt/{site}/{sensor_code}/telemetry` con payload `{"ts", "sensor", "values", "simulated"}`:

| Sensor | Valores |
|---|---|
| `CV-201-PLC` | `running`, `belt_speed_mps`, `motor_current_a`, `load_tph` |
| `CV-201-MOT-VIB` | `vibration_rms_mm_s` |
| `CV-201-TH-01` | `max_temp_c` como objeto `{"CV-201-IDL-01": 31.2, ...}` (un valor por polín) |
| `CV-201-RGB-01` | `belt_edge_offset_mm`, `belt_width_mm`, `belt_detected`, `count_<clase>` |

Eventos de visión: `mvt/{site}/{sensor_code}/event` con `{"ts", "type", "asset", "severity", "message", "value", "snapshot": {"bucket", "key"}, "data"}`.

Las cámaras las analiza el edge. Con `--cameras telemetry` el simulador publica directamente los valores de las cámaras, sin imágenes (útil sin edge). Con `--cameras frames`, que es lo que usa docker compose, publica imágenes en `mvt/{site}/sim/frames/{cámara}`.

### Desarrollo local sin Docker

```bash
pip install -r platform/requirements-dev.txt -r simulator/requirements-dev.txt -r edge/requirements-dev.txt
make test
# Para incluir la prueba de YOLO: pip install -r edge/requirements-yolo.txt
# Para correr también los tests contra PostgreSQL/TimescaleDB:
MVT_TEST_PG_URL=postgresql+psycopg://mvt:mvt@localhost:5432/mvt_test make test-platform
```
