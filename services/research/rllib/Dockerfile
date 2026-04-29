# RLlib + Ray Tune research container
# Status: deferred-prep scaffold — adapters and explicit-gate smoke paths are present
# Activation gate: workers require prep env vars; RL path approval is still required
# before production train/eval, registry writes, or paper/live activation.
# See: services/learning/rl/PATH_DEFINITION.md
# See: services/learning/DEFERRED_OSS_ACTIVATION_MAP.md §4
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY adapter ./adapter
COPY examples ./examples
COPY config.py worker.py ray_tune_worker.py smoke_test.py ray_tune_smoke_test.py ./
# Default command is inert. Run worker.py or ray_tune_worker.py with the explicit
# prep env gate when exercising the offline scaffold.
CMD ["python", "-c", "print('RLlib/Ray Tune deferred-prep image: explicit prep gate required; activation remains closed')"]
