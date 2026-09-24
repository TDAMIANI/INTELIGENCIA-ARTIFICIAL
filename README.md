# INTELIGENCIA-ARTIFICIAL

## MineVision Twin

Visión artificial + gemelos digitales para el **mantenimiento predictivo en plantas mineras**.
Cámaras térmicas y RGB, datos del PLC y un gemelo digital de cada equipo detectan fallas
incipientes y proponen el mantenimiento **antes** de la parada no programada.

- Plan del proyecto: [docs/PLAN_VISION_GEMELO_DIGITAL.md](docs/PLAN_VISION_GEMELO_DIGITAL.md)
- Estado: **MVP completo (sprints 1 a 6)**. Es la base para el piloto en planta (fase 3 del plan).

![Tablero: vista de planta](docs/img/tablero_planta.jpg)

### Qué hace

| Capa | Qué resuelve |
|---|---|
| **Edge (visión)** | Termografía: temperatura de cada polín y puntos calientes con foto de evidencia. Mide el desalineamiento de la banda con visión clásica. Detecta personas en zona de riesgo con YOLO. Lee el PLC por OPC UA, en solo lectura. |
| **Plataforma** | Guarda la telemetría en TimescaleDB, con compresión y retención. Dispara alarmas con confirmación e histéresis. Guarda las evidencias en S3. |
| **Gemelo digital** | Índice de salud 0–100 por componente, explicable y propagado por la jerarquía. Tendencia y tiempo estimado a la zona crítica. Recomendaciones de mantenimiento sin duplicados. |
| **Tablero web** | Planta, gemelo de la correa con los 40 polines, centro de alarmas con fotos, plan de mantenimiento y galería de visión. Todo en vivo. |
| **Integración** | Usuarios con roles (local o SSO por OIDC/Keycloak). Órdenes de trabajo al CMMS (CSV con formato SAP PM o webhook). Avisos por email y Teams/Slack. |

![Gemelo digital de la correa](docs/img/tablero_gemelo_correa.jpg)

### Estructura

```
config/plant.yaml   Planta demo: activos (ISO 14224), FMEA, alarmas, indicadores de salud y reglas de mantenimiento
platform/           API, ingesta, alarmas, gemelo, autenticación e integraciones   [Python · paquete mvtwin]
edge/               Gateway de planta: visión artificial y lectura del PLC           [Python · paquete mvedge]
simulator/          Correa con fallas inyectables, cámaras virtuales y PLC OPC UA    [Python · paquete mvsim]
web/                Tablero                                                          [React + TypeScript]
infra/              docker compose del entorno completo y usuarios de demo
ml/                 Entrenamiento de modelos propios (próxima fase, con imágenes reales)
```

### Levantarlo

Requiere Docker.

```bash
make up        # o: docker compose -f infra/docker-compose.yml up -d --build
```

Abrir **http://localhost:8080** e ingresar con un usuario de demo:

| Usuario | Contraseña | Puede |
|---|---|---|
| `operador` | `operador-demo` | ver todo y confirmar alarmas |
| `planificador` | `planificador-demo` | + planificar, descartar o cerrar recomendaciones y exportar órdenes de trabajo |
| `confiabilidad` | `confiabilidad-demo` | + editar activos |
| `visor` | `visor-demo` | solo consulta |
| `admin` | `admin-demo` | todo |

Para ver fallas, se inyectan en el simulador (con `mosquitto_pub` o desde el dashboard de EMQX en http://localhost:18083):

```bash
# Polín 12 con rodamiento trabado (se calienta en 5 minutos)
mosquitto_pub -t mvt/mina-demo/sim/cmd -m '{"action":"inject","kind":"idler_bearing","target":12,"ramp_s":300}'
# Desbalance del motor · banda desalineada · parada programada · limpiar todo
mosquitto_pub -t mvt/mina-demo/sim/cmd -m '{"action":"inject","kind":"motor_imbalance","ramp_s":300}'
mosquitto_pub -t mvt/mina-demo/sim/cmd -m '{"action":"inject","kind":"belt_misalignment","severity":0.6}'
mosquitto_pub -t mvt/mina-demo/sim/cmd -m '{"action":"stop"}'
mosquitto_pub -t mvt/mina-demo/sim/cmd -m '{"action":"clear"}'
```

En unos minutos se ve la secuencia completa:
1. El edge detecta el polín caliente en la imagen térmica y guarda la foto.
2. Salta la alarma.
3. El índice de salud baja.
4. Aparece la recomendación de cambio, con el plazo adelantado según la tendencia.
5. Llega el aviso a los canales de notificación.

| Servicio | URL |
|---|---|
| Tablero | http://localhost:8080 |
| API (Swagger) | http://localhost:8000/docs |
| EMQX (MQTT) | http://localhost:18083 (admin / public) |
| PLC simulado (OPC UA) | `opc.tcp://localhost:4840/minevision/` (se puede explorar con UaExpert) |
| Cámaras RTSP de prueba | `rtsp://localhost:8554/cv-201-rgb-01` |
| S3 (evidencias) | http://localhost:8333 |

### Arquitectura

```
                 PLANTA (edge)                                        PLATAFORMA
┌──────────────────────────────────────────┐        ┌────────────────────────────────────────────────┐
│ cámara térmica ─┐                        │        │ ingestion ─► TimescaleDB (telemetría, alarmas,  │
│ cámara RGB ─────┼─► edge: visión ────────┼─MQTT──►│              eventos, salud, recomendaciones)   │
│ PLC (OPC UA) ───┘   + lectura del PLC    │        │ twin ─► índice de salud + recomendaciones       │
│                     └─ fotos ─► S3 ◄─────┼────────│ notify ─► email / Teams / Slack                 │
└──────────────────────────────────────────┘        │ api (auth + roles) ─► web (nginx) ─► navegador  │
                                                    │                      └─ CMMS (CSV / webhook)    │
                                                    └────────────────────────────────────────────────┘
```

- **Inferencia en el edge:** por la red de la mina viajan resultados y fotos puntuales, no video continuo.
- **Un solo formato de datos:** simulador y equipos reales publican lo mismo (`mvt/{sitio}/{sensor}/telemetry` y `/event`). Pasar a la planta real no cambia la plataforma.
- **Solo lectura del PLC:** el sistema nunca escribe en el control. Es lo que pide IEC 62443 en la separación de redes OT e IT.

### Gemelo digital

```
HI = 100 − Σ (peso × severidad)      severidad 0–100 según la curva de cada indicador (config/plant.yaml)
```

| Indicador | Curva de severidad |
|---|---|
| Polín más caliente que sus vecinos (ΔT) | 5 °C → 0 · 15 °C → 40 · 30 °C → 80 · 45 °C → 100 |
| Vibración del motor | Zonas de ISO 10816 clase III: A → 0 · B → 30 · C → 70 · D → 100 |
| Desalineamiento de la banda | 10 mm → 0 · 30 mm → 40 · 50 mm → 80 · 80 mm → 100 |

- **Explicable:** cada índice muestra qué indicador lo bajó y cuánto aportó.
- **Jerarquía:** la correa, el área y la planta toman el índice de su peor componente.
- **Contexto de operación:** con la correa detenida no se recalcula. Un polín que se enfría porque la correa paró no está reparado.
- **Tendencia:** con la última hora se estima en cuántas horas el indicador llega a la zona crítica, y el plazo de la recomendación se adelanta.
- **Recomendaciones:**
  - hay una sola pendiente por activo e indicador, y sube de prioridad si la falla empeora;
  - quedan vinculadas al modo de falla del FMEA;
  - una descartada no se repite por 24 h, salvo que empeore.

### Seguridad e integraciones (Sprint 6)

- **Usuarios y roles:** `viewer`, `operator`, `planner`, `engineer` y `admin`.
  - *Modo local:* usuarios en un YAML con contraseñas scrypt y tokens JWT.
  - *Modo OIDC:* el SSO de la empresa (Keycloak, Azure AD), con `MVT_AUTH_MODE=oidc`, `MVT_OIDC_JWKS_URL` y `MVT_OIDC_ISSUER`.
  - Lo que queda registrado en cada acción es el nombre del usuario del token, no uno que mande el navegador.
- **CMMS:**
  - Exportación en `GET /api/v1/work-orders/export.csv`, con formato de aviso SAP PM: prioridad 1–4, ubicación técnica, texto breve de 40 caracteres y modo de falla.
  - Con `MVT_CMMS_WEBHOOK_URL`, al planificar una recomendación se crea la orden en el CMMS y se guarda su número. Si el CMMS falla, la recomendación no cambia de estado.
- **Notificaciones:**
  - Avisan de alarmas críticas, mantenimiento urgente y detecciones críticas.
  - Los canales son email (`MVT_SMTP_*`) y Teams/Slack (`MVT_NOTIFY_WEBHOOK_URL`).
  - Tienen anti-rebote: el mismo aviso no se repite antes de 15 minutos.
- **Datos:** la telemetría se comprime a los 7 días y se borra al año. El historial de salud se conserva 3 años.

### API (detalle en http://localhost:8000/docs)

| Método | Ruta | Para qué |
|---|---|---|
| POST | `/api/v1/auth/login` · GET `/api/v1/auth/me` | Sesión |
| GET | `/api/v1/assets`, `/assets/tree`, `/assets/{code}` | Jerarquía y ficha de activos |
| POST / PATCH / DELETE | `/api/v1/assets[/{code}]` | Alta, edición y baja de activos (engineer) |
| GET | `/api/v1/telemetry?asset=…&metric=…&bucket=60` · `/telemetry/latest` | Series de tiempo y últimos valores |
| GET · POST | `/api/v1/alarms` · `/alarms/{id}/ack` | Alarmas y confirmación (operator) |
| GET | `/api/v1/events` · `/events/{id}/snapshot` | Detecciones de visión y su foto |
| GET | `/api/v1/assets/{code}/health` · `/health/history` | Índice de salud explicado y su historia |
| GET · PATCH | `/api/v1/recommendations[/{id}]` | Plan de mantenimiento (planner) |
| GET | `/api/v1/work-orders/export.csv` | Órdenes de trabajo para el CMMS (planner) |
| WS | `/ws/alarms` · `/ws/events` · `/ws/twin` · `/ws/telemetry/{sensor}` | Datos en vivo |

### Desarrollo y tests

```bash
pip install -r platform/requirements-dev.txt -r simulator/requirements-dev.txt -r edge/requirements-dev.txt
make test                                  # plataforma, simulador y edge
cd web && npm install && npm test          # tablero
# Plataforma también contra PostgreSQL/TimescaleDB:
MVT_TEST_PG_URL=postgresql+psycopg://mvt:mvt@localhost:5432/mvt_test make test-platform
# Prueba de YOLO real: pip install -r edge/requirements-yolo.txt
```

El CI de GitHub Actions corre todo en cada push, con TimescaleDB real para la plataforma.

Más detalle de cada parte: [edge/README.md](edge/README.md) · [web/README.md](web/README.md)

### Próximos pasos (fase de piloto)

1. **Datos reales:**
   - instalar una cámara térmica radiométrica y una RGB en un tramo de correa;
   - dibujar los ROIs sobre imágenes reales;
   - calibrar la escala de la banda.
2. **Modelo propio de daños en banda:** etiquetar las imágenes con CVAT, entrenar YOLO en `ml/` y exportarlo a TensorRT para el Jetson.
3. **Integraciones reales:** conectar el SSO de la empresa, el webhook del CMMS y los canales de aviso.
4. **Medir los KPIs del plan contra la línea base:** paradas no programadas, MTBF, detección anticipada y falsos positivos.
