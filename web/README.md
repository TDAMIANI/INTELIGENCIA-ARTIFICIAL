# web

Tablero de MineVision Twin: React + TypeScript + Vite, sin librerías de UI ni de gráficos.
Los gráficos son SVG propios, así la app pesa poco y funciona sin internet en la planta.
Las fuentes se empaquetan con la app.

```bash
npm install
npm run dev        # http://localhost:5173 (usa la API de http://localhost:8000)
npm test           # tests de la lógica de presentación
npm run build      # genera dist/
```

En docker compose, el servicio `web` sirve la app en http://localhost:8080 con nginx,
que deriva `/api` y `/ws` a la API.

## Pantallas

| Ruta | Qué muestra |
|---|---|
| `#/` | Salud de la planta, alarmas activas, mantenimiento pendiente, detecciones, componentes más comprometidos y árbol de activos |
| `#/activo/CV-201` | Gemelo de la correa: esquema con los 40 polines coloreados por salud, explicación del índice, tendencias con umbrales, historial de salud, recomendaciones, alarmas, evidencias y ficha técnica (FMEA) |
| `#/alarmas` | Centro de alarmas con la foto de evidencia más cercana y confirmación del operador |
| `#/recomendaciones` | Plan sugerido: planificar (OT), marcar realizada, descartar con motivo; exportar órdenes a CSV |
| `#/eventos` | Galería de detecciones de visión |

## Diseño

- **Estética de sala de control:** grafito, franja de seguridad y amarillo de señalización. Es oscuro por defecto y tiene tema claro.
- **Estados:** bueno, atención, degradado y crítico usan una paleta fija y siempre llevan **ícono y texto**, así se entienden también con daltonismo o en una impresión en blanco y negro.
- **En vivo:** WebSocket para alarmas, eventos y salud, con reconexión automática. Además, recarga periódica como respaldo si se corta la conexión.
