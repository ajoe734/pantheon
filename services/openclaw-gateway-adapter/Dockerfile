FROM python:3.11-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY services/openclaw-gateway-adapter/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /workspace

ENV PORT=8104
EXPOSE 8104

CMD ["sh", "-c", "uvicorn main:app --app-dir services/openclaw-gateway-adapter --host 0.0.0.0 --port ${PORT:-8104}"]
