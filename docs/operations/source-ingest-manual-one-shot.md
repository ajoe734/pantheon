# Source Ingestion Manual One-Shot Operation and Dev Reconcile-Only Policy

Status: canonical operational guide for Source Ingestion manual pull and dev posture
Task: PFG-SOURCE-MANUAL-ONCE-20260820
Clarified by: SRCM-P1-HOSTED-ACCEPTANCE-20260824

## 1. Principles & Architecture

- **Single Durable Owner**: `services/source_ingestion/controller_worker.py` is the sole canonical desired-state and schedule reconciler. No secondary daemon or legacy scheduler service is permitted.
- **Default Dev Posture**: The default controller mode is `reconcile_only` (`SOURCE_INGEST_CONTROLLER_MODE=reconcile_only`, `SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL=scheduled_tick`, `SOURCE_INGEST_CONTROLLER_MAX_TICKS=0`) with restart policy `unless-stopped`. It continuously reconciles internal desired state while attempting **zero provider egress** (`provider_egress_attempted: false`); provider pulls remain disabled by `PANTHEON_EXTERNAL_EGRESS=deny`.
- **Bounded Manual Pull**: When acceptance testing or dev verification requires live provider data, an operator or test suite must explicitly select the `source-ingest-scheduler` deployment profile. That profile runs a bounded one-tick `reconcile_and_pull` action targeting one exact connector ID and the reviewed exact provider-host allowlist. An explicit non-empty connector set (`exclusive_connector_ids`) is strictly required to prevent unbounded schedule claiming.
- **Cross-Process Serialization, Request Fingerprint & Deduplication**: Manual one-shot executions serialize via an exclusive cross-process file lock (`<state_path>.lock`). When an `operation_key` is supplied, operations are bound to a canonical request fingerprint (covering mode, connector selection, force connectors, API URL, truth level, and max concurrency). Replayed executions with matching fingerprints are deduplicated against recorded terminal operations, returning terminal readback immediately without invoking redundant provider ticks. Reusing an existing operation key with mismatched parameters raises a fatal conflict error (`operation_key_conflict`).
- **Fail-Closed Execution**: Unbounded provider pull (`max_ticks=0` in `reconcile_and_pull` mode), missing connector selection in `reconcile_and_pull` mode, connector selection in `reconcile_only` mode, and operation-key parameter conflicts are strictly rejected with fatal errors.

## 2. PR #5064 Baseline and Phase-1 Confirmation

Pantheon PR #5064 (`OPS-DEV-SOURCE-MANUAL-PULL-20260820-V2`) established:
1. Default Source controller running indefinitely as an internal `reconcile_only` owner.
2. Direct Compose defaults locked to `reconcile_only` with `MAX_TICKS=0`.
3. Bounded provider egress allowed only via explicit exact-host allowlist and single-tick limit.

`SRCM-P1-HOSTED-ACCEPTANCE-20260824` confirmed all three guarantees after the
bounded recovery run: normal dev is restored to `reconcile_only`,
`MAX_TICKS=0`, restart policy `unless-stopped`, and external egress `deny`.
The explicit provider-pull profile remains finite and allowlisted. The core
Python contracts (`controller_worker.py`), retained CLI entrypoint
(`scripts/source_ingest_scheduler_once.py`), Compose/deploy contract tests, and
this guide carry the corrected boundary.

## 3. Retained CLI Entrypoint (`scripts/source_ingest_scheduler_once.py`)

`scripts/source_ingest_scheduler_once.py` is the consolidated, safe one-shot entrypoint. It invokes `controller_worker.run_controller_once()`, ensuring that:
- Exactly **one** bounded tick is executed (`max_ticks=1`).
- `reconcile_and_pull` requires at least one explicitly selected connector ID.
- Cross-process file locking ensures bounded serialization.
- `ControllerStateStore` records state sequence numbers, restart counters, and bounded operation keys for idempotent replay.
- Terminal readback is validated against the live Source Ingestion HTTP service.
- The process terminates immediately upon tick completion with exit code `0` (success) or `1` (failure). No recurring process or timer daemon is created.

### Usage

```bash
# Pull a specific allowlisted connector exclusively (positional or flag):
python3 scripts/source_ingest_scheduler_once.py tw-official-market-datasets
python3 scripts/source_ingest_scheduler_once.py --connector tw-official-market-datasets --max-concurrency 2

# Run multiple connectors with an operation key for idempotent replay:
python3 scripts/source_ingest_scheduler_once.py --connector c1,c2 -c c3 --operation-key acceptance-20260820-001

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
- `SOURCE_INGEST_CONTROLLER_OPERATION_KEY`: operation key for concurrent deduplication and replay idempotency.
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
