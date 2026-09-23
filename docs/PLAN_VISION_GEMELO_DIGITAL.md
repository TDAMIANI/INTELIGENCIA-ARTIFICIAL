# Plan de proyecto: visión artificial + gemelos digitales para mantenimiento predictivo en minería

> Nombre de trabajo: **MineVision Twin**
> Objetivo: detectar a tiempo fallas incipientes en equipos críticos de una planta minera, combinando cámaras (visibles y térmicas), sensores de la planta y un gemelo digital de cada activo, para pasar del mantenimiento correctivo o por calendario a uno **predictivo y basado en la condición real del equipo**.

---

## 1. Qué problema resolvemos (y cómo medimos el éxito)

En una planta minera (chancado, molienda, flotación, transporte de mineral), las paradas no programadas de correas transportadoras, chancadores, molinos y bombas cuestan entre USD 10.000 y USD 150.000 por hora de producción perdida. Hoy la mayoría de las inspecciones son visuales, manuales y periódicas.

**Propuesta de valor:** cámaras e IA inspeccionan los equipos las 24 horas, el gemelo digital junta ese dato con vibración, temperatura, corriente y el historial de mantenimiento, y el sistema genera **órdenes de trabajo priorizadas antes de que ocurra la falla**.

### KPIs del proyecto

| KPI | Línea base (medir en fase 0) | Meta del piloto (6 meses) |
|---|---|---|
| Paradas no programadas del activo piloto | X h/mes | −30 % |
| MTBF (tiempo medio entre fallas) | medir | +20 % |
| Detección anticipada (falla detectada antes de la parada) | ~0 % | ≥ 60 % de los eventos |
| Falsos positivos por semana | — | < 5 alertas/semana por línea |
| Precisión del modelo de visión (mAP50) | — | ≥ 0,85 en defectos objetivo |
| Tiempo de alerta a OT (orden de trabajo) | horas o días | < 15 min |

---

## 2. Alcance: empezar chico y con el activo que más duele

**No** intentar gemelizar toda la planta al principio. El piloto se hace sobre **una línea de correas transportadoras** (la mayor fuente de paradas en casi toda minera y la más visible para cámaras), y después se escala.

### Casos de uso del piloto (MVP), en orden de prioridad

| # | Caso de uso | Sensor | Técnica de IA | Por qué primero |
|---|---|---|---|---|
| 1 | **Rodillos (polines) calientes o trabados** | Cámara térmica | Detección de puntos calientes + umbral dinámico | Causa n.º 1 de incendios y cortes de correa; fácil de detectar térmicamente |
| 2 | **Daño en la banda** (roturas, desgarros, empalmes abiertos) | Cámara de línea / área RGB de alta velocidad | Detección de objetos y segmentación (YOLO) | Una rotura longitudinal para la línea por días |
| 3 | **Desalineamiento de banda** | Cámara RGB | Detección de bordes + seguimiento de la posición del borde | Genera desgaste y derrame |
| 4 | **Derrame de material / acumulación** | Cámara RGB | Segmentación | Seguridad y limpieza |
| 5 | **Objetos extraños / sobretamaño** (metal, rocas grandes) | Cámara RGB | Detección de objetos | Protege chancadores y bandas |
| 6 | **Personas en zona de riesgo** (opcional) | Cámara RGB | Detección de personas + geocercas | Seguridad; alto impacto, bajo costo marginal |

### Fase 2 (escala): otros activos

- **Chancadores:** desgaste de corazas (visión 3D / LiDAR), temperatura de rodamientos.
- **Molinos SAG/bolas:** termografía de la carcasa, desgaste de revestimientos, fugas.
- **Bombas y motores:** termografía + vibración, fugas visibles.
- **Camiones y palas (mina a cielo abierto):** desgaste de dientes del balde (el diente perdido que entra al chancador es un clásico), estado de neumáticos.

---

## 3. Arquitectura del sistema

```
┌───────────────────────────── PLANTA (Edge / OT) ─────────────────────────────┐
│                                                                              │
│  Cámaras RGB (IP67) ─┐                                                       │
│  Cámaras térmicas ───┼──► Gateway Edge (NVIDIA Jetson Orin / PC industrial)  │
│  Sensores vibración ─┤     • Captura RTSP / GigE Vision                      │
│  PLC / SCADA (OPC UA)┘     • Inferencia IA local (TensorRT)                  │
│                            • Reglas de alarma de primer nivel                │
│                            • Buffer local si se corta la red (store & fwd)   │
│                                   │  MQTT (TLS) solo eventos + métricas       │
│                                   │  + clips cortos, NO video continuo        │
└───────────────────────────────────┼──────────────────────────────────────────┘
                                    │  (DMZ / firewall industrial, IEC 62443)
┌───────────────────────────────────▼──── PLATAFORMA (on-prem o nube) ─────────┐
│                                                                              │
│  Broker MQTT (EMQX) ──► Ingesta (servicio Python)                            │
│                              │                                               │
│         ┌────────────────────┼────────────────────────┐                      │
│         ▼                    ▼                        ▼                      │
│  TimescaleDB           PostgreSQL               MinIO (S3)                   │
│  (series de tiempo:    (activos, jerarquía,     (imágenes, clips,            │
│   temp, vibración,      eventos, OT, usuarios)   datasets, modelos)          │
│   detecciones)                                                               │
│         │                    │                                               │
│         └──────► MOTOR DEL GEMELO DIGITAL ◄───────┘                          │
│                  • Modelo del activo (estado, componentes)                   │
│                  • Índice de salud (Health Index 0–100)                      │
│                  • Detección de anomalías en series de tiempo                │
│                  • Estimación de vida útil remanente (RUL)                   │
│                  • Reglas de mantenimiento → recomendaciones                 │
│                              │                                               │
│                  API REST + WebSocket (FastAPI)                              │
│                              │                                               │
│      ┌───────────────────────┼─────────────────────────┐                     │
│      ▼                       ▼                         ▼                     │
│  Dashboard web          Integración CMMS           Notificaciones            │
│  (React + visor 3D)     (SAP PM / Maximo / GMAO)   (email, Teams, WhatsApp)  │
│                                                                              │
│  MLOps: etiquetado (CVAT) → entrenamiento → registro (MLflow) → deploy edge  │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Decisiones clave de arquitectura (y por qué)

1. **Inferencia en el borde (edge), no en la nube.** Las minas tienen conectividad limitada y el video pesa mucho. En el edge se procesa el video y solo se suben eventos, métricas y clips cortos. Además, reduce la latencia de alarma a menos de 1 s.
2. **MQTT + Sparkplug B** para la telemetría: es el estándar de facto en IIoT, liviano y tolerante a cortes.
3. **OPC UA** para leer del PLC/SCADA (velocidad de la correa, corriente del motor, estados de marcha/parada). Sin este contexto la IA genera falsos positivos (por ejemplo, "la correa está quieta" cuando en realidad está en parada programada).
4. **Gemelo digital como modelo de datos + lógica**, no como "un 3D lindo". El 3D es una vista más; el valor está en el modelo del estado del activo.
5. **Separación estricta de las redes OT e IT** (DMZ), según IEC 62443. El sistema **solo lee** de la red de control; nunca escribe en el PLC durante el piloto.

---

## 4. El gemelo digital: qué es concretamente en este proyecto

Se construye en **tres niveles de madurez**; el MVP llega al nivel 2.

| Nivel | Nombre | Qué hace | Cuándo |
|---|---|---|---|
| 1 | **Gemelo descriptivo** | Refleja el estado actual del activo: jerarquía, sensores, últimas lecturas, imágenes, alarmas activas | Mes 2–3 |
| 2 | **Gemelo de diagnóstico / predictivo** | Calcula el índice de salud por componente, detecta anomalías y estima la vida útil remanente (RUL) | Mes 4–6 |
| 3 | **Gemelo prescriptivo / simulación** | Simula escenarios ("si postergo el cambio de polines 2 semanas, ¿cuál es el riesgo?") y optimiza el plan de mantenimiento | Fase 2 |

### Modelo de datos del gemelo (jerarquía de activos, estilo ISO 14224)

```
Planta
 └── Área (Chancado secundario)
      └── Sistema (Correa CV-201)
           ├── Componente: Banda            → atributos: largo, tipo, fecha de instalación
           │     ├── Sensor: Cámara RGB C-01 (daño, desalineamiento)
           │     └── Modo de falla: rotura longitudinal, empalme abierto, desgaste de cubierta
           ├── Componente: Estación de polines #1..#N
           │     ├── Sensor: Cámara térmica T-01 (cubre polines 1–40)
           │     └── Modo de falla: rodamiento trabado, sobretemperatura
           ├── Componente: Polea motriz
           ├── Componente: Motor M-201
           │     ├── Sensor: vibración (acelerómetro), corriente (desde PLC)
           │     └── Modo de falla: desbalance, desalineación, falla de rodamiento
           └── Componente: Reductor
```

Cada **componente** tiene:
- `health_index` (0–100), calculado a partir de sus indicadores.
- `failure_modes` (vinculados a un **FMEA**/AMFE hecho con mantenimiento).
- `rul_days` estimado (cuando hay suficiente historial).
- Historial de eventos, inspecciones y órdenes de trabajo.

### Cómo se calcula el índice de salud (versión inicial, simple y explicable)

```
HI_componente = 100 − Σ (peso_i × severidad_i)

severidad_i ∈ [0, 100], por indicador:
  - Temperatura de polín: 0 si ΔT < 10 °C sobre el promedio de sus vecinos;
    escala lineal hasta 100 si ΔT ≥ 40 °C
  - Daño en banda: por clase y tamaño del defecto detectado
  - Vibración: según zonas de ISO 10816 / 20816 (A/B/C/D)
  - Anomalía de serie de tiempo: puntaje del modelo (Isolation Forest / autoencoder)
```

Arrancar con **reglas basadas en normas y en el conocimiento de los mantenedores**, y pasar a modelos de ML **cuando haya historial etiquetado de fallas**. Esto es lo que hace viable el proyecto: da valor desde el día 1 sin esperar años de datos de fallas.

---

## 5. Visión artificial: detalle técnico

### Hardware de captura recomendado (por punto de inspección)

| Elemento | Recomendación | Costo aprox. (USD) |
|---|---|---|
| Cámara térmica fija | FLIR A50/A70, Hikvision serie térmica o equivalente radiométrica (debe entregar temperatura por píxel) | 2.500 – 8.000 |
| Cámara RGB industrial | Basler / Hikrobot GigE, o cámara IP 4K IP67 para casos lentos | 500 – 2.500 |
| Iluminación | Foco LED industrial (la banda en túnel o de noche lo exige) | 200 – 600 |
| Carcasa | IP67 con limpieza por aire (polvo), protección antivibración | 300 – 1.000 |
| Cómputo edge | NVIDIA Jetson Orin NX/AGX en caja industrial sin ventilador | 1.000 – 3.000 |
| Red | Switch industrial PoE, fibra si hay distancias largas | 500 – 2.000 |

> El polvo, la vibración y la variación de luz son **el** problema real en minería. Presupuestar limpieza por aire y soportes antivibración desde el inicio.

### Modelos de IA

| Tarea | Modelo base | Notas |
|---|---|---|
| Detección de defectos en banda y objetos extraños | **YOLO (Ultralytics v8/v11)** detección + segmentación | Transfer learning; exportar a TensorRT para el Jetson |
| Puntos calientes en polines | Procesamiento radiométrico (OpenCV/NumPy) + ROI por polín | No requiere deep learning al principio: umbral relativo entre vecinos |
| Desalineamiento | Detección de bordes + regresión de la posición del borde | Clásico + suavizado temporal |
| Anomalías sin etiquetas (defectos raros) | **Anomalib** (PatchCore / PaDiM) | Se entrena solo con imágenes "normales"; ideal porque hay pocas fallas etiquetadas |
| Series de tiempo (vibración, temperatura, corriente) | Isolation Forest → luego LSTM-autoencoder | Con el contexto de operación del PLC |
| Vida útil remanente (RUL) | Modelo de degradación (regresión / Weibull) | Solo cuando haya historial de fallas |

### Estrategia de datos (lo más crítico del proyecto)

1. **Semanas 1–4:** capturar video/imágenes en operación normal (sin etiquetar todavía).
2. **Dataset inicial:** combinar imágenes propias + datasets públicos de daño en bandas + imágenes históricas de inspecciones de mantenimiento.
3. **Etiquetado con CVAT** (open source, self-hosted), con criterio definido **junto con los mantenedores** (qué es un "desgarro" vs. un "desgaste normal").
4. **Aprendizaje activo:** el sistema en producción guarda los casos dudosos (confianza entre 0,3 y 0,6) y los validados/rechazados por el operador para reentrenar.
5. **Feedback en el dashboard:** botones "alerta correcta / falsa alarma" en cada evento → esa es la fuente principal de mejora continua.

---

## 6. Stack tecnológico propuesto

Elegido por ser **open source, conocido y fácil de contratar en LATAM**, evitando el lock-in con un proveedor de nube.

| Capa | Tecnología |
|---|---|
| Edge: captura e inferencia | Python 3.11, OpenCV, GStreamer, Ultralytics YOLO, TensorRT, DeepStream (opcional) |
| Edge: comunicación | paho-mqtt, asyncua (OPC UA), contenedores Docker |
| Mensajería | EMQX o Mosquitto (MQTT), Sparkplug B |
| Backend / API | **FastAPI** (Python), Pydantic, SQLAlchemy, Celery/Redis para tareas |
| Datos | **PostgreSQL + TimescaleDB** (series de tiempo), almacenamiento **compatible con S3** para imágenes y modelos (SeaweedFS en desarrollo, porque MinIO dejó de publicar imágenes comunitarias; en producción, cualquier S3) |
| ML / MLOps | PyTorch, Anomalib, scikit-learn, **MLflow**, CVAT, DVC para datasets |
| Frontend | **React + TypeScript**, Vite, Tailwind, ECharts/Recharts; **Three.js / React Three Fiber** para el visor 3D del gemelo |
| Tiempo real | WebSockets |
| Integración CMMS | API REST de SAP PM / IBM Maximo / GMAO local (adaptador intercambiable) |
| Infraestructura | Docker Compose (piloto) → Kubernetes / K3s (escala); Grafana + Prometheus para monitoreo |
| Seguridad | Keycloak (SSO/roles), TLS en todo, VPN / DMZ OT-IT, IEC 62443 |

> Alternativa gestionada si el cliente ya está en una nube: Azure IoT Hub + Azure Digital Twins, o AWS IoT Greengrass + IoT TwinMaker. El diseño en capas permite cambiar la plataforma sin reescribir el edge ni los modelos.

---

## 7. Funcionalidades de la aplicación (MVP)

### Módulos

1. **Mapa de planta / árbol de activos:** jerarquía navegable con el color del índice de salud (verde / amarillo / rojo).
2. **Ficha del gemelo de cada activo:** vista 3D o esquema 2D del equipo, componentes con su HI, últimas imágenes y termografías, tendencias, alarmas activas e historial de OT.
3. **Centro de alarmas:** lista priorizada por criticidad × severidad, con la imagen/clip de evidencia, confirmación ("correcta / falsa alarma") y creación de la OT en 1 clic.
4. **Visor de cámaras:** video en vivo con las detecciones superpuestas y la termografía con escala de temperatura.
5. **Tendencias y analítica:** series de tiempo, comparación entre activos, KPIs (MTBF, MTTR, disponibilidad).
6. **Plan de mantenimiento:** recomendaciones del gemelo ("cambiar polines 12, 13 y 27 en la próxima parada programada") y exportación al CMMS.
7. **Administración:** activos, cámaras, zonas de interés (ROI), umbrales, usuarios y roles.

### Roles de usuario

- **Operador de sala de control:** ve alarmas y confirma.
- **Supervisor / planificador de mantenimiento:** prioriza y genera OT.
- **Ingeniero de confiabilidad:** ajusta umbrales, FMEA y analiza tendencias.
- **Administrador:** gestiona la configuración y los usuarios.

---

## 8. Estructura del repositorio (para cuando empecemos a desarrollar)

```
minevision-twin/
├── edge/                      # Corre en el Jetson / PC industrial
│   ├── capture/               # Lectura RTSP / GigE / térmica radiométrica
│   ├── inference/             # Pipelines YOLO, térmico, anomalías
│   ├── opcua_client/          # Contexto del PLC (velocidad, marcha/parada)
│   ├── publisher/             # MQTT + buffer store-and-forward
│   └── config/                # Cámaras, ROIs, umbrales (YAML)
├── platform/
│   ├── api/                   # FastAPI: activos, eventos, gemelo, OT
│   ├── ingestion/             # Consumidor MQTT → TimescaleDB / MinIO
│   ├── twin_engine/           # Health Index, reglas, anomalías, RUL
│   ├── integrations/          # Adaptadores CMMS, notificaciones
│   └── migrations/            # Alembic
├── web/                       # React + TS: dashboard, visor 3D, alarmas
├── ml/
│   ├── datasets/              # Versionado con DVC (no en git)
│   ├── training/              # Scripts de entrenamiento y evaluación
│   ├── notebooks/
│   └── export/                # ONNX → TensorRT
├── simulator/                 # Generador de datos y video sintético para desarrollo
├── infra/                     # docker-compose, k8s, Grafana
└── docs/                      # Arquitectura, FMEA, manuales
```

> **Clave para poder desarrollar sin estar en la mina:** el módulo `simulator/` genera telemetría sintética (temperaturas, vibración con fallas inyectadas) y reproduce videos grabados como si fueran cámaras RTSP. Así todo el equipo desarrolla y prueba sin depender de la planta.

---

## 9. Plan de trabajo por fases (≈ 9 meses hasta el piloto validado)

### Fase 0 — Descubrimiento (semanas 1–4)
- Visita a la planta, entrevistas con mantenimiento, operaciones y TI/OT.
- Elegir el **activo piloto** con los datos de paradas de los últimos 2 años (análisis de Pareto).
- Hacer el **FMEA** del activo piloto con los mantenedores: modos de falla, síntomas visibles/térmicos y criticidad.
- Relevamiento de la red, los PLC, el SCADA, el CMMS y la ubicación física de las cámaras (polvo, vibración, energía, conectividad).
- Medir la línea base de los KPIs.
- **Entregable:** documento de requerimientos, FMEA, diseño de la instalación y caso de negocio con ROI.

### Fase 1 — Prueba de concepto técnica (semanas 5–10)
- Instalar **1 cámara térmica + 1 RGB + 1 edge** en un tramo de la correa.
- Grabar datos en operación real; validar la calidad de imagen con polvo, de noche y con vibración.
- Primeros modelos: puntos calientes (reglas) + detector YOLO inicial.
- Backend mínimo + dashboard básico con alarmas.
- **Criterio de éxito (go/no-go):** el sistema detecta polines calientes reales confirmados por inspección manual con cámara termográfica portátil.

### Fase 2 — MVP del gemelo digital (semanas 11–22)
- Modelo de datos de activos + motor del gemelo (Health Index basado en reglas).
- Integración OPC UA con el PLC (contexto de operación).
- Detección de daño en la banda y desalineamiento; Anomalib para defectos raros.
- Dashboard completo: árbol de activos, ficha del gemelo, centro de alarmas y tendencias.
- Integración con el CMMS (creación de avisos/OT) y notificaciones.
- Ciclo de feedback del operador → reentrenamiento.
- **Entregable:** MVP funcionando en el activo piloto 24/7.

### Fase 3 — Piloto operativo y validación (semanas 23–36)
- Operación asistida con el equipo de mantenimiento.
- Ajuste de umbrales para bajar los falsos positivos.
- Primeros modelos de anomalías en series de tiempo; RUL experimental.
- Medir los KPIs contra la línea base → **informe de ROI**.
- Capacitación de usuarios y manuales.
- **Decisión:** escalar a más líneas y equipos (chancadores, molinos, bombas).

### Fase 4 — Escalamiento (a partir del mes 10)
- Replicar en otras correas (ya con el modelo y la configuración reutilizable).
- Nuevos tipos de activo: chancadores, molinos, bombas, camiones y palas.
- Gemelo prescriptivo: simulación y optimización del plan de mantenimiento.
- Pasar a Kubernetes/K3s, multi-sitio y MLOps automatizado.

### Cronograma resumido

```
Mes:           1    2    3    4    5    6    7    8    9
Fase 0       ████
Fase 1          ██████
Fase 2                ████████████
Fase 3                            ████████████████
```

---

## 10. Equipo necesario

| Rol | Dedicación | Responsabilidades |
|---|---|---|
| Líder de proyecto / arquitecto | 100 % | Arquitectura, relación con el cliente, prioridades |
| Ingeniero/a de visión artificial / ML | 100 % | Modelos, datasets, pipeline edge |
| Desarrollador/a backend (Python) | 100 % | API, ingesta, motor del gemelo |
| Desarrollador/a frontend (React) | 50–100 % | Dashboard, visor 3D |
| Ingeniero/a de automatización / OT | 30–50 % | OPC UA, PLC, redes industriales, instalación |
| Ingeniero/a de confiabilidad (del cliente) | 20 % | FMEA, validación de alarmas, criterios de falla |
| Etiquetadores/as | Por demanda | Etiquetado de imágenes (puede ser personal del cliente capacitado) |

---

## 11. Presupuesto estimado del piloto (orden de magnitud, USD)

| Rubro | Rango |
|---|---|
| Hardware (2–4 puntos de inspección: cámaras, edge, carcasas, red) | 15.000 – 40.000 |
| Instalación en planta (montaje, cableado, energía) | 5.000 – 15.000 |
| Desarrollo de software e IA (9 meses, equipo arriba) | 120.000 – 220.000 |
| Infraestructura (servidor on-prem con GPU o nube) | 5.000 – 15.000 |
| Contingencia (15 %) | 20.000 – 40.000 |
| **Total del piloto** | **≈ 165.000 – 330.000** |

**Justificación del ROI:** evitar **una sola** rotura mayor de banda (típicamente 24–72 h de parada más USD 50–200 mil de banda nueva) suele pagar el piloto completo.

---

## 12. Riesgos y mitigaciones

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Polvo, vibración y luz variable degradan las imágenes | Alto | Carcasas con limpieza por aire, iluminación propia, validación en la fase 1 antes de invertir más |
| Pocos ejemplos de fallas reales para entrenar | Alto | Reglas + detección de anomalías sin supervisión (Anomalib) + aprendizaje activo + datos sintéticos |
| Demasiados falsos positivos → los operadores ignoran las alarmas | Alto | Contexto del PLC, umbrales relativos, confirmación en N cuadros, métrica de falsos positivos como KPI |
| Conectividad limitada en la mina | Medio | Inferencia en el edge + store-and-forward |
| Ciberseguridad OT | Alto | DMZ, solo lectura del PLC, IEC 62443, revisión con el equipo de TI/OT del cliente |
| Resistencia al cambio del equipo de mantenimiento | Medio | Involucrarlos desde el FMEA, la IA como apoyo (no reemplazo), mostrar victorias tempranas |
| Integración con el CMMS lenta (burocracia de SAP) | Medio | Adaptador desacoplado; en el piloto, exportación manual/CSV o correo si la API se demora |

---

## 13. Backlog inicial para arrancar el desarrollo (sprints de 2 semanas)

**Sprint 1 — Esqueleto y simulador** ✅ completado
- [x] Monorepo con la estructura de la sección 8, Docker Compose (Postgres + Timescale, almacenamiento S3, EMQX, API). La web se suma en el Sprint 5.
- [x] `simulator/`: publicador MQTT de telemetría sintética de una correa (temperatura de polines, vibración del motor y velocidad) con fallas inyectables.
- [x] `simulator/`: servidor RTSP que reproduce videos de ejemplo en loop.
- [x] Modelo de datos de activos (planta → área → sistema → componente → sensor) y migraciones.

**Sprint 2 — Ingesta y API** ✅ completado
- [x] Servicio de ingesta MQTT → TimescaleDB.
- [x] API REST: CRUD de activos, consulta de series de tiempo y eventos.
- [x] WebSocket de alarmas en tiempo real.
- [x] Adelantado del Sprint 4: alarmas por umbral con confirmación, histéresis y escalamiento (reglas en `config/plant.yaml`).

**Sprint 3 — Pipeline de visión en el edge**
- [ ] Captura RTSP + inferencia YOLO preentrenada (prueba con las clases base).
- [ ] Pipeline térmico: ROI por polín, ΔT contra vecinos y generación de evento.
- [ ] Publicación de eventos con snapshot a MinIO.

**Sprint 4 — Motor del gemelo v1**
- [ ] Health Index por componente con reglas configurables (YAML).
- [ ] Generación de alarmas con severidad y deduplicación.
- [ ] Recomendaciones de mantenimiento básicas.

**Sprint 5 — Dashboard**
- [ ] Árbol de activos con el color de salud.
- [ ] Ficha del gemelo (esquema 2D de la correa con los polines coloreados; 3D en un sprint posterior).
- [ ] Centro de alarmas con evidencia y botones de feedback.

**Sprint 6 — Integración y endurecimiento**
- [ ] Cliente OPC UA (contra un simulador de PLC, p. ej., el servidor de ejemplo de asyncua).
- [ ] Exportación de OT (CSV/API genérica) y notificaciones por email/Teams.
- [ ] Autenticación y roles (Keycloak), pruebas y CI.

---

## 14. Próximos pasos inmediatos

1. **Validar este plan** con un cliente o una planta candidata y elegir el activo piloto con datos reales de paradas.
2. **Conseguir videos reales** (aunque sean de celular o de inspecciones anteriores) de correas y termografías para empezar a prototipar los modelos.
3. **Arrancar el Sprint 1** en este repositorio: esqueleto + simulador, que permiten desarrollar todo el sistema sin depender del acceso a la mina.
