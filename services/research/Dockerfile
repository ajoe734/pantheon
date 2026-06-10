FROM python:3.11-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace:/workspace/services/research

RUN pip install --no-cache-dir fastapi uvicorn pydantic "psycopg[binary]" "jsonschema>=4.0,<5.0"

COPY . .

ENV PORT=8101
EXPOSE 8101

CMD ["sh", "-c", "uvicorn main:app --app-dir services/research --host 0.0.0.0 --port ${PORT:-8101}"]
