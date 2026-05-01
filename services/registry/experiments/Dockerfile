# Registry experiments container
# Status: offline-local W&B store plus explicit-gated SDK-backed W&B online sync
# Activation gate: online sync requires PANTHEON_WANDB_ONLINE_SYNC_ENABLED=1,
# a test W&B project, and WANDB_API_KEY; see WANDB_ACTIVATION.md.
FROM python:3.11-slim
WORKDIR /app
COPY services/registry/experiments/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY services/registry/experiments/adapter.py ./adapter.py
COPY services/registry/experiments/config.py ./config.py
COPY services/registry/experiments/smoke_test.py ./smoke_test.py
# Default command is inert. Run smoke_test.py with --backend wandb-online only
# when the explicit online env gate and test credentials are present.
CMD ["python", "-c", "print('Registry experiments image: offline default preserved; W&B online sync requires explicit env gate and test credentials')"]
