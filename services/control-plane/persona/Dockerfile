FROM python:3.12-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace

COPY dependencies/locks/services-control-plane-persona.txt /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock

COPY . /workspace

EXPOSE 8002

CMD ["uvicorn", "main:app", "--app-dir", "/workspace/services/control-plane/persona", "--host", "0.0.0.0", "--port", "8002"]
