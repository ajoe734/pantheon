# RLlib + Ray Tune research container
# Status: package stub only — not yet a governed adapter path
# Activation gate: RL path approval required before any training/eval work begins
# See: services/learning/rl/PATH_DEFINITION.md
# See: services/learning/DEFERRED_OSS_ACTIVATION_MAP.md §4
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# No adapter/ directory yet — governed adapter task is deferred until RL path is approved
CMD ["echo", "RLlib container stub: RL path approval required before activation"]
