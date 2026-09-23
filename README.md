# INTELIGENCIA-ARTIFICIAL

## MineVision Twin

Visión artificial + gemelos digitales para mantenimiento predictivo en plantas mineras.

- Plan del proyecto: [docs/PLAN_VISION_GEMELO_DIGITAL.md](docs/PLAN_VISION_GEMELO_DIGITAL.md)
- Estado: **Sprint 1 completo** (esqueleto, simulador y modelo de datos de activos)

### Estructura

```
config/plant.yaml   Jerarquía de activos de la planta demo (correa CV-201 con 40 polines)
platform/           API FastAPI + modelo de datos (SQLAlchemy/Alembic) + seed      [paquete mvtwin]
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
| `CV-201-TH-01` | `idler_max_temp_c` (un valor por polín `CV-201-IDL-01..40`) |
| `CV-201-RGB-01` | `belt_edge_offset_mm` |

El edge real (Sprint 3) publicará los mismos tópicos, así que la plataforma no distingue entre datos simulados y reales.

### Desarrollo local sin Docker

```bash
pip install -r platform/requirements-dev.txt -r simulator/requirements-dev.txt
make test
```
