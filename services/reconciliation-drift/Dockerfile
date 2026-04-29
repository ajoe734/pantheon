FROM python:3.11-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace:/workspace/services/reconciliation-drift

COPY services/reconciliation-drift/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /workspace

ENV PORT=8102
EXPOSE 8102

CMD ["sh", "-c", "uvicorn main:app --app-dir services/reconciliation-drift --host 0.0.0.0 --port ${PORT:-8102}"]
