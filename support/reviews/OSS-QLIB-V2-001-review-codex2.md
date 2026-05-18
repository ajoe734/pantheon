# Review: OSS-QLIB-V2-001 Qlib production rolling admission

Reviewer: Codex2
Owner: Codex
Date: 2026-05-17
Status: approved

## Scope

Task-owned files reviewed:

- `services/research/qlib/production_rolling_run.py`
- `services/research/qlib/registry_admission_packet.py`
- `services/research/qlib/test_production_rolling_run.py`
- `support/evidence/OSS-QLIB-V2-001/admission_packet.json`

## Findings

No blocking findings.

The production rolling runner returns a schema-valid `ExperimentRun`, binds the
repo-local Qlib materialization to the MGMT-QLIB-001 dataset manifest and
MGMT-QLIB-002 StrategySpec packet, enforces the 50-instrument and two-year
production floors, and emits per-window `rolling_sharpe` and `rolling_ic`
metrics. The model artifact projection validates against the registry entry
schema with `artifact_type=model_artifact`, `artifact_state=draft`, checksum,
lineage refs, and `deployment_stage=none`.

The admission packet is correctly scoped to candidate review only:

- `registry_request.registry_write_performed=false`
- `registry_request.requested_artifact_state=candidate`
- `candidate_artifact.artifact_state=draft`
- `downstream_scope.deployment_stage=none`
- `downstream_scope.order_route=none`
- `downstream_scope.broker_session_opened=false`
- `safety_assertions.no_registry_write=true`

Minor non-blocking note: regenerating the packet through the CLI does not
byte-match the checked-in JSON because the underlying rolling run uses a random
`run_id`. The regenerated packet preserved the target id, model checksum,
gate results, rolling window count, and rolling metric summary.

## Verification

Commands run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/research/qlib/test_production_rolling_run.py -q
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/research/qlib/test_rolling_pipeline.py services/research/qlib/test_production_rolling_run.py -q
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/research/qlib/production_rolling_run.py services/research/qlib/registry_admission_packet.py services/research/qlib/test_production_rolling_run.py
PYTHONDONTWRITEBYTECODE=1 python3 services/research/qlib/registry_admission_packet.py --output /tmp/oss-qlib-v2-001-admission-packet-review.json --created-at 2026-05-17T11:45:00Z
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/research/qlib -q
```

Results:

- Production rolling tests: 5 passed.
- Rolling plus production tests: 7 passed.
- `py_compile`: passed.
- CLI packet emission: passed; regenerated gate and metric values matched the checked-in packet except for UUID-derived run refs.
- Full Qlib test slice: 40 passed.

## Decision

Approved. Codex should perform owner closeout per
`.orchestrator/skills/task-closeout-finalization.md`.
