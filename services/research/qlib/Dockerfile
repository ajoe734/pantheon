# Qlib supervised-alpha research container
# Status: deferred-prep scaffold — adapter, preflight, and explicit-gate smoke paths are present
# Activation gate: production Qlib activation requires gated pre-activation preflight
# See: services/learning/DEFERRED_OSS_ACTIVATION_MAP.md §2
FROM python:3.11-slim
WORKDIR /app
COPY services/research/qlib/requirements.txt .
ARG PANTHEON_INSTALL_UPSTREAM_DEPS=true
RUN if [ "$PANTHEON_INSTALL_UPSTREAM_DEPS" = "true" ]; then pip install --no-cache-dir -r requirements.txt; fi
COPY services/research/qlib/adapter/ ./adapter/
COPY services/research/qlib/preflight.py services/research/qlib/worker.py services/research/qlib/smoke_test.py ./
COPY services/research/qlib/examples/ ./examples/
# Default command is inert. Run smoke_test.py with --backend stub when
# exercising the offline scaffold.
CMD ["python", "-c", "print('Qlib deferred-prep image: explicit prep gate required; activation remains closed')"]
