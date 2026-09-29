# Imagen del gateway edge para desarrollo (x86). En un Jetson se usa una base
# NVIDIA L4T con TensorRT; el código es el mismo.
FROM python:3.11-slim
# WITH_YOLO=1 instala Ultralytics/PyTorch (imagen mucho más pesada) para detección de objetos.
ARG WITH_YOLO=0
WORKDIR /app
COPY requirements.txt requirements-yolo.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$WITH_YOLO" = "1" ]; then pip install --no-cache-dir -r requirements-yolo.txt; fi
COPY mvedge/ mvedge/
COPY config/ config/
ENTRYPOINT ["python", "-m", "mvedge"]
CMD ["--config", "config/edge.yaml"]
