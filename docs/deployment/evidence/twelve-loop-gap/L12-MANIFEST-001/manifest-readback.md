# L12-MANIFEST-001 — final runtime manifest readback

Owner `Human/Ops` integration, reviewer `Codex` for the final replacement PR.
Cut v1.1.0 on 2026-07-29. The scan boundary for this cut is journal sequence
4575.

## Result

The parent manifest now composes the merged health/heartbeat, auth/volume, and
isolated restart-proof workstreams on top of `origin/dev`.

| surface | result |
| :-- | :-- |
| required loop workers in `REQUIRED_LOOP_WORKERS` | 27 / 27 present in bare Compose |
| default-on worker healthchecks | 27 / 27 |
| restart policy | 27 / 27 `unless-stopped` |
| graceful stop | 27 / 27 `30s` |
| auth applicability matrix | 27 / 27 pass, 0 gaps |
| durable-volume matrix | 27 / 27 pass, 0 gaps |
| isolated daemon restart proof | pass, same container `RestartCount` 0 → 1 |
| live/canary/live-capital defaults | remain false / denied |
| legacy static paper runtime duplicate | not resolved by bare Compose |

## Required worker inventory

Rendered with:

```bash
docker compose -f docker-compose.yml --env-file /dev/null config --format json
```

| loop | service | restart | healthcheck | stop grace | named volume targets |
| :-- | :-- | :-- | :--: | :-- | :-- |
| `source_ingestion` | `source-ingest` | unless-stopped | yes | 30s | `/data/source-ingest` |
| `strategy_distillation` | `strategy-distillation-worker` | unless-stopped | yes | 30s | `/data/source-ingest` |
| `alpha_replication` | `alpha-replication-worker` | unless-stopped | yes | 30s | `/data/research-orchestrator`, `/data/source-ingest` |
| `persona_teaching` | `training-session-svc` | unless-stopped | yes | 30s | `/data/training-session`, `/data/source-ingest` |
| `persona_teaching` | `training-session-preview-worker` | unless-stopped | yes | 30s | `/data/training-session` |
| `agora_interaction_evidence` | `policy-learning-svc` | unless-stopped | yes | 30s | `/data/policy-learning` |
| `human_imitation_shadow_evaluation` | `policy-learning-shadow-eval-scheduler` | unless-stopped | yes | 30s | delegated / none |
| `consultation` | `consultation-svc` | unless-stopped | yes | 30s | `/data/consultation` |
| `promotion_deployment` | `deployment` | unless-stopped | yes | 30s | `/data/governance`, `/data/capital`, `/data/runtime` |
| `promotion_deployment` | `deployment-outbox-consumer` | unless-stopped | yes | 30s | delegated / none |
| `promotion_deployment` | `runtime-manager` | unless-stopped | yes | 30s | `/data/runtime` |
| `capital_pool_execution` | `broker` | unless-stopped | yes | 30s | `/data/broker` |
| `capital_pool_execution` | `capital` | unless-stopped | yes | 30s | `/data/capital` |
| `capital_pool_execution` | `paper-fleet-reconciler` | unless-stopped | yes | 30s | `/data/runtime` |
| `capital_pool_execution` | `paper-signal-producer` | unless-stopped | yes | 30s | delegated / none |
| `telemetry_reconciliation` | `reconciliation-drift-svc` | unless-stopped | yes | 30s | `/data/reconciliation-drift` |
| `telemetry_reconciliation` | `reconciliation-drift-consumer` | unless-stopped | yes | 30s | `/data/reconciliation-drift` |
| `telemetry_reconciliation` | `reconciliation-drift-scheduler` | unless-stopped | yes | 30s | delegated / none |
| `telemetry_reconciliation` | `reconciliation-drift-incident-listener` | unless-stopped | yes | 30s | `/data/reconciliation-drift` |
| `evolution` | `evolution` | unless-stopped | yes | 30s | `/data/evolution`, `/data/incidents` |
| `evolution` | `evolution-dispatch-worker` | unless-stopped | yes | 30s | `/data/evolution` |
| `evolution` | `evolution-daily-sweep-scheduler` | unless-stopped | yes | 30s | delegated / none |
| `evolution` | `evolution-threshold-sweep-producer` | unless-stopped | yes | 30s | `/data/evolution` |
| `bff_health_monitoring` | `operator-bff` | unless-stopped | yes | 30s | `/data/bff`, `/data/governance`, `/data/runtime`, `/data/incidents` |
| `bff_health_monitoring` | `loop-run-projector-scheduler` | unless-stopped | yes | 30s | `/data/bff` |
| `shared search index behind source + agora reads` | `search-svc` | unless-stopped | yes | 30s | `/data/search`, `/data/source-ingest` |
| `shared search index behind source + agora reads` | `search-index-scheduler` | unless-stopped | yes | 30s | `/data/search` |

## Auth and durable-volume repairs integrated

The previous matrix workstream correctly found ten auth gaps. This parent cut
repairs them in `docker-compose.yml` and updates the matrix so the default
validator admits the manifest without `--allow-declared-gaps`:

- `training-session-svc`: strict mode plus local/dev JWT verifier input.
- `training-session-preview-worker`: worker token and tenant.
- `consultation-svc`: required bearer token mode plus local/dev token.
- `deployment-outbox-consumer`: deployment service token and tenant.
- `capital`: strict mode plus local/dev JWT verifier input.
- `reconciliation-drift-svc` and its three workers: token mode and shared token.
- `operator-bff`: strict/stub-false plus local/dev JWT verifier input.

`search-index-scheduler` was also corrected from delegated volume to
manifest-required `/data/search`, matching the rendered `search-data` mount.

## Health/heartbeat repairs integrated

All seven workers named by the reopened review now render healthchecks in bare
Compose:

- `alpha-replication-worker`
- `policy-learning-shadow-eval-scheduler`
- `paper-signal-producer`
- `reconciliation-drift-consumer`
- `reconciliation-drift-scheduler`
- `reconciliation-drift-incident-listener`
- `search-index-scheduler`

The source-level health contracts are supplied by the merged workstream evidence
under `L12-MANIFEST-HC-ALPHA-SRC-20260729`,
`L12-MANIFEST-HC-IMIT-CAP-20260729`, and
`L12-MANIFEST-HC-REC-20260729`.

## Restart proof integrated

`L12-MANIFEST-RESTART-PROOF-20260729/proof-run.json` proves an isolated,
non-shared worker restart: no Docker stop/kill API, no post-signal start/up,
same container ID, PID changed, and `RestartCount` increased from 0 to 1. The
shared `pantheon` Compose project snapshot was unchanged and the isolated proof
project cleaned up to zero containers and zero networks.

## Safety defaults retained

The cut does not enable live trading or production writes. Rendered defaults
retain `PANTHEON_LIVE_BROKER_ENABLED=false`,
`PANTHEON_CANARY_EXECUTION_ENABLED=false`, `BROKER_PAPER_ENABLED=false`, source
egress deny-by-default, and no external source-refresh always-on profile.

## Admission commands

```bash
docker compose -f docker-compose.yml --env-file /dev/null config --quiet
python3 scripts/validate_loop_worker_manifest_matrix.py   --matrix docs/deployment/evidence/twelve-loop-gap/L12-MANIFEST-AUTH-VOLUME-MATRIX-20260729/applicability-matrix.json   --compose-file docker-compose.yml   --format json
python3 scripts/validate_twelve_loop_gap_evidence.py   docs/deployment/evidence/twelve-loop-gap/L12-MANIFEST-001/evidence.json   --json
```

Expected after evidence sealing: compose pass, matrix `admission_ready=true`,
and evidence validator zero rejections.
