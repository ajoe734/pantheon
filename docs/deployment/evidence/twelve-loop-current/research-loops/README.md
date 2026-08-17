# Current research-loop deployed E2E evidence

Task: `L12-CURRENT-E2E-RESEARCH-20260814`

This directory holds bounded evidence for loops 1 through 4 over one running
Compose project. The deployed suite is
`tests/integration/l12/test_current_research_loops_deployed_e2e.py`.

The suite proves one exact identity chain through existing owners and public
authority boundaries:

```text
scheduled connector
  -> source-ingest-scheduler
  -> SourceRecord / source controller readback
  -> strategy-distillation-worker receipt
  -> StrategySpec / Registry readback
  -> alpha-replication-worker admission
  -> ExperimentRun / Research readback
  -> training-session-preview-worker session and evaluation
  -> Teaching terminal readback consumed by the operator/Learning verifier
```

Run from the repository root after starting the current Compose services:

```bash
env -u SOURCE_INGEST_CONTROLLER_MODE \
    -u SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL \
    -u SOURCE_INGEST_CONTROLLER_MAX_TICKS \
    -u SOURCE_INGEST_CONTROLLER_RESTART_POLICY \
    -u SOURCE_INGEST_CONTROLLER_FORCE_CONNECTOR_IDS \
    -u SOURCE_INGEST_CONTROLLER_EXCLUSIVE_CONNECTOR_IDS \
    -u PANTHEON_DEV_COMPOSE_PROFILES \
PANTHEON_L12_RESEARCH_E2E=1 \
  PANTHEON_L12_COMPOSE_PROJECT=pantheon \
  pytest -q tests/integration/l12/test_current_research_loops_deployed_e2e.py
```

When the test is run together with a pre-started isolated compose project, start
that project with a known-safe source-ingest owner profile to avoid inheriting
polluted local env and with an explicit pull-capable scheduler identity:

```bash
env -u SOURCE_INGEST_CONTROLLER_MODE \
    -u SOURCE_INGEST_CONTROLLER_MAX_TICKS \
    -u SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL \
    -u SOURCE_INGEST_CONTROLLER_RESTART_POLICY \
    -u SOURCE_INGEST_CONTROLLER_FORCE_CONNECTOR_IDS \
    -u SOURCE_INGEST_CONTROLLER_EXCLUSIVE_CONNECTOR_IDS \
    SOURCE_INGEST_CONTROLLER_MODE=reconcile_and_pull \
    SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL=scheduled_tick \
    SOURCE_INGEST_CONTROLLER_MAX_TICKS=1 \
    SOURCE_INGEST_CONTROLLER_RESTART_POLICY=no \
    PANTHEON_L12_COMPOSE_PROJECT=l12research814 \
    docker compose -p l12research814 -f docker-compose.yml -f /tmp/l12-research-814-compose.override.yml up -d --build \
      source-ingest-scheduler strategy-distillation-worker alpha-replication-worker training-session-preview-worker
```

Then run the `pytest` command above in the same shell (with `PANTHEON_L12_COMPOSE_PROJECT=l12research814` and the same env cleanup).

If the suite still times out at `source.durable_terminal_readback`, that is the expected
symptom of a pull-disabled scheduler mode (such as `SOURCE_INGEST_CONTROLLER_MODE=reconcile_only`).

Optional endpoint, timeout, Compose file, tenant, training token, and report
overrides use the `PANTHEON_L12_*` variables declared in the test module. The
default report is `run-report.json` in this directory.

The test uses unique records and first verifies that the generated SourceRecord
does not already exist. It does not import product stores, use temporary
authorities, invoke in-process service clients, or write repair tasks. At the
first failed boundary it stops the chain and atomically writes only the bounded
report: completed cases, the active/failed case, last successful boundary, and
first failure. Later cases are marked not reached by pytest rather than being
made green with seed data.
