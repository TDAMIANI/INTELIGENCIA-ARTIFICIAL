# Contexto de build: raíz del repositorio (necesita config/plant.yaml).
FROM python:3.11-slim
WORKDIR /app
COPY platform/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY platform/alembic.ini ./
COPY platform/migrations/ migrations/
COPY platform/mvtwin/ mvtwin/
COPY config/ /config/
ENV MVT_PLANT_CONFIG=/config/plant.yaml
EXPOSE 8000
CMD ["uvicorn", "mvtwin.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
