FROM python:3.12-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace:/workspace/services/channels/web

COPY dependencies/locks/services-channels-web.txt /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock

COPY . /workspace

ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn main:app --app-dir services/channels/web --host 0.0.0.0 --port ${PORT:-8000}"]
