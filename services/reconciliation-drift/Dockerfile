FROM python:3.12-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace:/workspace/services/reconciliation-drift

COPY dependencies/locks/services-reconciliation-drift.txt /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock

COPY . /workspace

ENV PORT=8102
EXPOSE 8102

CMD ["sh", "-c", "uvicorn main:app --app-dir services/reconciliation-drift --host 0.0.0.0 --port ${PORT:-8102}"]
