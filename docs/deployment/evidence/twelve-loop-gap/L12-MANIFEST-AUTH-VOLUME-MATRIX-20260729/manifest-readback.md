# L12 manifest auth / durable-volume applicability readback

Task `L12-MANIFEST-AUTH-VOLUME-MATRIX-20260729`, integrated by parent
`L12-MANIFEST-001` on `2026-07-29`.

## Result

The readback joins the literal `REQUIRED_LOOP_WORKERS` shell array to bare
Compose JSON (`docker compose -f docker-compose.yml --env-file /dev/null config
--format json`) and to source contracts named in `applicability-matrix.json`.
After the parent manifest integration, default admission is ready.

| surface | result | admission |
| :-- | --: | :-- |
| required worker coverage | 27 / 27 | pass |
| auth applicability entries | 27 / 27 | pass |
| auth configuration | 27 pass / 0 gaps | pass |
| durable-volume applicability entries | 27 / 27 | pass |
| durable-volume configuration | 27 pass / 0 gaps | pass |
| zero named-volume workers | 5 | all adjudicated |
| matrix consistency | true | pass |
| admission ready | true | pass |

## Per-worker matrix

`service_default` means the source enforces auth through an explicit source
default. `not_applicable` means the current process contract has no protected
HTTP boundary; tenant/actor metadata and storage credentials are not relabelled
as API authentication. `delegated` means an API or store named below owns all
durable claims, retries, idempotency, queues, or results.

| service | auth applicability | auth | volume applicability | volume |
| :-- | :-- | :--: | :-- | :--: |
| `source-ingest` | manifest_required | pass | manifest_required | pass |
| `strategy-distillation-worker` | not_applicable | pass | manifest_required | pass |
| `alpha-replication-worker` | not_applicable | pass | manifest_required | pass |
| `training-session-svc` | manifest_required | pass | manifest_required | pass |
| `training-session-preview-worker` | manifest_required | pass | manifest_required | pass |
| `policy-learning-svc` | manifest_required | pass | manifest_required | pass |
| `policy-learning-shadow-eval-scheduler` | manifest_required | pass | delegated | pass |
| `consultation-svc` | manifest_required | pass | manifest_required | pass |
| `deployment` | service_default | pass | manifest_required | pass |
| `deployment-outbox-consumer` | manifest_required | pass | delegated | pass |
| `runtime-manager` | manifest_required | pass | manifest_required | pass |
| `broker` | conditional | pass | manifest_required | pass |
| `capital` | manifest_required | pass | manifest_required | pass |
| `paper-fleet-reconciler` | manifest_required | pass | manifest_required | pass |
| `paper-signal-producer` | manifest_required | pass | delegated | pass |
| `reconciliation-drift-svc` | manifest_required | pass | manifest_required | pass |
| `reconciliation-drift-consumer` | manifest_required | pass | manifest_required | pass |
| `reconciliation-drift-scheduler` | manifest_required | pass | delegated | pass |
| `reconciliation-drift-incident-listener` | manifest_required | pass | manifest_required | pass |
| `evolution` | manifest_required | pass | manifest_required | pass |
| `evolution-dispatch-worker` | manifest_required | pass | manifest_required | pass |
| `evolution-daily-sweep-scheduler` | manifest_required | pass | delegated | pass |
| `evolution-threshold-sweep-producer` | not_applicable | pass | manifest_required | pass |
| `operator-bff` | manifest_required | pass | manifest_required | pass |
| `loop-run-projector-scheduler` | not_applicable | pass | manifest_required | pass |
| `search-svc` | not_applicable | pass | manifest_required | pass |
| `search-index-scheduler` | not_applicable | pass | manifest_required | pass |

## Zero local named-volume adjudication

These five services do not need a local named volume added. Each is a stateless
client; its owning API/store already holds the state that must survive worker
restart.

| service | durable state owner | why no local volume is required |
| :-- | :-- | :-- |
| `policy-learning-shadow-eval-scheduler` | `policy-learning-svc` | Dataset discovery, shadow runs, recovery, and results are API-owned; the sidecar retains no local checkpoint. |
| `deployment-outbox-consumer` | `deployment` | Outbox leases, claim tokens, inbox dedupe, retry, and terminal state are owned by the deployment API; the local health file is ephemeral. |
| `paper-signal-producer` | `signal-store` | Pending signal queues are written to the external signal-store; the producer retains no local authoritative state. |
| `reconciliation-drift-scheduler` | `reconciliation-drift-svc` | Scheduled window identity, retry outcome, and reconciliation records are service-owned; the poller retains no local checkpoint. |
| `evolution-daily-sweep-scheduler` | `evolution` | Daily sweep selection, idempotency, and resulting proposals/incidents are API-owned; the health file is ephemeral. |

`search-index-scheduler` is no longer delegated: bare Compose renders
`search-data:/data/search`, and the matrix now requires that named-volume target.

## Parent integration repairs

The parent manifest integration renders all previously declared auth gaps as
non-empty dev-safe configuration:

- `training-session-svc`: `TRAINING_SESSION_AUTH_MODE=strict` plus `TRAINING_SESSION_JWT_SECRET`.
- `training-session-preview-worker`: `TRAINING_SESSION_WORKER_TOKEN` plus `TRAINING_SESSION_TENANT_ID`.
- `consultation-svc`: `CONSULTATION_AUTH_REQUIRED=true` plus `CONSULTATION_SERVICE_TOKEN`.
- `deployment-outbox-consumer`: `PANTHEON_DEPLOYMENT_SERVICE_TOKEN` plus `PANTHEON_DEPLOYMENT_TENANT_ID`.
- `capital`: `CAPITAL_AUTH_MODE=strict` plus `CAPITAL_JWT_SECRET`.
- `reconciliation-drift-svc` and its three workers: token mode and shared `RECONCILIATION_DRIFT_AUTH_TOKEN`.
- `operator-bff`: strict/stub-false plus `PANTHEON_BFF_JWT_SECRET`.

These are local/dev placeholder credentials or verifier inputs. They do not
enable live broker, canary execution, live-capital writes, or provider egress.

## Validator behavior

Admission gate:

```bash
python3 scripts/validate_loop_worker_manifest_matrix.py \
  --matrix docs/deployment/evidence/twelve-loop-gap/L12-MANIFEST-AUTH-VOLUME-MATRIX-20260729/applicability-matrix.json \
  --compose-file docker-compose.yml \
  --format json
```

Expected now: exit `0`, `matrix_consistent=true`, `admission_ready=true`,
`auth.gap=0`, `durable_volume.gap=0`, `status=pass`.
