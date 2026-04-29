# TRL preference-learning container
# Status: deferred-prep scaffold — adapter and explicit-gate smoke paths are present
# Activation gate: production TRL activation requires preference-learning path approval
# See: services/learning/DEFERRED_OSS_ACTIVATION_MAP.md §5
# Stub backend uses stdlib only; TRL/torch packages not required for offline smoke.
FROM python:3.11-slim
WORKDIR /app
COPY services/learning/trl/adapter/ ./services/learning/trl/adapter/
COPY services/learning/trl/smoke_test.py ./services/learning/trl/smoke_test.py
COPY services/learning/trl/examples/ ./services/learning/trl/examples/
# Default command is inert. Run smoke_test.py with --backend stub when
# exercising the offline scaffold.
CMD ["python", "-c", "print('TRL deferred-prep image: explicit prep gate required; activation remains closed')"]
