# FinRL research container
# Status: deferred-prep scaffold — adapter and explicit-gate smoke paths are present
# Activation gate: workers require prep env vars; RL path approval is still required
# before production train/eval, registry writes, or paper/live activation.
# See: services/learning/DEFERRED_OSS_ACTIVATION_MAP.md §3
FROM python:3.11-slim
WORKDIR /app
COPY services/research/finrl/requirements.txt .
ARG PANTHEON_INSTALL_UPSTREAM_DEPS=true
RUN if [ "$PANTHEON_INSTALL_UPSTREAM_DEPS" = "true" ]; then pip install --no-cache-dir -r requirements.txt; fi
COPY services/research/finrl/adapter/ ./adapter/
COPY services/research/finrl/config.py services/research/finrl/worker.py services/research/finrl/smoke_test.py ./
COPY services/research/finrl/examples/ ./examples/
# Default command is inert. Run smoke_test.py with --enable-deferred-prep when
# exercising the offline scaffold.
CMD ["python", "-c", "print('FinRL deferred-prep image: explicit prep gate required; activation remains closed')"]
