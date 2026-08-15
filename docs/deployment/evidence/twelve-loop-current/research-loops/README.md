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
PANTHEON_L12_RESEARCH_E2E=1 \
  PANTHEON_L12_COMPOSE_PROJECT=pantheon \
  pytest -q tests/integration/l12/test_current_research_loops_deployed_e2e.py
```

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
