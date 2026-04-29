FROM python:3.11-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace:/workspace/services/research-worker-gateway

RUN pip install --no-cache-dir fastapi uvicorn pydantic

COPY . .

ENV PORT=8103
EXPOSE 8103

CMD ["sh", "-c", "uvicorn main:app --app-dir services/research-worker-gateway --host 0.0.0.0 --port ${PORT:-8103}"]
