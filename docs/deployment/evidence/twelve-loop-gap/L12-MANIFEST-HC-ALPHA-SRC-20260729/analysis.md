# Workstream Analysis & Handoff: L12-MANIFEST-HC-ALPHA-SRC-20260729

## Overview
Task ID: `L12-MANIFEST-HC-ALPHA-SRC-20260729`
Goal: Provide concrete disposition, robust healthcheck implementations, environment-aligned probe bounds, live compose readback commands, and verified test execution evidence for `alpha-replication-worker` and `search-index-scheduler` health/heartbeat liveness surfaces, handed off to `L12-MANIFEST-001` owner (`Claude2`).

---

## 1. Audit Findings & Dispositions

### A. `search-index-scheduler`
- **Current State in `docker-compose.yml`**:
  - `build.dockerfile`: `services/search/Dockerfile`
  - `command`: `["python", "-m", "services.search.scheduler_worker"]`
  - `restart: unless-stopped`
  - `stop_grace_period: 30s`
  - **Healthcheck**: Missing in baseline.
- **Worker Liveness Architecture**:
  - `scheduler_worker.py` runs a loop making POST requests to `SEARCH_API_URL` (`/api/search/index/refresh` and `/api/search/index/materialize`).
  - It does not expose an HTTP server.
- **Explicit Disposition**: **Option 2 (Heartbeat File via Code Touch + Compose Healthcheck)**.
  - `search-index-scheduler` will touch a heartbeat file at path specified by `SEARCH_INDEX_SCHEDULER_ALIVE_PATH` (default `/data/search/search_scheduler_alive`) upon each tick completion.
  - Compose healthcheck will inspect file freshness bounded by `max(2 * interval, interval + 300)` seconds (default 600s for 300s interval).
- **Residual Risk (for `L12-MANIFEST-001` waiver / residual risk manifest if needed)**:
  - *"If `search-svc` stalls silently without raising an exception during HTTP calls, `search-index-scheduler` will delay updating `SEARCH_INDEX_SCHEDULER_ALIVE_PATH`. The container healthcheck will transition to unhealthy after 600s, but because Docker `restart: unless-stopped` acts on process exit only (and no autoheal sidecar is enabled), the container will remain unhealthy without automatic restart unless an external watcher intervenes."*

### B. `alpha-replication-worker`
- **Current State in `docker-compose.yml`**:
  - `build.dockerfile`: `services/research/Dockerfile`
  - `command`: `["python", "-m", "services.research.alpha_replication.replication_controller"]`
  - `restart: unless-stopped`
  - `stop_grace_period: 30s`
  - `environment` exports `ALPHA_REPLICATION_CONTROLLER_STATE_PATH: /data/research-orchestrator/alpha-replication/controller_state.json`.
- **Worker Liveness Architecture**:
  - `replication_controller.py` manages `ControllerState` via `ControllerStateStore` saving JSON + checksum to `ALPHA_REPLICATION_CONTROLLER_STATE_PATH`.
  - On every tick start/success/failure, state is persisted atomically with fsync, updating mtime.
- **Explicit Disposition**: **Option 1 (State Path mtime Healthcheck)**.
  - Healthcheck directly consumes environment variable `ALPHA_REPLICATION_CONTROLLER_STATE_PATH` (default `/data/research-orchestrator/alpha-replication/controller_state.json`).
  - Freshness window is dynamically bounded to `max(2 * interval, interval + 180)` seconds (yielding 240s for the default 60s interval).
- **Residual Risk (for `L12-MANIFEST-001` waiver / residual risk manifest if needed)**:
  - *"State path liveness check relies on atomic state file mtime updates during controller ticks; if filesystem mounts fail or state file write is blocked by disk exhaustion, container healthcheck will fail after 240s, marking the container unhealthy (no automatic Compose restart without an external watcher)."*

---

## 2. Proposed Integrable Patches (for `L12-MANIFEST-001` Owner)

### Patch 1: Add Alive Heartbeat File to `services/search/scheduler_worker.py` & `docker-compose.yml`

In `services/search/scheduler_worker.py`:
```python
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# In main loop of run_tick / main worker loop:
alive_path_env = os.getenv("SEARCH_INDEX_SCHEDULER_ALIVE_PATH", "/data/search/search_scheduler_alive")
if alive_path_env:
    try:
        p = Path(alive_path_env)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
    except Exception as exc:
        logger.error("Failed to update alive path %s: %s", alive_path_env, exc)
```

In `docker-compose.yml` under `search-index-scheduler`:
```yaml
    environment:
      SEARCH_API_URL: http://search-svc:8098
      SEARCH_INDEX_SCHEDULER_INTERVAL_SECONDS: ${SEARCH_INDEX_SCHEDULER_INTERVAL_SECONDS:-300}
      SEARCH_INDEX_SCHEDULER_MAX_TICKS: ${SEARCH_INDEX_SCHEDULER_MAX_TICKS:-0}
      SEARCH_INDEX_SCHEDULER_FORCE_FULL: ${SEARCH_INDEX_SCHEDULER_FORCE_FULL:-false}
      SEARCH_INDEX_SCHEDULER_MATERIALIZE: ${SEARCH_INDEX_SCHEDULER_MATERIALIZE:-true}
      SEARCH_INDEX_SCHEDULER_ALIVE_PATH: ${SEARCH_INDEX_SCHEDULER_ALIVE_PATH:-/data/search/search_scheduler_alive}
    volumes:
      - search-data:/data/search
    healthcheck:
      test:
        [
          "CMD-SHELL",
          "python -c 'import os,sys,time;p=os.getenv(\"SEARCH_INDEX_SCHEDULER_ALIVE_PATH\",\"/data/search/search_scheduler_alive\");interval=float(os.getenv(\"SEARCH_INDEX_SCHEDULER_INTERVAL_SECONDS\",\"300\"));window=max(2*interval,interval+300);sys.exit(0 if os.path.exists(p) and time.time()-os.path.getmtime(p)<=window else 1)'",
        ]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 60s
```

### Patch 2: Add Controller State Healthcheck for `alpha-replication-worker` in `docker-compose.yml`

In `docker-compose.yml` under `alpha-replication-worker`:
```yaml
    healthcheck:
      test:
        [
          "CMD-SHELL",
          "python -c 'import os,sys,time;p=os.getenv(\"ALPHA_REPLICATION_CONTROLLER_STATE_PATH\",\"/data/research-orchestrator/alpha-replication/controller_state.json\");interval=float(os.getenv(\"ALPHA_REPLICATION_CONTROLLER_INTERVAL_SECONDS\",\"60\"));window=max(2*interval,interval+180);sys.exit(0 if os.path.exists(p) and time.time()-os.path.getmtime(p)<=window else 1)'",
        ]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 60s
```

---

## 3. Verification Executed

### Exact Verification Commands & Results:
1. **Python Distribution Provisioning**:
   ```bash
   python3 scripts/dev/provision_python_distribution.py
   ```
   *Result*: Success (`.venv-pantheon` created and linked).

2. **Target Worker Unit Tests**:
   ```bash
   PANTHEON_PY="$(python3 scripts/dev/provision_python_distribution.py --print-python)"
   "$PANTHEON_PY" -m pytest -q services/search/test_index_scheduler_worker.py services/research/alpha_replication/test_replication_controller.py
   ```
   *Result*: `7 passed, 3 warnings in 3.10s`.

3. **Full `alpha_replication` Test Suite**:
   ```bash
   PANTHEON_PY="$(python3 scripts/dev/provision_python_distribution.py --print-python)"
   "$PANTHEON_PY" -m pytest -q services/research/alpha_replication/
   ```
   *Result*: `27 passed, 13 warnings in 7.39s`.

---

## 4. Live Readback Commands for L12-MANIFEST-001 Owner (`Claude2`)

To execute per-probe live readbacks and container policy inspects matching `L12-MANIFEST-001` evidence conventions:

### A. Live Probe Execution Readbacks:
```bash
# 1. search-index-scheduler live probe execution
docker exec pantheon-search-index-scheduler-1 sh -c 'python -c "import os,sys,time;p=os.getenv(\"SEARCH_INDEX_SCHEDULER_ALIVE_PATH\",\"/data/search/search_scheduler_alive\");interval=float(os.getenv(\"SEARCH_INDEX_SCHEDULER_INTERVAL_SECONDS\",\"300\"));window=max(2*interval,interval+300);sys.exit(0 if os.path.exists(p) and time.time()-os.path.getmtime(p)<=window else 1)"'

# 2. alpha-replication-worker live probe execution
docker exec pantheon-alpha-replication-worker-1 sh -c 'python -c "import os,sys,time;p=os.getenv(\"ALPHA_REPLICATION_CONTROLLER_STATE_PATH\",\"/data/research-orchestrator/alpha-replication/controller_state.json\");interval=float(os.getenv(\"ALPHA_REPLICATION_CONTROLLER_INTERVAL_SECONDS\",\"60\"));window=max(2*interval,interval+180);sys.exit(0 if os.path.exists(p) and time.time()-os.path.getmtime(p)<=window else 1)"'
```

### B. Container Policy Readbacks:
```bash
# Inspect container restart policy and stop timeout
docker inspect --format '{{.HostConfig.RestartPolicy.Name}} {{.HostConfig.StopTimeout}}' pantheon-search-index-scheduler-1
docker inspect --format '{{.HostConfig.RestartPolicy.Name}} {{.HostConfig.StopTimeout}}' pantheon-alpha-replication-worker-1
```
