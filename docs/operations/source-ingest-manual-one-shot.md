# Source Ingestion Manual One-Shot Operation and Dev Reconcile-Only Policy

Status: canonical operational guide for Source Ingestion manual pull and dev posture
Task: PFG-SOURCE-MANUAL-ONCE-20260820

## 1. Principles & Architecture

- **Single Durable Owner**: `services/source_ingestion/controller_worker.py` is the sole canonical desired-state and schedule reconciler. No secondary daemon or legacy scheduler service is permitted.
- **Default Dev Posture**: The default controller mode is `reconcile_only` (`SOURCE_INGEST_CONTROLLER_MODE=reconcile_only`, `SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL=scheduled_tick`). In this mode, internal persona/connector desired state is continuously reconciled while attempting **zero provider egress** (`provider_egress_attempted: false`).
- **Bounded Manual Pull**: When acceptance testing or dev verification requires live provider data, an operator or test suite must explicitly invoke a bounded one-tick action targeting allowlisted connector IDs.
- **Fail-Closed Execution**: Unbounded provider pull (`max_ticks=0` in `reconcile_and_pull` mode) and connector selection in `reconcile_only` mode are strictly rejected with fatal configuration errors.

## 2. PR #5064 Candidate Reuse

Pantheon PR #5064 (`OPS-DEV-SOURCE-MANUAL-PULL-20260820-V2`) established:
1. Default Source controller running indefinitely as an internal `reconcile_only` owner.
2. Direct Compose defaults locked to `reconcile_only` with `MAX_TICKS=0`.
3. Bounded provider egress allowed only via explicit exact-host allowlist and single-tick limit.

Under `PFG-SOURCE-MANUAL-ONCE-20260820`, these guarantees are consolidated into the core Python contracts (`controller_worker.py`), the retained CLI entrypoint (`scripts/source_ingest_scheduler_once.py`), unit/contract tests, and operational documentation.

## 3. Retained CLI Entrypoint (`scripts/source_ingest_scheduler_once.py`)

`scripts/source_ingest_scheduler_once.py` is the consolidated, safe one-shot entrypoint. It invokes `controller_worker.run_controller_once()`, ensuring that:
- Exactly **one** bounded tick is executed (`max_ticks=1`).
- `ControllerStateStore` records state sequence numbers, restart counters, and failure summaries without state duplication or recursion.
- Terminal readback is validated against the live Source Ingestion HTTP service.
- The process terminates immediately upon tick completion with exit code `0` (success) or `1` (failure). No recurring process or timer daemon is created.

### Usage

```bash
# Pull a specific allowlisted connector exclusively (positional or flag):
python3 scripts/source_ingest_scheduler_once.py tw-official-market-datasets
python3 scripts/source_ingest_scheduler_once.py --connector tw-official-market-datasets --max-concurrency 2

# Run multiple connectors:
python3 scripts/source_ingest_scheduler_once.py --connector c1,c2 -c c3

# Run a single bounded reconcile-only tick (zero provider egress):
python3 scripts/source_ingest_scheduler_once.py --mode reconcile_only

# Override API URL, state file path, or token file:
python3 scripts/source_ingest_scheduler_once.py tw-official-market-datasets \
  --api-url http://127.0.0.1:8097 \
  --state-path /tmp/pantheon/source-ingest/controller_state.json \
  --token-file /data/source-ingest/controller_token
```

### Environment Variable Fallbacks

The script honours standard environment variables:
- `SOURCE_INGEST_API_URL`: base URL of the service (default `http://127.0.0.1:8097`).
- `SOURCE_INGEST_CONTROLLER_MODE`: `reconcile_and_pull` (default for CLI) or `reconcile_only`.
- `SOURCE_INGEST_CONTROLLER_EXCLUSIVE_CONNECTOR_IDS` / `SOURCE_INGEST_CONNECTORS`: comma-separated connector IDs.
- `SOURCE_INGEST_CONTROLLER_FORCE_CONNECTOR_IDS`: comma-separated forced connector IDs.
- `SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY`: max concurrent connector pulls (1..4, default 2).
- `SOURCE_INGEST_CONTROLLER_TIMEOUT_SECONDS`: HTTP request timeout (default 30.0s).
- `SOURCE_INGEST_CONTROLLER_STATE_PATH`: state file path.
- `SOURCE_INGEST_CONTROLLER_TOKEN_FILE`: token file path.

## 4. Verification & Validation Commands

```bash
# Verify Python compilation
python3 -m py_compile services/source_ingestion/controller_worker.py scripts/source_ingest_scheduler_once.py

# Run focused unit and contract test suites
pytest services/source_ingestion/tests/test_controller_worker_manual_once.py \
       scripts/tests/test_source_ingest_scheduler_once.py \
       services/source_ingestion/tests/test_controller_worker.py -q

# Run full source_ingestion suite
pytest services/source_ingestion/ -q
```
