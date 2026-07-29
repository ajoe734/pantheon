# L12 manifest auth / durable-volume applicability readback

Task `L12-MANIFEST-AUTH-VOLUME-MATRIX-20260729`, owner `Codex`, reviewer
`Antigravity`. This is an integrable workstream under `L12-MANIFEST-001`; it
does not edit that task's uniquely owned `docker-compose.yml`,
`scripts/deploy_nonprod_vm.sh`, or evidence directory.

## Result

The readback joins the literal `REQUIRED_LOOP_WORKERS` shell array to bare
Compose JSON (`docker compose -f docker-compose.yml --env-file /dev/null config
--format json`) and to source contracts named in
`applicability-matrix.json`.

| surface | result | admission |
| :-- | --: | :-- |
| required worker coverage | 27 / 27 | pass |
| auth applicability entries | 27 / 27 | structurally complete |
| auth configuration | 17 pass / 10 gaps | **fail closed** |
| durable-volume applicability entries | 27 / 27 | pass |
| named-volume target match | 27 / 27 | pass |
| zero named-volume workers | 6 | all adjudicated as delegated |

The existing parent readback's volume count is reproducible except for its
review prose calling this set seven services. Bare Compose has exactly six
required workers with zero named-volume mounts. A bind mount is deliberately
not counted as a durable volume.

## Per-worker matrix

`service_default` means the source enforces auth through an explicit source
default. `not_applicable` means the current process contract has no protected
HTTP boundary; tenant/actor metadata and storage credentials are not relabelled
as API authentication. `delegated` means an API or store named in §3 owns all
durable claims, retries, idempotency, queues, or results.

| service | auth applicability | auth | volume applicability | volume |
| :-- | :-- | :--: | :-- | :--: |
| `source-ingest` | manifest_required | pass | manifest_required | pass |
| `strategy-distillation-worker` | not_applicable | pass | manifest_required | pass |
| `alpha-replication-worker` | not_applicable | pass | manifest_required | pass |
| `training-session-svc` | manifest_required | **gap** | manifest_required | pass |
| `training-session-preview-worker` | manifest_required | **gap** | manifest_required | pass |
| `policy-learning-svc` | manifest_required | pass | manifest_required | pass |
| `policy-learning-shadow-eval-scheduler` | manifest_required | pass | delegated | pass |
| `consultation-svc` | manifest_required | **gap** | manifest_required | pass |
| `deployment` | service_default | pass | manifest_required | pass |
| `deployment-outbox-consumer` | manifest_required | **gap** | delegated | pass |
| `runtime-manager` | manifest_required | pass | manifest_required | pass |
| `broker` | conditional | pass | manifest_required | pass |
| `capital` | manifest_required | **gap** | manifest_required | pass |
| `paper-fleet-reconciler` | manifest_required | pass | manifest_required | pass |
| `paper-signal-producer` | manifest_required | pass | delegated | pass |
| `reconciliation-drift-svc` | manifest_required | **gap** | manifest_required | pass |
| `reconciliation-drift-consumer` | manifest_required | **gap** | manifest_required | pass |
| `reconciliation-drift-scheduler` | manifest_required | **gap** | delegated | pass |
| `reconciliation-drift-incident-listener` | manifest_required | **gap** | manifest_required | pass |
| `evolution` | manifest_required | pass | manifest_required | pass |
| `evolution-dispatch-worker` | manifest_required | pass | manifest_required | pass |
| `evolution-daily-sweep-scheduler` | manifest_required | pass | delegated | pass |
| `evolution-threshold-sweep-producer` | not_applicable | pass | manifest_required | pass |
| `operator-bff` | manifest_required | **gap** | manifest_required | pass |
| `loop-run-projector-scheduler` | not_applicable | pass | manifest_required | pass |
| `search-svc` | not_applicable | pass | manifest_required | pass |
| `search-index-scheduler` | not_applicable | pass | delegated | pass |

The machine-readable matrix carries the exact named-volume targets, environment
requirements, conditions, source evidence, and rationale for every row. This
table is only its review-friendly projection.

## Zero-volume adjudication

These six services do not need a volume added. Each is a stateless client; its
owning API/store already holds the state that must survive worker restart.

| service | durable state owner | why no local volume is required |
| :-- | :-- | :-- |
| `policy-learning-shadow-eval-scheduler` | `policy-learning-svc` | dataset discovery, shadow runs, recovery, and results are API-owned |
| `deployment-outbox-consumer` | `deployment` | outbox leases, claims, inbox dedupe, retry, and terminal state are API-owned; its health file is ephemeral |
| `paper-signal-producer` | `signal-store` | binding-scoped pending signal queues are external; the producer retains no checkpoint |
| `reconciliation-drift-scheduler` | `reconciliation-drift-svc` | window identity, idempotency, and reconciliation results are API-owned |
| `evolution-daily-sweep-scheduler` | `evolution` | sweep idempotency and resulting program/incident state are API-owned; its health file is ephemeral |
| `search-index-scheduler` | `search-svc` | index jobs, retention, materialization, and evidence are service-owned |

This adjudication is guarded, not prose-only: the validator rejects a delegated
row if a named volume appears, rejects a required row when its target set
differs, and rejects inventory drift between the matrix and
`REQUIRED_LOOP_WORKERS`.

## Auth gaps the parent manifest must repair or explicitly re-adjudicate

| service(s) | reproduced gap | integrable repair contract |
| :-- | :-- | :-- |
| `training-session-svc` | strict source default has no rendered JWT/JWKS/OIDC verifier | render `TRAINING_SESSION_AUTH_MODE=strict` and at least one supported verifier input |
| `training-session-preview-worker` | `_authority_headers` requires token + tenant, but neither key exists in its Compose environment | render non-empty `TRAINING_SESSION_WORKER_TOKEN` and `TRAINING_SESSION_TENANT_ID` |
| `consultation-svc` | source-supported token auth remains in legacy optional mode | render `CONSULTATION_AUTH_REQUIRED=true` and non-empty `CONSULTATION_SERVICE_TOKEN` |
| `deployment-outbox-consumer` | `_deployment_headers` requires deployment token + tenant; Compose supplies only the runtime-manager token | render non-empty `PANTHEON_DEPLOYMENT_SERVICE_TOKEN` and `PANTHEON_DEPLOYMENT_TENANT_ID` |
| `capital` | strict source default has no rendered JWT/JWKS/OIDC verifier | render `CAPITAL_AUTH_MODE=strict` and at least one supported capital/runtime verifier input |
| reconciliation service + consumer + scheduler + incident listener | service defaults to disabled auth and none of the four containers receives the supported token | render `RECONCILIATION_DRIFT_AUTH_MODE=token` on the API and the same non-empty `RECONCILIATION_DRIFT_AUTH_TOKEN` on all four |
| `operator-bff` | bare Compose selects strict auth and disables the stub, but JWT/JWKS/OIDC verifier inputs are all empty | provide a verifier in the accepted deployment env, or change the default contract through its owning auth lane; hosted injection must be separately read back |

The matrix does not infer secrets or print values. It tests only exact mode
values and whether credential/verifier inputs are non-empty. The parent owner
can repair these keys in its uniquely owned Compose surface, update the matrix
row from `gap` to `pass`, and run the same command without
`--allow-declared-gaps`.

## Validator behavior

Audit the current truth, including declared gaps:

```bash
python3 scripts/validate_loop_worker_manifest_matrix.py \
  --matrix docs/deployment/evidence/twelve-loop-gap/L12-MANIFEST-AUTH-VOLUME-MATRIX-20260729/applicability-matrix.json \
  --allow-declared-gaps
```

Expected summary:

```text
worker_count=27
auth.pass=17 auth.gap=10
durable_volume.pass=27 durable_volume.gap=0
zero_named_volume_count=6
matrix_consistent=true admission_ready=false status=pass
```

Run the admission gate:

```bash
python3 scripts/validate_loop_worker_manifest_matrix.py \
  --matrix docs/deployment/evidence/twelve-loop-gap/L12-MANIFEST-AUTH-VOLUME-MATRIX-20260729/applicability-matrix.json
```

Expected now: exit `1`, `matrix_consistent=true`, `admission_ready=false`,
`status=fail`. Exit `1` is the desired result until all declared gaps are
repaired. `--allow-declared-gaps` changes only the process exit policy; it does
not change `admission_ready`.

## Independent review

The reviewer should independently run both commands, confirm the first has no
matrix errors, confirm the second fails for the ten declared auth gaps, and
spot-check:

1. `training-session-preview-worker` and `deployment-outbox-consumer` fail
   closed in source when their omitted credential variables are absent.
2. reconciliation service/client sources support the exact token variables the
   matrix names.
3. the six zero-volume rows retain no local authoritative state and name the
   correct durable state owner.
4. `.orchestrator/config.json`, `docker-compose.yml`,
   `scripts/deploy_nonprod_vm.sh`, and the parent evidence directory are
   unchanged by this workstream.
