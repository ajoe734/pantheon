FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Install from the committed lock only: every package is pinned, so pip does no dependency resolution
# (no backtracking) and torch comes from the CPU wheel index (no CUDA/triton wheels).
COPY deploy/collector/requirements.txt deploy/collector/requirements.lock /app/
RUN pip install --no-cache-dir --no-deps \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    -r /app/requirements.lock

# Scrapling 0.2.99's browser class (PlayWrightFetcher) drives a Playwright-managed Chromium; install it
# (plus its OS libraries) at build time into a path the unprivileged runtime user can read.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN python -m playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright \
    && rm -rf /var/lib/apt/lists/*

# Build context is the repository root (docker-compose.yml: context ../.., dockerfile deploy/collector/Dockerfile)
# so the ONE shared listing parser ships in the image, byte-identical to the repo file at the same commit.
COPY deploy/collector/collector_stack /app/collector_stack
COPY deploy/collector/fetch-policy.json /app/fetch-policy.json
COPY deploy/collector/query_vocabulary.json /app/query_vocabulary.json
COPY services/source_ingestion/security_listing.py /app/services/source_ingestion/security_listing.py

RUN useradd --create-home --uid 10001 collector \
    && mkdir -p /data/bundles /data/state /data/library \
    && chown -R 10001:10001 /data
USER collector

ENTRYPOINT ["python3", "-m", "collector_stack.cli"]
CMD ["validate-config", "--policy-file", "/app/fetch-policy.json"]
