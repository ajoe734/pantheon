# Registry experiments container
# Status: offline-local scaffold - adapter, offline W&B local store, and smoke path are present
# Activation gate: W&B backend remains deferred; see WANDB_ACTIVATION.md
# All backends (memory, offline W&B) are stdlib-only; no mlflow/wandb install required
# for offline local-store smoke.
FROM python:3.11-slim
WORKDIR /app
COPY services/registry/experiments/adapter.py ./adapter.py
COPY services/registry/experiments/config.py ./config.py
COPY services/registry/experiments/smoke_test.py ./smoke_test.py
# Default command is inert. Run smoke_test.py with --backend memory when
# exercising the offline local store.
CMD ["python", "-c", "print('Registry experiments offline-local image: explicit offline-store gate required; online activation remains closed')"]
