FROM python:3.11-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/workspace:/workspace/services/openclaw-gateway-adapter

# Install necessary system dependencies for CLI tools
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

COPY services/openclaw-gateway-adapter/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
RUN pip install --no-cache-dir codex-cli claude-cli

COPY . /workspace

ENV PORT=8104
EXPOSE 8104

CMD ["sh", "-c", "uvicorn main:app --app-dir services/openclaw-gateway-adapter --host 0.0.0.0 --port ${PORT:-8104}"]
