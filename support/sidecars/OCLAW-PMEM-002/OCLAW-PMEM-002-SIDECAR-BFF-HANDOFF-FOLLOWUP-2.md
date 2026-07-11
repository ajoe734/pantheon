# OCLAW-PMEM-002 BFF / Frontend Handoff Follow-up 2

Status: support-only follow-up for parent-owner composition; not canonical truth
Parent task: `OCLAW-PMEM-002`
Sidecar task: `OCLAW-PMEM-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`
Owner: Codex2
Reviewer: Codex

## Scope And Non-Goals

This packet turns the earlier BFF/frontend handoff into a current-code gap map
and an implementation-ready operator journey. It may be adopted, amended, or
rejected by the parent owner. It does not change the reconciler, BFF routes,
frontend source, runtime registry, provider readiness, Memory Plane, governance,
or any canonical contract.

The parent implementation remains responsible for deciding route names and
authoritative result types. The frontend repository is `execute-plans`; no
frontend source belongs in this Pantheon worktree.

## Verified Current-Code Baseline

The following observations are bounded to the current task branch:

| Surface | Current behavior | Consequence |
|---|---|---|
| `POST /bff/personas` | Persists `metadata.openclaw_agent_reconcile` as `pending`, or `blocked` when runtime-profile resolution fails | Creation proves desired reconcile intent, not consumption or reachability |
| `PATCH /bff/personas/{persona_id}` | Replaces the metadata reconcile request with a new `pending`/`blocked` request | A later update has no queryable attempt identity and no protection visible to the UI against an older completion |
| `GET /bff/personas/{persona_id}` | Returns the persona DTO; reconcile metadata is not a stable operator projection | Persona detail cannot truthfully render agent readiness from its public DTO |
| `GET /bff/personas/{persona_id}/runtime-profile` | Builds desired workspace, routing, memory policy, and sync generation | This is desired configuration only; it does not prove observed OpenClaw state |
| `GET /bff/personas/{persona_id}/memory` | Calls an optional `list_memory_updates_for_persona`; when unavailable, returns an empty list with HTTP 200 | Empty memory is ambiguous between “no entries” and “read path unavailable” |
| Existing tests | Assert stored reconcile intent is `pending` and runtime-profile routing is desired-state ready/degraded | Tests do not yet prove consumer acknowledgement, observed convergence, or live agent reachability |

The desired request currently carries `reason`, `agent_id`,
`model_id=openclaw/{persona_id}`, consumer name, workspace, sync generation, and
model routing. It does not carry an attempt id, requested timestamp, observed
state, completion timestamp, stable failure code, retryability, or evidence
references.

## BFF Query Gap To Close

The smallest operator-safe addition is a persona-scoped reconcile read model.
It may be a dedicated route such as
`GET /bff/personas/{persona_id}/openclaw-reconcile` or a named sub-projection of
an existing runtime-status route. Do not overload the desired runtime-profile
route with implied observed truth.

Required fields:

```json
{
  "data": {
    "persona_id": "persona-123",
    "agent_id": "persona-123",
    "model_id": "openclaw/persona-123",
    "state": "queued | reconciling | ready | drifted | blocked | failed | unavailable",
    "desired": {
      "workspace_ref": "governed workspace ref",
      "primary_model": "provider/model",
      "sync_generation": 4
    },
    "observed": {
      "reachable": true,
      "workspace_ref": "governed workspace ref",
      "primary_model": "provider/model",
      "sync_generation": 4,
      "observed_at": "RFC3339 timestamp"
    },
    "drift": ["model"],
    "last_attempt": {
      "attempt_id": "opaque id",
      "reason": "persona_created | persona_updated | manual_retry",
      "requested_at": "RFC3339 timestamp",
      "acknowledged_at": "RFC3339 timestamp or null",
      "completed_at": "RFC3339 timestamp or null",
      "reason_code": "stable code or null",
      "retryable": false,
      "repair_action": null,
      "evidence_refs": []
    },
    "last_success": {
      "sync_generation": 3,
      "observed_at": "RFC3339 timestamp",
      "evidence_refs": []
    }
  },
  "meta": {
    "snapshot_at": "RFC3339 timestamp",
    "truth_level": "observed | desired_only | unavailable",
    "source": "openclaw_reconciler_read_model"
  }
}
```

Projection invariants:

- `ready` requires a successful reachability probe and equality of required
  observed fields at the requested generation. A stored `pending` request or a
  runtime profile with routing `status=ready` is insufficient.
- `queued` becomes `reconciling` only after durable consumer acknowledgement.
- `drifted` lists stable keys (`identity`, `workspace`, `model`, `soul`, or
  `sync_generation`) rather than raw textual diffs.
- `blocked` describes an invalid prerequisite; `failed` describes an attempted
  operation. Both use stable reason codes and safe repair actions.
- `last_success` remains visible during a newer attempt but is labelled stale;
  it cannot make the current generation appear ready.
- Query/read-model failure yields `unavailable`. It must never fall back to
  desired metadata as observed readiness.
- Unknown enum values fail closed in clients.
- Provider credentials, tokens, raw CLI output, and unredacted exceptions are
  never returned.

## Command And Ordering Handoff

The parent-owned command path should make `persona_id + sync_generation` (plus
an operation version if necessary) an idempotent reconcile key. It should:

1. persist the request and attempt before acknowledging the persona mutation;
2. record consumer acknowledgement before execution;
3. reconcile identity, workspace, model, SOUL, and generation;
4. probe the resulting agent and persist observed state before `ready`;
5. return the prior attempt/result on duplicate delivery;
6. reject an older generation's completion from replacing newer truth; and
7. preserve the last known good observation when a new attempt fails.

Candidate reason-code families for parent alignment are
`runtime_profile_invalid`, `provider_not_ready`, `agent_list_failed`,
`agent_create_failed`, `agent_update_failed`, `model_drift_unrepairable`,
`identity_update_failed`, `soul_write_failed`, and
`post_reconcile_probe_failed`. These are handoff suggestions, not a canonical
enum.

## Operator Journey For `execute-plans`

### Persona creation

1. Submit the existing Create Paper Persona flow.
2. Present paper-persona creation and OpenClaw reconciliation as separate
   outcomes: “Persona created” may coexist with “Agent queued”.
3. Show desired model and generation while polling/refreshing the reconcile
   projection.
4. Enable the conversation action only at observed `ready` for the current
   generation.
5. On `blocked` or `failed`, display the safe reason and server-owned repair
   action; do not mutate provider or registry configuration client-side.

### Persona or model update

1. Preview and submit the profile/persona change through the owning BFF action.
2. Preserve last observed values and label them “previous generation” while the
   new desired generation reconciles.
3. Render desired and observed model/workspace values side by side on drift.
4. Replace the active display only after current-generation observed
   convergence.

### Retry and diagnosis

- Offer Retry only when `retryable=true`; send an idempotency key.
- Use the server-provided `repair_action` to link to the owning configuration
  surface.
- A details drawer may show attempt id, timestamps, generations, drift keys,
  safe evidence links, and truth level.
- Disable chat for `queued`, `reconciling`, `drifted`, `blocked`, `failed`,
  `unavailable`, and unknown states.

## Truth Rows The UI Must Keep Separate

| Row | Question answered | Must not be inferred from |
|---|---|---|
| Runtime profile | What workspace/model/memory policy is desired? | Agent reachability |
| Provider readiness | Is provider auth plus governed live smoke usable? | Provider mount or stored model id |
| Agent reconciliation | Does the current OpenClaw agent match and respond? | Desired runtime-profile readiness |
| Canonical memory read | Can BFF retrieve governed persona memory? | An empty list without source health |
| Workspace materialization | Which memory generation was cached into OpenClaw context? | Agent reconcile success alone |

For `/bff/personas/{persona_id}/memory`, the BFF owner should make “zero
canonical entries” distinguishable from “memory facade unavailable”, using
surface-health metadata or an explicit degraded/unavailable result. This is a
handoff to the Memory Plane/BFF owner and is not part of the reconciliation
implementation itself.

## Acceptance Matrix For Parent Composition

| Case | BFF proof | Frontend expectation |
|---|---|---|
| Create accepted | Durable attempt is queryable at current generation | Persona-created success plus non-ready reconcile state |
| Consumer starts | Acknowledgement timestamp and `reconciling` | Continue status updates; chat disabled |
| Successful reconcile | Observed identity/workspace/model/generation and reachable probe precede `ready` | Enable conversation and show observation time |
| Desired model changes | New generation shows desired/new and observed/old until convergence | Never optimistically label the new model active |
| Invalid routing | `blocked`, stable reason code, repair action | Link to owning configuration surface |
| CLI/provider failure | Redacted failed result; retryability declared; last success preserved | Safe retry or evidence action; no raw error |
| Duplicate delivery | Same attempt/result returned | No duplicate progress cards or mutations |
| Older completion arrives late | Newer generation remains authoritative | Ignore stale poll/event by generation and timestamp |
| Read-model outage | `unavailable`/degraded truth | No readiness claim; refresh only |
| Memory facade missing | Distinct unavailable/degraded health, not silent empty-success semantics | Explain memory status independently of agent readiness |

Focused parent tests should cover every row above, including unknown-state
fail-closed behavior in `execute-plans`. Hosted closeout evidence still needs a
sanitized readback and one response through `model=openclaw/{persona_id}`; this
packet is not runtime proof.

## Composition Ownership

- `OCLAW-PMEM-002` parent owner decides and implements the durable attempt/read
  model, reconcile lifecycle, ordering, result taxonomy, and live proof.
- The BFF owner projects only governed desired/observed evidence and health.
- The `execute-plans` owner renders the projection without direct OpenClaw
  calls, metadata parsing, or client-side truth synthesis.
- Memory retrieval/materialization owners keep canonical Memory Plane status
  separate from agent reconciliation.
- Reviewer `Codex` should check that this remains support-only and that the
  parent does not claim readiness from `pending` intent or desired profile.

No canonical or runtime implementation is modified by this sidecar.
