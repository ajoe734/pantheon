# Qlib supervised-alpha research container
# Status: activation-ready offline worker — fail-closed unless explicit gates are set
# Activation gate: production Qlib activation still requires gated pre-activation preflight
# See: services/learning/DEFERRED_OSS_ACTIVATION_MAP.md §2
FROM python:3.11-slim
WORKDIR /app
COPY services/research/qlib/requirements.txt .
ARG PANTHEON_INSTALL_UPSTREAM_DEPS=true
RUN if [ "$PANTHEON_INSTALL_UPSTREAM_DEPS" = "true" ]; then pip install --no-cache-dir -r requirements.txt; fi
COPY services/research/qlib/adapter/ ./adapter/
COPY services/research/qlib/preflight.py services/research/qlib/worker.py services/research/qlib/smoke_test.py ./
COPY services/research/qlib/examples/ ./examples/
# Default command is inert. Invoke worker.py only with
# PANTHEON_QLIB_ACTIVATION_READY_ENABLED=1 and explicit QLIB_BACKEND.
CMD ["python", "-c", "print('Qlib activation-ready image: explicit offline gate required; production activation remains closed')"]
