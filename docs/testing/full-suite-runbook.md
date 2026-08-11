# Canonical Full-Suite Runbook And CI Matrix

Status: canonical local/CI test matrix for the activation-ready platform closure.
Last updated: 2026-04-30
Task: `TEST-FULLSUITE-RUNBOOK-CI-MATRIX`

This runbook defines the repeatable order for full-suite verification. The
default matrix is safe: it does not activate Qlib, TRL, RLlib, FinRL, W&B online
sync, paper execution, canary execution, or live execution. Rows that exercise
activation-ready behavior use local fixtures, explicit temporary environment
flags, and closed gates.

## Required Shell

Run from the repository root.

```bash
export PYTHONPATH="${PYTHONPATH:-.}"
```

Use Python 3.11 or newer locally. CI currently uses the workflow-specific
versions declared under `.github/workflows/`.

## Matrix

| Row | Purpose | Command | Default-safe rule |
|---|---|---|---|
| Stage-0 config | Validate the machine-readable CI matrix and deployment doc alignment. | `python3 scripts/ci_stage0.py validate` | No service startup, no broker path. |
| Orchestrator regression | Validate supervisor, status, provider permission, and dashboard helpers. | `PYTHONPATH=.orchestrator python3 -m pytest -q .orchestrator/test_adapter_delivery_policy.py .orchestrator/test_provider_permissions.py .orchestrator/test_common.py .orchestrator/test_supervisor.py && python3 -m pytest -q scripts/test_supervisor.py scripts/test_ai_status.py scripts/test_ci_stage0.py` | Local unit tests only. |
| Root collection | Prove pytest can collect without service-local module collisions. | `python3 -m pytest -q --collect-only` | Collection only; direct smoke entrypoints remain excluded. |
| Root execution | Run the default pytest suite. | `python3 -m pytest -q` | Uses pytest defaults; failures must be recorded as runtime/domain issues, not bypassed by changing collection. |
| Python compile | Catch syntax/import-path drift in smoke and orchestration scripts. | `python3 -m py_compile scripts/smoke_honest_stack.py scripts/smoke_openclaw_activation_ready_e2e.py scripts/smoke_oss_activation_ready_matrix.py scripts/smoke_dormant_oss_matrix.py scripts/run_research_activation_gates.py` | Compile only. |
| Dormant OSS matrix | Confirm dormant integrations stay gate-closed. | `python3 scripts/smoke_dormant_oss_matrix.py` | Must report `activated=false`; W&B online sync remains closed. |
| OSS activation-ready matrix | Exercise local offline fixture rows and default fail-closed rows. | `python3 scripts/smoke_oss_activation_ready_matrix.py` | Uses local artifacts only; no registry, governance, broker, or live writes. |
| OpenClaw activation-ready E2E | Prove OpenClaw degraded/ready facade, paper simulation fixture, and live denial. | `python3 scripts/smoke_openclaw_activation_ready_e2e.py` | Explicit fake upstream/runtime-manager/broker fixtures; live remains denied. |
| Compose config | Validate compose syntax and interpolation. | `docker compose config --quiet` | No container execution. |
| Full compose smoke | Start the default stack and run the smoke service. | `docker compose -p pantheon-fullsuite up -d --build && docker compose -p pantheon-fullsuite --profile smoke run --rm smoke-stack && docker compose -p pantheon-fullsuite down --volumes --remove-orphans` | Default services plus `smoke` profile only; optional upstream OpenClaw is not required. |
| Compose activation-ready OSS | Run the containerized OSS activation-ready matrix. | `docker compose --profile activation-ready-smoke run --rm oss-activation-ready-smoke-matrix` | Profile is disabled by default and uses closed production gates. |
| Compose OpenClaw E2E | Run the containerized OpenClaw activation-ready E2E. | `docker compose --profile openclaw-activation-ready-e2e run --rm openclaw-activation-ready-e2e` | Profile is disabled by default; production broker and live adapter envs are false. |
| Production activation gates | Report production activation truth from evidence, without promoting any row. | `python3 scripts/run_research_activation_gates.py` | Read/report only unless a future evidence packet is passed deliberately. |

## Explicit Environment Flags

Only use these flags in the row that names them. Do not export them globally.

| Flag | Allowed row | Safe value | Meaning |
|---|---|---|---|
| `PANTHEON_OFFLINE_GATE_ENABLED` | OSS activation-ready smoke | Script/container sets a task-scoped value | Opens only bounded offline fixture dispatch inside the smoke harness. |
| `RESEARCH_WORKER_GATEWAY_ENABLE_PRODUCTION_ADAPTERS` | OSS activation-ready smoke | `false` | Keeps production adapter routes closed. |
| `PANTHEON_ENABLE_WANDB_OFFLINE_STORE` | W&B focused smoke | `1` | Enables local offline-store compatibility only. |
| `PANTHEON_ENABLE_WANDB_DEFERRED_PREP` | W&B compatibility smoke | `1` | Legacy alias for offline/deferred prep compatibility. |
| `OPENCLAW_PRODUCTION_BROKER_ENABLED` | OpenClaw E2E | `false` | Keeps production broker disabled. |
| `OPENCLAW_CAPITAL_BINDING_ENABLED` | OpenClaw E2E | `false` | Keeps capital binding disabled. |
| `OPENCLAW_LIVE_ADAPTER_ENABLED` | OpenClaw E2E | `false` | Keeps live order routing disabled. |
| `PANTHEON_LIVE_BROKER_ENABLED` | OpenClaw E2E / dev compose | `false` | Keeps live broker disabled. |

## Focused Follow-Up Commands

Use these when a matrix row fails and the failing subsystem needs a narrow
rerun.

```bash
python3 -m pytest services/openclaw-gateway-adapter/test_main.py scripts/test_smoke_openclaw_activation_ready_e2e.py -q
PANTHEON_ENABLE_WANDB_OFFLINE_STORE=1 python3 services/registry/experiments/smoke_test.py --backend wandb
PANTHEON_ENABLE_WANDB_DEFERRED_PREP=1 python3 services/registry/experiments/smoke_test.py --backend wandb
SOURCE_INGEST_URL=http://127.0.0.1:8097 SEARCH_URL=http://127.0.0.1:8098 python3 scripts/smoke_source_search_prod_posture.py
```

The source/search production-posture smoke requires the relevant services to be
running with Postgres-backed posture enabled. It is not part of the default
offline local matrix.

## CI Mapping

| CI layer | Existing entrypoint | Runbook row |
|---|---|---|
| Stage 0 | `.github/workflows/stage-0-ci.yml` | Stage-0 config, changed target verify/build dry-run |
| Regression | `.github/workflows/regression-tests.yml` | Root collection, root execution, focused service regressions |
| Research regression | `.github/workflows/research-regression-tests.yml` | Dormant OSS, OSS activation-ready, research gate report |
| Syntax | `.github/workflows/syntax-tests.yml` | Python compile and Lean syntax checks |

When adding a new CI job, place it under one of these layers and link it back to
the matching row above. Do not add implicit production, paper, canary, or live
activation to a default workflow.

## Closeout Evidence

The 2026-04-30 closure used these already-green focused checks as inputs:

- Root collection reached `2214` tests without import mismatch.
- OpenClaw gateway adapter focused tests passed with `40` tests.
- OpenClaw activation-ready E2E passed with `13/13` rows.
- W&B dormant/offline compatibility smokes passed while online activation
  remained gated.
- `docker compose config --quiet` passed.

Full compose smoke is the canonical end-to-end row. If it fails after an
already-passing subsystem section, log the later subsystem failure as its own
runtime/domain task instead of weakening this matrix.
