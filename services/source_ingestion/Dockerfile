FROM python:3.11-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY services/source_ingestion/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /workspace

ENV PORT=8097
EXPOSE 8097

CMD ["sh", "-c", "uvicorn services.source_ingestion.main:app --host 0.0.0.0 --port ${PORT:-8097}"]
