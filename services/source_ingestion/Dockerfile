FROM python:3.12-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
ENV MALLOC_ARENA_MAX=2

COPY dependencies/locks/services-source_ingestion.txt /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock

COPY . /workspace

ENV PORT=8097
EXPOSE 8097

CMD ["sh", "-c", "exec uvicorn services.source_ingestion.main:app --host 0.0.0.0 --port ${PORT:-8097}"]
