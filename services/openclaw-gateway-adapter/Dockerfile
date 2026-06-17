# --- Stage: source of the governed OpenClaw CLI runtime ---
# OpenClaw is NOT published on npm; the CLI is a bundled build at /app/openclaw.mjs
# that runs on the Node runtime shipped in the upstream image. We copy that build
# (and its Node) into the adapter so assistant_openclaw_provider can shell out to
# `openclaw agent --url ws://openclaw-gateway:18789 ...` as a remote WS-RPC client.
#
# The upstream runtime pin is GOVERNED: this tag MUST stay in lockstep with
# integrations/openclaw/gateway/Dockerfile (FROM) and the pin tracked in
# OSS_INTEGRATION_CHECKLIST.md / integrations/openclaw/governance.md. Bump all together.
FROM ghcr.io/openclaw/openclaw:2026.6.6 AS openclaw_cli

FROM python:3.11-slim

WORKDIR /workspace

ARG OPENAI_CODEX_NPM_VERSION=0.136.0
ARG ANTHROPIC_CLAUDE_CODE_NPM_VERSION=2.1.161
ARG PANTHEON_ASSISTANT_UID=10001

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/workspace:/workspace/services/openclaw-gateway-adapter

# Install necessary system dependencies for CLI tools
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git nodejs npm \
    && rm -rf /var/lib/apt/lists/*

RUN useradd \
    --uid "${PANTHEON_ASSISTANT_UID}" \
    --home-dir /home/pantheon-assistant \
    --create-home \
    --shell /usr/sbin/nologin \
    pantheon-assistant \
    && install -d -m 0700 -o pantheon-assistant -g pantheon-assistant /home/pantheon-assistant/.codex \
    && install -d -m 0700 -o pantheon-assistant -g pantheon-assistant /home/pantheon-assistant/.claude

COPY services/openclaw-gateway-adapter/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
RUN npm install -g \
    "@openai/codex@${OPENAI_CODEX_NPM_VERSION}" \
    "@anthropic-ai/claude-code@${ANTHROPIC_CLAUDE_CODE_NPM_VERSION}" \
    && npm cache clean --force

# Bake the governed OpenClaw CLI runtime (see the openclaw_cli stage above).
# Node 24 is kept isolated under /opt/openclaw so it does NOT shadow the apt
# nodejs (Node 20) that the codex/claude global CLIs run on. The `openclaw`
# wrapper on PATH is what assistant_openclaw_provider resolves via shutil.which.
# Both the upstream image and python:3.11-slim are Debian bookworm/glibc x64, so
# the copied Node binary is ABI-compatible (libstdc++6 is present via the apt
# nodejs install above). `openclaw --version` at build time fails the build if
# the runtime is broken — this is the gate that the prior fake-green lacked.
COPY --from=openclaw_cli /usr/local/bin/node /opt/openclaw/bin/node
COPY --from=openclaw_cli /app /opt/openclaw/app
RUN printf '#!/bin/sh\nexec /opt/openclaw/bin/node /opt/openclaw/app/openclaw.mjs "$@"\n' > /usr/local/bin/openclaw \
    && chmod +x /usr/local/bin/openclaw \
    && openclaw --version

COPY . /workspace

ENV PORT=8104
EXPOSE 8104

CMD ["sh", "-c", "uvicorn main:app --app-dir services/openclaw-gateway-adapter --host 0.0.0.0 --port ${PORT:-8104}"]
