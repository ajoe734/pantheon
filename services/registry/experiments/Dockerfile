# Registry experiments container
# Status: deferred-prep scaffold — adapter, offline W&B prep backend, and smoke path are present
# Activation gate: W&B backend remains deferred; see WANDB_ACTIVATION.md
# All backends (memory, offline W&B) are stdlib-only; no mlflow/wandb install required
# for deferred-prep smoke.
FROM python:3.11-slim
WORKDIR /app
COPY services/registry/experiments/adapter.py ./adapter.py
COPY services/registry/experiments/config.py ./config.py
COPY services/registry/experiments/smoke_test.py ./smoke_test.py
# Default command is inert. Run smoke_test.py with --backend memory when
# exercising the offline scaffold.
CMD ["python", "-c", "print('Registry experiments deferred-prep image: explicit prep gate required; activation remains closed')"]
