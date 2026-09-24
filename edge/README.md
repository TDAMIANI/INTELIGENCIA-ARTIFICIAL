# edge (paquete `mvedge`)

Software del gateway que corre en planta, junto a las cámaras (NVIDIA Jetson o PC
industrial). Lee las cámaras, analiza las imágenes ahí mismo y publica por MQTT solo
resultados: telemetría y eventos. Las imágenes de evidencia se suben a S3. No viaja
video continuo por la red de la mina.

```bash
pip install -r requirements.txt           # visión clásica y termografía
pip install -r requirements-yolo.txt      # + detección de objetos con YOLO (instala PyTorch)
python -m mvedge --config config/edge.yaml
```

## Módulos

| Módulo | Qué hace |
|---|---|
| `sources.py` | Fuentes de imagen: RTSP (cámaras reales, con reconexión) y MQTT (cámaras virtuales del simulador) |
| `thermal.py` | Imagen radiométrica a °C, temperatura máxima por ROI (un ROI por polín), ΔT contra vecinos, confirmación en N imágenes, imagen de evidencia |
| `belt.py` | Bordes de la banda y desalineamiento en mm (visión clásica, sin deep learning) |
| `detection.py` | YOLO (Ultralytics) y reglas "objeto de la clase X dentro de la zona Y" |
| `events.py` | Eventos con anti-rebote: sube la foto a S3 y publica el evento. Si S3 falla, el evento se publica igual |
| `pipelines.py` | Une todo por tipo de cámara y publica en el mismo formato que el simulador |
| `app.py` | Un hilo por cámara, configurado desde `config/edge.yaml` |

## Llevarlo a una planta real

1. **Cámara térmica:** debe ser **radiométrica**, es decir, entregar la temperatura de cada píxel (FLIR A50/A70 o equivalente). Hay que ajustar `radiometric.scale/offset_c` al formato de la cámara y dibujar los ROIs sobre una imagen real (`rois.items` con x, y, w, h por polín).
2. **Cámara RGB:** `source: {type: rtsp, url: ...}`. Para calibrar la banda se usa su ancho conocido: `mm_per_px` = ancho real en mm dividido por el ancho en píxeles. `ref_center_px` es el centro con la banda bien alineada.
3. **YOLO:** el modelo preentrenado solo sirve para personas y vehículos. Para daños en la banda hay que entrenar un modelo propio con imágenes etiquetadas (`ml/`) y agregar reglas para sus clases. En un Jetson se exporta a TensorRT (`yolo export model=best.pt format=engine`) y se usa el `.engine`.
4. **Red:** si se corta la conexión, los mensajes quedan en memoria (hasta `mqtt.max_queued`) y se reenvían al reconectar.
