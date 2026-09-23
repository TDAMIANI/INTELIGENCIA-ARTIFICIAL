# INTELIGENCIA-ARTIFICIAL

## MineVision Twin

Visión artificial + gemelos digitales para mantenimiento predictivo en plantas mineras.

- Plan del proyecto: [docs/PLAN_VISION_GEMELO_DIGITAL.md](docs/PLAN_VISION_GEMELO_DIGITAL.md)
- Estado: **Sprint 2 completo** (ingesta MQTT → TimescaleDB, alarmas en tiempo real, API de series de tiempo y WebSocket)

### Estructura

```
config/plant.yaml   Jerarquía de activos de la planta demo (correa CV-201 con 40 polines)
platform/           API FastAPI, modelo de datos, ingesta MQTT y alarmas           [paquete mvtwin]
simulator/          Simulador de telemetría con fallas inyectables + cámaras RTSP  [paquete mvsim]
infra/              docker-compose del entorno de desarrollo
edge/ web/ ml/      Se desarrollan en los próximos sprints
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
simulador / edge ──MQTT──► ingestion ──► TimescaleDB (telemetry, alarms)
                              │
                              └─ cambios de alarmas ──MQTT──► API ──WebSocket──► navegador
```

### API (detalle en http://localhost:8000/docs)

| Método | Ruta | Para qué |
|---|---|---|
| GET | `/api/v1/assets`, `/assets/tree`, `/assets/{code}` | Jerarquía y ficha de activos |
| POST / PATCH / DELETE | `/api/v1/assets[/{code}]` | Alta, edición y baja de activos |
| GET | `/api/v1/telemetry?asset=CV-201-MOT&metric=vibration_rms_mm_s&bucket=60` | Serie de tiempo (cruda o promediada por intervalo) |
| GET | `/api/v1/telemetry/latest?asset=CV-201-IDL&subtree=true` | Último valor de cada métrica (p. ej. los 40 polines) |
| GET | `/api/v1/alarms?status=active&asset=CV-201` | Alarmas (incluye las de los activos hijos) |
| POST | `/api/v1/alarms/{id}/ack` | El operador confirma que vio la alarma |
| WS | `/ws/alarms` | Alarmas en vivo (raised / escalated / cleared) |
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
| `CV-201-RGB-01` | `belt_edge_offset_mm` |

El edge real (Sprint 3) publicará los mismos tópicos, así que la plataforma no distingue entre datos simulados y reales.

### Desarrollo local sin Docker

```bash
pip install -r platform/requirements-dev.txt -r simulator/requirements-dev.txt
make test
# Para correr también los tests contra PostgreSQL/TimescaleDB:
MVT_TEST_PG_URL=postgresql+psycopg://mvt:mvt@localhost:5432/mvt_test make test-platform
```
