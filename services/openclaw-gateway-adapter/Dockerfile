FROM python:3.11-slim

WORKDIR /workspace

ARG OPENAI_CODEX_NPM_VERSION=0.136.0
ARG ANTHROPIC_CLAUDE_CODE_NPM_VERSION=2.1.161

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/workspace:/workspace/services/openclaw-gateway-adapter

# Install necessary system dependencies for CLI tools
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git nodejs npm \
    && rm -rf /var/lib/apt/lists/*

COPY services/openclaw-gateway-adapter/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
RUN npm install -g \
    "@openai/codex@${OPENAI_CODEX_NPM_VERSION}" \
    "@anthropic-ai/claude-code@${ANTHROPIC_CLAUDE_CODE_NPM_VERSION}" \
    && npm cache clean --force

COPY . /workspace

ENV PORT=8104
EXPOSE 8104

CMD ["sh", "-c", "uvicorn main:app --app-dir services/openclaw-gateway-adapter --host 0.0.0.0 --port ${PORT:-8104}"]
