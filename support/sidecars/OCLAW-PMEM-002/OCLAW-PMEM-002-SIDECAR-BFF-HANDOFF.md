# OCLAW-PMEM-002 BFF / Frontend Handoff Packet

Status: sidecar proposal for parent-owner composition; not canonical truth  
Parent task: `OCLAW-PMEM-002`  
Sidecar task: `OCLAW-PMEM-002-SIDECAR-BFF-HANDOFF`  
Owner: Codex2  
Reviewer: Codex

## Purpose And Boundary

This packet gives the parent owner and the downstream BFF/frontend owners a
narrow integration checklist for persona-agent reconciliation. It does not
change the runtime reconciler, registry, governance policy, canonical Memory
Plane, or any L1 contract. Names and response shapes below are proposed
composition points; the parent owner decides what to adopt.

The intended truth boundary remains:

- the Persona Registry owns persona identity and policy metadata;
- `PersonaRuntimeProfile` resolves workspace/model/memory policy inputs;
- the OpenClaw reconciler owns the attempt to make the materialized agent match
  that desired state;
- BFF exposes desired state and observed reconcile evidence without claiming
  that a queued request is a reachable agent;
- the frontend renders BFF truth and never infers readiness from provider mount,
  a stored agent id, or `status=pending`.

## Current-State Readback And Gaps

Current `POST /bff/personas` and `PATCH /bff/personas/{persona_id}` build and
persist `metadata.openclaw_agent_reconcile`. The request contains a desired
agent id, `model_id=openclaw/{persona_id}`, workspace, sync generation, and
resolved model routing. Invalid routing becomes `blocked` with a repair action.

That is useful command intent, but it leaves these query gaps:

| Gap | Operator consequence | Handoff requirement |
|---|---|---|
| `pending` names `scripts/openclaw-sync-persona-agents.py` as consumer, but no durable consumption/readback evidence is exposed | UI can mistake intent for completion | Return attempt id, lifecycle state, timestamps, and consumer acknowledgement |
| Desired routing is present but observed OpenClaw agent model/workspace/generation are absent | Model drift cannot be explained | Project desired and observed values side by side, plus drift fields |
| Create/update response does not expose reconcile truth in stable `meta` or a dedicated query | Operator must inspect internal persona metadata | Add a persona-scoped reconcile query or stable runtime-status projection |
| Failure records do not have a documented error taxonomy | UI cannot offer a precise next action | Return stable `reason_code`, safe detail, retryability, and `repair_action` |
| No last-attempt / last-success evidence ref is shown | A stale prior success can look current | Include attempt timestamps, requested generation, reconciled generation, and evidence refs |
| Provider readiness and agent reconciliation can be conflated | Mounted auth may be shown as usable runtime | Keep provider auth/smoke truth separate from persona reconcile truth |
| Memory materialization belongs to the Memory Plane bridge, not this slice | Agent readiness could overclaim fresh memory | Report memory materialization as a separate nullable/degraded sub-status |

## Proposed BFF Query Projection

The parent owner may compose this into the persona runtime-profile surface or
publish a dedicated read route such as:

```text
GET /bff/personas/{persona_id}/openclaw-reconcile
```

Minimum operator-safe projection:

```json
{
  "data": {
    "persona_id": "persona-123",
    "agent_id": "persona-123",
    "model_id": "openclaw/persona-123",
    "state": "queued | reconciling | ready | drifted | blocked | failed | unavailable",
    "desired": {
      "workspace_ref": "/home/node/.openclaw/workspaces/persona-123",
      "primary_model": "provider/model",
      "sync_generation": 4
    },
    "observed": {
      "reachable": true,
      "workspace_ref": "/home/node/.openclaw/workspaces/persona-123",
      "primary_model": "provider/model",
      "sync_generation": 4,
      "observed_at": "RFC3339 timestamp"
    },
    "drift": [],
    "last_attempt": {
      "attempt_id": "opaque id",
      "requested_at": "RFC3339 timestamp",
      "completed_at": "RFC3339 timestamp or null",
      "reason": "persona_created | persona_updated | manual_retry",
      "reason_code": null,
      "retryable": false,
      "repair_action": null,
      "evidence_refs": []
    },
    "provider_readiness_ref": "opaque ref or null",
    "memory_materialization": {
      "state": "ready | stale | blocked | unavailable | not_requested",
      "generation": 7,
      "materialized_at": "RFC3339 timestamp or null",
      "evidence_refs": []
    }
  },
  "meta": {
    "snapshot_at": "RFC3339 timestamp",
    "source": "openclaw_reconciler_read_model",
    "truth_level": "observed | desired_only | unavailable"
  }
}
```

Contract guidance:

- `ready` requires observed reachability and equality of required desired versus
  observed fields for the requested generation. A stored request is never
  sufficient.
- `queued` and `reconciling` are non-terminal and must not enable conversation
  actions.
- `drifted` means observed state exists but differs; enumerate stable drift
  keys such as `model`, `workspace`, `identity`, `soul`, or `sync_generation`.
- `blocked` means a precondition is invalid and retry without repair is unsafe;
  `failed` means an attempt ran and failed. Preserve the last known good
  observation separately.
- `unavailable` is fail-closed when the reconciler/read model cannot be queried.
  Do not fall back to `metadata.openclaw_agent_reconcile.status=pending` as live
  truth.
- Do not expose auth tokens, provider secrets, raw CLI output, host paths beyond
  the already governed workspace reference, or unredacted exception strings.

## Command-Side Composition

The existing create/update flow may keep emitting desired reconcile intent, but
the parent implementation should make the handoff durable and idempotent:

1. Use a stable key derived from `persona_id + sync_generation` (and operation
   version if needed).
2. Persist request/attempt lifecycle independently of the frontend process.
3. Acknowledge consumption before moving `queued` to `reconciling`.
4. Record observed OpenClaw state after mutation and only then mark `ready`.
5. On replay, return the existing attempt/result rather than creating parallel
   mutations for the same generation.
6. On a newer generation, make older in-flight completion unable to overwrite
   newer truth.

Suggested stable failure classes are `runtime_profile_invalid`,
`provider_not_ready`, `agent_list_failed`, `agent_create_failed`,
`agent_update_failed`, `model_drift_unrepairable`, `identity_update_failed`,
`soul_write_failed`, `memory_materialization_failed`, and
`post_reconcile_probe_failed`. The parent owner should align these with the
reconciler's actual result type rather than copying strings from CLI stderr.

## Operator Journey

### Create persona

1. Operator submits the paper-persona create flow.
2. UI confirms persona creation separately from OpenClaw readiness.
3. Runtime card shows `Queued` with desired model and generation.
4. UI polls the BFF query or consumes a governed event until terminal state.
5. `Ready` enables the OpenClaw conversation entry point only when observed
   reachability is true. `Blocked`/`Failed` shows reason and repair action.

### Update model or persona identity

1. Operator previews the runtime-profile change and submits it.
2. UI immediately shows desired generation/model and retains the last observed
   state with a `Reconciliation in progress` label.
3. If observed state converges, UI shows the new model and completion time.
4. If it drifts or fails, UI shows desired versus observed fields and a precise
   repair/retry action; it must not silently display the desired model as active.

### Retry and diagnosis

- Retry is available only when BFF says `retryable=true` and must use an
  idempotency key.
- Profile/provider repair navigates to the owning surface; it is not implemented
  as client-side mutation of registry or provider state.
- A diagnostic details drawer may show attempt id, timestamps, generation,
  drift keys, and evidence links. It must redact raw provider/CLI details.

## Frontend Handoff

Recommended UI states and actions:

| BFF state | Label | Primary action |
|---|---|---|
| `queued` / `reconciling` | Reconciling | Refresh/status details; disable chat |
| `ready` | Ready | Open persona conversation |
| `drifted` | Drift detected | Review desired vs observed; retry only if allowed |
| `blocked` | Configuration blocked | Follow `repair_action` to owning surface |
| `failed` | Reconcile failed | Retry if allowed; otherwise inspect evidence |
| `unavailable` | Status unavailable | Refresh; do not claim readiness |

Frontend implementation rules:

- Consume the BFF projection; do not call OpenClaw directly or parse persona
  metadata/CLI output.
- Display provider auth/live-smoke, persona routing, agent reconcile, and memory
  materialization as separate truth rows.
- Preserve the last observed value during an update and label it stale/in
  progress; never replace it optimistically with desired state.
- Treat unknown enum values as unavailable/degraded, not ready.
- Use `snapshot_at`, `observed_at`, and generation to reject stale polling/event
  responses.
- Accessibility copy must include state text and repair guidance, not color only.

## Acceptance And Test Handoff

The parent/BFF slice should prove:

- create produces durable queued intent and a queryable attempt;
- successful consumption records observed agent identity, workspace, model, and
  generation before `ready`;
- update exposes desired/observed model drift until convergence;
- invalid routing is blocked with a stable reason and repair action;
- CLI/provider failure is redacted, terminal or retryable as declared, and does
  not erase last known good observation;
- duplicate command delivery is idempotent and an older generation cannot win;
- read-model outage returns unavailable/degraded truth rather than desired-only
  readiness;
- frontend disables chat for all non-ready/unknown states and renders desired
  versus observed values correctly;
- provider readiness and memory materialization failures remain independently
  visible.

Suggested integration evidence for parent closeout:

- focused BFF contract-test output;
- reconciler unit/parity-test output, including model drift and Memory section;
- one sanitized create/update attempt record with desired and observed values;
- hosted dev readback showing `model=openclaw/{persona_id}` response;
- explicit residual-risk note if durable consumption or hosted probing is not
  yet available.

## Composition Notes

- `OCLAW-PMEM-002` owns reconciler behavior, durable attempt truth, and model /
  identity / SOUL convergence.
- `OCLAW-PMEM-004` should consume the resulting read model for persona runtime
  and Management UI surfaces, and separately own canonical Memory Plane reads.
- `OCLAW-PMEM-005` should use the observed reconcile state and live probe as
  release evidence; it must not treat this packet as proof.
- This sidecar intentionally does not modify `services/control-plane/bff/main.py`,
  `integrations/openclaw/persona_agent_sync.py`, frontend source, registry,
  runtime profile, Memory Plane, or canonical documentation.
