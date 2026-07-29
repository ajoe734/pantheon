# RLlib + Ray Tune research container
# Status: deferred-prep scaffold — adapters and explicit-gate smoke paths are present
# Activation gate: workers require prep env vars; RL path approval is still required
# before production train/eval, registry writes, or paper/live activation.
# See: services/learning/rl/PATH_DEFINITION.md
# See: services/learning/DEFERRED_OSS_ACTIVATION_MAP.md §4
FROM python:3.11-slim
WORKDIR /app
COPY services/research/rllib/requirements.txt .
ARG PANTHEON_INSTALL_UPSTREAM_DEPS=true
RUN if [ "$PANTHEON_INSTALL_UPSTREAM_DEPS" = "true" ]; then pip install --no-cache-dir -r requirements.txt; fi
COPY services/research/rllib/adapter/ ./adapter/
COPY services/research/rllib/examples/ ./examples/
COPY services/research/rllib/__init__.py services/research/rllib/adapter.py services/research/rllib/cartpole_ppo.py services/research/rllib/config.py services/research/rllib/contract.md services/research/rllib/security.py services/research/rllib/worker.py services/research/rllib/ray_tune_worker.py services/research/rllib/smoke_test.py services/research/rllib/ray_tune_smoke_test.py ./
RUN addgroup --system pantheon \
    && adduser --system --ingroup pantheon --home /home/pantheon pantheon \
    && chown -R pantheon:pantheon /app /home/pantheon
ENV HOME=/tmp/pantheon \
    RAY_AUTH_MODE=token \
    RAY_AUTH_TOKEN_PATH=/run/secrets/pantheon_ray_auth_token \
    RAY_DASHBOARD_HOST=127.0.0.1
USER pantheon
# Default command is inert. Run worker.py or ray_tune_worker.py with the explicit
# prep env gate and a mounted Ray auth-token secret when exercising the real
# offline backend. The stub path never starts Ray or opens a network listener.
CMD ["python", "-c", "print('RLlib/Ray Tune deferred-prep image: token auth and explicit prep gate required; activation remains closed')"]
