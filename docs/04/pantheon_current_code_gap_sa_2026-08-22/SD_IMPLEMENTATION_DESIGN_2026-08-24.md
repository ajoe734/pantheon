# Pantheon Functional Closure SD — 2026-08-24

Status: worker-ready detailed design

Source: [`CURRENT_GAP_2026-08-24.md`](CURRENT_GAP_2026-08-24.md) and
[`SA_IMPLEMENTATION_PLAN_2026-08-24.md`](SA_IMPLEMENTATION_PLAN_2026-08-24.md)

Implementation rule: preserve the seven pre-existing immutable task specs, compose
them with the five materialized corrective roots/sidecars, and reuse current code.
Do not merge stale Pantheon PR #5147 as-is, call a corrective root a replacement, or
supersede task history.

## 1. Baseline invariants

These invariants apply to every change in this design:

1. `public.telemetry_events` is the canonical Lifecycle input.
2. `trade_journey_projection` is the only relational Lifecycle schema.
3. `loop-run-projector-scheduler` remains the Compose service key.
4. PostgreSQL is the target worker, BFF reader, and readiness authority.
5. JSON is migration-only and has no fallback role after accepted cutover.
6. FE/BFF identities are exact and automatic; a changing candidate does not require
   a new operator authorization for paper-only proof.
7. Management/AI and Agora acceptance performs real persisted domain actions.
8. Source Ingestion is reconcile-only; tests may manually pull once.
9. The proof profile is paper-only, bounded, and restored to public read-only.
10. No live-capital action, new security program, duplicate task, or duplicate product
    implementation is introduced.

## 2. Change map

| Design unit | Canonical task | Primary implementation surfaces |
|---|---|---|
| SD-LIFE-01 relational worker | `PFG-LIFECYCLE-POSTGRES-ACTIVATION-20260824` | `services/trade_journey/lifecycle_projector.py`, `projection_store.py`, migration/tests |
| SD-LIFE-02 BFF relational reader/readiness | `PFG-LIFECYCLE-POSTGRES-ACTIVATION-20260824` | BFF `trade_journey_projection_store.py`, `trade_journeys.py`, `main.py` |
| SD-LIFE-03 deploy/restart/cleanup | `LIFECYCLE-PROJ-RETIRE-001` after root handoff | Compose, retirement/deploy integration, runbook/evidence; serialized cleanup in overlapping source |
| SD-PROOF-01 automatic candidate binding | `PFG-CANDIDATE-AUTO-BINDING-20260824` | `cross_repo_release_controller.py`, its test, Pantheon `nonprod-deploy.yml` |
| SD-PROOF-02 bounded proof execution | `PFG-BOUNDED-FUNCTIONAL-CLOSURE-PROOF-20260824` | its three declared `execute-plans` proof workflows and evidence |
| SD-MGMT-01 hosted OpenClaw repair | `PFG-MGMT-OPENCLAW-HOSTED-REPAIR-20260824` | existing adapter/provider, focused tests, hosted smoke script |
| SD-MGMT-02 Management/AI journey | `PFG-MGMT-JOURNEY-E2E-20260820` | declared `execute-plans` Management hosted specs/evidence |
| SD-AGORA-01 Agora journey | `PFG-AGORA-JOURNEY-E2E-20260820` | existing Agora BFF and `execute-plans` Agora hosted specs |
| SD-CLEAN-00 read-only inventories | two exact `*-SIDECAR-CALLER-INVENTORY` tasks | one `support/sidecars/.../caller-inventory-20260824.md` per repo |
| SD-CLEAN-01 caller-backed cleanup | existing BE/FE consolidation parents | declared product candidates and disposition ledgers |
| SD-REL-01 final hosted acceptance | `PFG-HOSTED-ACCEPT-20260820` | deployment manifest/version/evidence only |

All five new nodes declare `PFG-FUNCTIONAL-REAUDIT-DOCS-20260824` as their only
canonical dependency. The seven older rows keep their original `depends_on` values.
Root-to-consumer ordering below is a governed handoff/coordination hold, not a
retroactive edit of immutable task fields.

## 3. SD-LIFE-01 — PostgreSQL transactional projector

Owner: `PFG-LIFECYCLE-POSTGRES-ACTIVATION-20260824`.

### 3.1 Reuse boundary

Reuse without redesign:

- `ProjectionStore` and migration
  `services/trade_journey/migrations/001_create_trade_journey_projection_schema.sql`;
- `ControllerStateRow`, event receipts, identity links, journeys, journey stages,
  loop runs, and quarantine rows;
- stable advisory-lock derivation;
- correlation-envelope validation and current materialization rules; and
- current event-type selection from canonical telemetry.

Do not import the Pantheon PR #5147 retirement-HMAC command, seven-day state machine, or
claimed hosted evidence. Do not keep normal-operation JSON serialization beside the
relational writer.

### 3.2 Runtime configuration

The root implements and tests these target values but does not edit Compose or deploy
workflows; SD-LIFE-03 activates them after the root merges:

| Variable | Target value/contract |
|---|---|
| `LIFECYCLE_PROJECTOR_WRITER_BACKEND` | `postgres`; no shadow/JSON steady state |
| `LIFECYCLE_PROJECTOR_PROJECTION_DSN` | DML-capable DSN for the existing schema |
| `LIFECYCLE_PROJECTOR_PROJECTION_SCHEMA` | `trade_journey_projection` |
| `PANTHEON_BFF_TRADE_JOURNEY_READER_BACKEND` | `postgres` after cutover gate |
| `PANTHEON_BFF_TRADE_JOURNEY_PROJECTION_DSN` | read-capable DSN for the same schema |
| `PANTHEON_BFF_TRADE_JOURNEY_PROJECTION_SCHEMA` | `trade_journey_projection` |
| `GIT_SHA` / `BFF_COMMIT` | exact deployed backend candidate SHA |

Configuration must fail closed when PostgreSQL is selected without a DSN. It must not
silently switch back to JSON.

### 3.3 Batch transaction contract

For one controller scope and batch:

1. acquire the stable PostgreSQL advisory lock;
2. load the controller row and select canonical telemetry where
   `ingested_seq > checkpoint_seq`, ordered ascending, limited by batch size;
3. compute a stable fingerprint and materialized mutations;
4. insert or validate the event receipt;
5. upsert identity links, journey/stage rows, and loop-run rows;
6. record malformed/conflicting input in quarantine without advancing a false
   accepted-live state;
7. update controller checkpoint, source high watermark, backlog, revision, mode,
   status, deployment SHA, freshness, and quarantine count; and
8. commit all mutations atomically, then release the lock.

Required idempotency behavior:

- same event ID and same fingerprint: no duplicate domain mutation;
- same event ID and different fingerprint: conflicting duplicate, quarantined/fails
  acceptance;
- crash before commit: no checkpoint advance;
- crash after commit: the receipt makes replay a no-op;
- two workers: only the advisory-lock winner mutates the controller scope.

### 3.4 Backfill and catch-up modes

Use the current migration/backfill entry points rather than inventing a new CLI. The
worker state transitions are:

| Mode | Input | `accepted_live` | Exit condition |
|---|---|---:|---|
| `backfill` | canonical history from configured start/checkpoint | false | historical range processed |
| `recovery` | records after current checkpoint | false | checkpoint reaches observed source high watermark |
| `live` | newly committed telemetry | true only after live poll | checkpoint equals source high, backlog zero, no unresolved quarantine |

An empty relational schema must be populated from real telemetry. Adopting counts
from JSON without corresponding relational rows is invalid. The observed zero-row
hosted schema cannot pass by merely seeding a controller row.

### 3.5 Controller acceptance contract

The accepted controller must return, at minimum:

```json
{
  "controller_id": "canonical-lifecycle-projector",
  "status": "ready",
  "mode": "live",
  "accepted_live": true,
  "deployment_sha": "<exact-bff-sha>",
  "checkpoint": 0,
  "source_high_watermark": 0,
  "backlog": 0,
  "quarantine_count": 0,
  "last_poll_at": "<fresh-rfc3339>"
}
```

The zero values above illustrate equality fields, not expected production counts.
Readiness passes only when `checkpoint == source_high_watermark`, backlog is zero,
unresolved quarantine is zero, deployment SHA matches, and freshness is within the
configured bound.

### 3.6 Projector tests

Extend focused tests under `services/trade_journey/`:

- schema bootstrap and empty-schema backfill;
- ordered multi-batch catch-up;
- same-fingerprint duplicate;
- conflicting duplicate/quarantine;
- identity conflict;
- atomic failure before controller advance;
- restart from committed checkpoint;
- advisory-lock contention;
- missing DSN/config fail-closed;
- exact deployment-SHA mismatch; and
- proof that normal PostgreSQL mode does not write JSON generation/temp files.

## 4. SD-LIFE-02 — BFF relational reader and readiness

Owner: `PFG-LIFECYCLE-POSTGRES-ACTIVATION-20260824`. This unit may edit only the
task's declared BFF source/tests and may not switch deployment or delete legacy data.

### 4.1 Reader behavior

When `PANTHEON_BFF_TRADE_JOURNEY_READER_BACKEND=postgres`:

- journey list/detail/stages and loop-run read surfaces use the existing relational
  reader only;
- pagination ordering and page tokens remain stable;
- no `FileStore`, JSON path, or local-snapshot fallback is consulted; and
- a missing reader/DSN raises typed projection unavailability rather than returning
  an empty successful payload.

Remove JSON-path alignment checks from the PostgreSQL branch. Keep them only in the
pre-cutover JSON branch until the deployment transition is complete; remove the
branch and path variables in SD-LIFE-03 after accepted cutover.

### 4.2 Readiness behavior

`_lifecycle_projector_dependency()` already contains most relational checks. Its
PostgreSQL result is authoritative when the reader backend is `postgres` and must
report:

- writer/reader backend `postgres`;
- controller scope and exact deployment SHA;
- status, mode, accepted-live, checkpoint, high watermark, backlog, quarantine,
  last-poll freshness, and reasons; and
- no claim that legacy JSON is an accepted reader.

The dependency is degraded for controller absence, non-ready status, non-live mode,
checkpoint mismatch, backlog, quarantine, last error, stale poll, missing DSN, or SHA
mismatch. Overall `/readyz` must reflect this dependency.

### 4.3 Read compatibility tests

For the same relational fixture rows, verify:

- list/detail/stage/loop-run response schemas remain compatible with current clients;
- sorting and pagination are deterministic;
- tenant/environment filtering is preserved;
- no JSON read occurs in PostgreSQL mode;
- PostgreSQL unavailable does not fall back to stale JSON; and
- all readiness-negative conditions produce an actionable reason.

## 5. SD-LIFE-03 — Deployment, restart/readback, and immediate retirement

Owner: the pre-existing `LIFECYCLE-PROJ-RETIRE-001`, only after the activation root
merges and an audited operator correction clears the stale seven-day/HMAC status
blockers. The correction is recorded through governed task state and does not rewrite
the old task's immutable spec.

### 5.1 Gate-before-switch sequence

`scripts/deploy_nonprod_vm.sh` and its focused tests implement one bounded transition:

1. build exact-SHA `loop-run-projector-scheduler` and `operator-bff` images;
2. verify the existing schema migration is applied;
3. start the projector with PostgreSQL writer selected while the old BFF candidate
   still reads JSON;
4. run real backfill/recovery until the controller meets section 3.5;
5. sample at least one journey and one loop-run identity from PostgreSQL;
6. recreate BFF with PostgreSQL reader selected;
7. verify `/bff/version`, `/readyz`, journey detail, and loop-run readback;
8. recreate both projector and BFF once more without rebuilding;
9. verify the same sampled identities and controller revision/checkpoint are readable;
10. atomically mark the candidate accepted; and
11. execute exact legacy cleanup.

Steps 3–9 are required functional evidence. No wall-clock soak is required.

### 5.2 Exact cleanup target

Delete only the contents owned by the legacy Lifecycle projector under:

```text
/data/bff/lifecycle-projection/
  controller_state.json
  health_state.json
  current/
  generations/
  staging/ and projector-created root temp files
```

Resolve and validate the literal directory before deletion. Do not delete the entire
`bff-data` volume, other BFF stores, `public.telemetry_events`, or any table in
`trade_journey_projection`.

After cleanup:

- Compose no longer mounts/sets Lifecycle JSON state paths for normal operation;
- BFF and projector source scans have no runtime dependency on the legacy files;
- disk usage confirms the expected large release (baseline approximately 21 GiB);
- two projector ticks and one restart produce no new generation/temp files; and
- retained runbook/evidence records paths, before/after bytes, candidate SHA, and
  sampled readback IDs without secrets.

### 5.3 Rollback

Before step 10, failure reactivates the prior JSON-reading candidate; no cleanup runs.
After step 10 and cleanup, repair is forward-only from canonical telemetry. Do not
restore the full-state JSON writer.

### 5.4 Deployment tests

Extend:

- `services/trade_journey/test_lifecycle_projector_compose.py`;
- `scripts/test_wait_for_bff_lifecycle_readiness.py`;
- `scripts/test_deploy_nonprod_bff_source_sha_contract.py`; and
- focused sections of `scripts/test_dev_environment_lease_deploy_contract.py`.

Positive tests cover backfill, switch, restart, cleanup, disk release, and no regrowth.
Negative tests cover failed backfill, mismatched SHA, stale controller, failed
readback, and cleanup never running before acceptance.

## 6. SD-PROOF-01 and SD-PROOF-02 — Candidate binding and bounded proof

SD-PROOF-01 owner: `PFG-CANDIDATE-AUTO-BINDING-20260824`.

SD-PROOF-02 owner: the pre-existing
`PFG-BOUNDED-FUNCTIONAL-CLOSURE-PROOF-20260824`, after the root merges and an audited
operator correction clears the stale old-pair/per-pair authorization status
blockers. Neither action changes the old task's immutable spec.

### 6.1 Source of truth

`scripts/cross_repo_release_controller.py` creates one immutable candidate record:

```json
{
  "candidate_id": "<immutable-id>",
  "pantheon_sha": "<bff-sha>",
  "execute_plans_sha": "<fe-sha>",
  "pair_id": "<derived-hash>",
  "profile": "write-proof",
  "expires_at": "<bounded-time>",
  "source_mode": "reconcile-only"
}
```

The pair ID uses the repository's current canonical derivation. Child workflows
receive these values as generated outputs. Canonical task descriptions may identify
the candidate ID, but must not hard-code a previous FE/BFF pair as a prerequisite.

### 6.2 Exclusive workflow ownership

SD-PROOF-01 edits only:

- `scripts/cross_repo_release_controller.py`;
- `scripts/test_cross_repo_release_controller.py`; and
- Pantheon `.github/workflows/nonprod-deploy.yml`.

It publishes immutable candidate outputs and prevents stale task/child inputs from
overriding them. It does not edit `execute-plans` product or proof workflows.

After root handoff, SD-PROOF-02 owns the pre-existing task's declared
`execute-plans` workflows `.github/workflows/pantheon-dev-fe-deploy.yml`,
`.github/workflows/pantheon-integration-gate.yml`, and
`.github/workflows/pantheon-proof-watchdog.yml`. They consume the parent outputs and
retain:

- immutable read-only/operator/write-proof build profiles;
- exact manifest and `/bff/version` verification;
- gate-before-switch and rollback-safe hosted symlink switch;
- bounded proof expiry/watchdog; and
- test-account secrets loaded only in the job environment.

The combined contract removes the normal dependency on a manually supplied
`proof_window_ack`, authorized-operator name, or stale exact-pair input. The parent
controller opening the paper-only candidate is the authorization event for this
development workflow.

### 6.3 Proof state machine

```text
CREATED -> IDENTITY_VERIFIED -> WRITE_PROOF_ACTIVE
        -> JOURNEYS_RUNNING -> PROOF_CAPTURED
        -> READ_ONLY_RESTORED -> COMPLETE
```

Any exception, cancellation, or expiry transitions through
`READ_ONLY_RESTORED`. Restoration is idempotent and checks the served manifest.

### 6.4 Tests

- candidate derives the exact current FE/BFF pair;
- stale child input cannot override the candidate;
- served identity mismatch fails before journeys;
- paper writes are enabled only in the proof profile;
- live-capital and recurring Source pull remain disabled;
- concurrent candidate cannot take the single dev proof lease;
- success restores read-only;
- failure, cancellation, and watchdog expiry restore read-only; and
- no credential or secret appears in artifacts/logs.

## 7. SD-MGMT-01 and SD-MGMT-02 — OpenClaw repair and hosted journey

### 7.1 Provider repair boundary

Owner: `PFG-MGMT-OPENCLAW-HOSTED-REPAIR-20260824`.

Its exclusive files are
`services/openclaw-gateway-adapter/assistant_openclaw_provider.py`, adapter `main.py`,
`test_assistant_openclaw_provider_live.py`, `test_main.py`, and
`scripts/openclaw-assistant-openclaw-live-smoke.sh`. It must not modify BFF main,
Compose/deployment, or frontend code.

Trace `POST /bff/management/nl/ask` through the BFF to
`services/openclaw-gateway-adapter/assistant_openclaw_provider.py`. Correct the
configuration/transport issue that produces `OPENCLAW_RESPONSES_UNREACHABLE`.
Do not add a second assistant endpoint, browser-direct provider call, or source-writing
repair capability.

Provider readiness requires one actual response through the hosted BFF. A synthetic
response or catching the error and displaying success is prohibited.

### 7.2 Journey steps

Owner: the pre-existing `PFG-MGMT-JOURNEY-E2E-20260820`. It consumes the hosted
provider repair but owns only its declared `execute-plans` specs/evidence. The hosted
test uses a unique run namespace and:

1. verifies candidate manifest, BFF version, write-proof profile, and paper mode;
2. opens Management and reads Formula, Activity, Paper Telemetry, and Postmortem
   surfaces, accepting typed unavailable only where the feature is genuinely absent;
3. submits a Management AI question and records an actual provider answer plus
   request/network correlation;
4. asks/confirms one supported paper-domain action;
5. records one command/action ID and one terminal receipt;
6. reloads and reads the resulting domain object and activity/audit entry; and
7. verifies no duplicate mutation on refresh/retry.

The action may be any currently supported non-capital Management mutation whose
backend persistence and terminal receipt are observable. The test must not select a
control that is merely rendered but disabled by the profile.

### 7.3 Evidence schema

```json
{
  "candidate": {"fe_sha": "", "bff_sha": "", "pair_id": ""},
  "profile": "write-proof",
  "test_run_id": "",
  "provider": {"request_id": "", "response_observed": true, "latency_ms": 0},
  "action": {"command_id": "", "object_id": "", "receipt_id": "", "count": 1},
  "reload_readback": true,
  "fixture_or_seed_count": 0,
  "read_only_restored": true
}
```

Negative cases: provider unreachable, nonterminal receipt, duplicate command,
readback absent after reload, candidate drift, fixture/seed import, or public profile
not restored all fail the task.

## 8. SD-AGORA-01 — Agora hosted journey

### 8.1 Reuse boundary

Use merged Agora code and existing BFF product-journey tests under
`tests/agora_product_journey/`. Use `execute-plans` hosted specs such as
`e2e/agora-strategy-workshop-hosted.spec.ts` and
`e2e/agora-winner-branch-hosted.spec.ts` as the test base. Do not create a parallel
Agora route or store.

### 8.2 Journey steps

1. verify the exact candidate and write-proof paper profile;
2. create unique Workshop inputs through browser/API behavior;
3. complete Consultation with correlated participant/opinion state;
4. create/use a real Trading Room workspace or pool;
5. execute the paper-domain decision flow and observe performance state;
6. record backend object IDs, command/receipt IDs, and network correlations;
7. reload/fresh-session readback the Workshop, workspace, decision, and performance
   result; and
8. verify cleanup/restoration does not erase the accepted persisted evidence.

Negative controls reject prebuilt IDs, seed/fixture endpoints, memory-only state,
cross-tenant leakage, duplicate commands, and a decision/performance result missing
after reload.

## 9. SD-CLEAN-00 and SD-CLEAN-01 — Inventory and simplification

### 9.1 Read-only sidecar ownership

`PFG-BE-CONSOLIDATE-20260820-SIDECAR-CALLER-INVENTORY` owns only Pantheon
`support/sidecars/PFG-BE-CONSOLIDATE-20260820/caller-inventory-20260824.md`.

`PFG-FE-CONSOLIDATE-20260820-SIDECAR-CALLER-INVENTORY` owns only `execute-plans`
`support/sidecars/PFG-FE-CONSOLIDATE-20260820/caller-inventory-20260824.md`.

Both sidecars are read-only audits: no product source, deployment, deletion, or
canonical parent-task state may change. Existing parent tasks retain all product and
deletion authority and start only after their declared dependencies are terminal.

### 9.2 Inventory record

Each backend/frontend candidate receives:

```json
{
  "path_or_symbol": "",
  "behavior": "",
  "callers": [],
  "runtime_or_deploy_refs": [],
  "replacement": "",
  "replacement_proof": "",
  "disposition": "retain|replace_then_delete|delete|defer",
  "validation": []
}
```

Caller scans include imports, lazy/dynamic imports, route manifests, OpenAPI clients,
workflows, Docker/Compose/deploy scripts, tests, and docs used as operational
contracts. Generated outputs are traced to their generator before deletion.

### 9.3 Parent change rules

- `retain`: name the canonical implementation and remove no code.
- `replace_then_delete`: migrate every caller, pass the replacement journey, then
  remove the old implementation and temporary adapter in the same bounded program.
- `delete`: prove zero callers and run relevant negative source scans/tests.
- `defer`: leave code unchanged and record the missing evidence.

Do not mark an old task superseded because a new plan exists. Do not add a compatibility
copy to reduce merge conflict. Frontend changes remain in `ajoe734/execute-plans`, not
inside the Pantheon checkout.

### 9.4 Validation

Backend and frontend focused unit/contract tests must pass, followed by the relevant
Management or Agora hosted journey if a product path changed. A source scan alone is
not sufficient for deletion of a route, deployment entry, persistence adapter, or
workflow target.

## 10. SD-REL-01 — Final hosted acceptance

The final task consumes evidence; it does not implement missing features.

Required artifact:

```json
{
  "manifest": {
    "fe_sha": "",
    "bff_sha": "",
    "pair_id": "",
    "deployment_state": "active",
    "public_profile": "read-only"
  },
  "bff_version_matches": true,
  "lifecycle": {
    "backend": "postgres",
    "ready": true,
    "restart_readback": true,
    "legacy_json_present": false,
    "legacy_regrowth_bytes": 0
  },
  "management_ai_journey": "passed",
  "agora_journey": "passed",
  "consolidation": "passed",
  "source_mode": "reconcile-only",
  "live_capital_actions": 0
}
```

`deployment_state` may use the repository's canonical accepted-state spelling; it
must not remain `standby` when represented as final active acceptance. The public
build values remain `VITE_BFF_MODE=live`, strict BFF fallback, real writes false, and
dev stub writes false.

## 11. Task update and completion rules

Do not update the seven existing task specs or dependencies. Preserve their immutable
payloads and history. Execute the correction through the five already materialized
nodes:

1. `PFG-LIFECYCLE-POSTGRES-ACTIVATION-20260824` owns the core before the old
   Lifecycle deployment/retirement task resumes.
2. `PFG-CANDIDATE-AUTO-BINDING-20260824` owns candidate derivation before the old
   bounded-proof task resumes.
3. `PFG-MGMT-OPENCLAW-HOSTED-REPAIR-20260824` owns provider repair before the
   unchanged Management journey runs.
4. `PFG-BE-CONSOLIDATE-20260820-SIDECAR-CALLER-INVENTORY` supplies only the BE
   inventory to its existing parent.
5. `PFG-FE-CONSOLIDATE-20260820-SIDECAR-CALLER-INVENTORY` supplies only the FE
   inventory to its existing parent.

The old Lifecycle retirement and bounded-proof tasks remain blocked until their
roots land. Then record the audited operator correction through the governed
lifecycle/status path to clear their stale status blockers. This preserves the old
seven-day/HMAC, old-pair, and per-pair authorization text as history; it does not
silently mutate those immutable fields. The Agora and Management tasks retain full
write-journey acceptance, the consolidation parents retain deletion authority, and
hosted acceptance remains last.

Task status changes must use the V2 TaskStore/supervisor path. Do not hand-edit
canonical queue JSON. A task is complete only with its required runtime artifact, not
because code was merged or a worker stopped.

## 12. Delivery sequence

For every repository change:

1. start from current repository `dev` in a clean branch/worktree;
2. keep Pantheon and `execute-plans` changes in their own repositories;
3. run focused positive and negative validation;
4. commit only owned files with required trailers;
5. push, open the repository PR, and pass required checks;
6. merge the exact validated head;
7. promote through the fixed dev VM SSH path;
8. verify served manifest/version and the design-unit runtime acceptance; and
9. attach evidence to the exact owning root, sidecar, or pre-existing consumer task.

Pantheon PR #5147 is not step 1 of this sequence. Its valid relational ideas are
re-applied to a clean current-dev branch with the reduced scope defined here.

## 13. Definition of done

Implementation is complete when all design-unit tests pass, the current hosted
candidate satisfies SD-REL-01, all five corrective nodes and all seven pre-existing
tasks are terminal with evidence, and there is no unresolved functional blocker.
Security hardening or additional governance may be planned separately but cannot be
used to represent this functional program as unfinished or to substitute for missing
product journeys.
