# OCLAW-PMEM-002 BFF / Frontend Handoff Follow-up 4

Status: support-only adoption packet; not canonical truth or runtime proof
Parent task: `OCLAW-PMEM-002`
Sidecar task: `OCLAW-PMEM-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4`
Owner: Codex2
Reviewer: Codex

## Decision Requested From The Parent

Adopt an observed reconciliation read model as a distinct parent delivery
slice. Keep `GET /bff/personas/{persona_id}/runtime-profile` as desired-state
truth and expose observed agent convergence through a separate, persona-scoped
projection. A dedicated route such as
`GET /bff/personas/{persona_id}/openclaw-reconcile` is the least ambiguous
option, but the parent owns the final route name and schema.

This packet only supplies composition guidance. It does not modify the
reconciler, BFF, frontend, Persona Registry, Memory Plane, provider readiness,
governance, or canonical contracts. The frontend repository remains
`ajoe734/execute-plans`; no frontend source belongs in this worktree.

## Current-Code Evidence And Claim Limit

The current branch supports only these claims:

| Code surface | Verified behavior | Maximum truthful claim |
|---|---|---|
| `_openclaw_agent_reconcile_request` in `services/control-plane/bff/main.py` | Produces `pending` desired intent or `blocked` prerequisite state with consumer, workspace, generation, and model routing | Reconciliation was requested or could not be requested |
| `POST /bff/personas` and `PATCH /bff/personas/{persona_id}` | Persist that request in persona metadata | Persona mutation and desired reconcile intent were stored |
| `GET /bff/personas/{persona_id}/runtime-profile` | Recomputes desired workspace, model routing, memory policy, and generation | Desired runtime configuration is readable |
| `sync_persona_agents` in `integrations/openclaw/persona_agent_sync.py` | Returns process-local created/updated/failed lists; existing model drift is reported as update unavailable | A particular invocation attempted reconciliation |
| Existing BFF tests | Assert persisted `pending` intent and desired routing readiness/degradation | Intent/profile contract works in focused tests |

None of those surfaces proves durable consumer acknowledgement, current agent
reachability, observed workspace/model/generation, or a queryable last result.
The parent must not describe `pending`, desired routing `status=ready`, agent-id
existence, or provider readiness as observed agent readiness.

## Parent-Owned Contract Seam

The smallest useful seam consists of one durable write model and one governed
read projection:

1. Persist an attempt before acknowledging the persona mutation handoff. Use
   `persona_id + sync_generation` plus a server-owned operation version as the
   idempotency identity.
2. Record consumer acknowledgement before entering `reconciling`.
3. Reconcile identity, workspace, model, SOUL, and generation, then run a
   reachability probe.
4. Persist observed values and sanitized evidence before entering `ready`.
5. Reject completion for generation N when N+1 is authoritative.
6. Preserve a prior success after a newer failure, labelled with its original
   generation and never promoted to current readiness.
7. Project read-store failure as `unavailable`; never synthesize observed truth
   from persona metadata or runtime-profile output.

Minimum operator fields for the chosen projection:

```text
persona_id, agent_id, model_id
state: queued | reconciling | ready | drifted | blocked | failed | unavailable
desired: workspace_ref, primary_model, sync_generation
observed: reachable, workspace_ref, primary_model, sync_generation, observed_at
drift[]: identity | workspace | model | soul | sync_generation
last_attempt: attempt_id, reason, requested_at, acknowledged_at, completed_at,
              reason_code, retryable, repair_action, evidence_refs[]
last_success: sync_generation, observed_at, evidence_refs[]
meta: snapshot_at, truth_level: observed | desired_only | unavailable, source
```

`ready` is valid only when the current generation has a successful probe and
all required observed fields converge. Unknown lifecycle values fail closed.
Raw CLI output, exceptions, credentials, tokens, and provider secrets must not
cross the BFF boundary.

## BFF Handoff Checklist

- [ ] Name the durable attempt/result store and its write owner.
- [ ] Keep desired runtime-profile data separate from observed reconcile data.
- [ ] Return both desired and observed values while drift or a newer generation
      is in progress.
- [ ] Use stable redacted reason codes and server-owned repair actions.
- [ ] Declare retryability; require an idempotency key for a retry command.
- [ ] Protect the projection from late completion and stale read responses.
- [ ] Return `unavailable` when observed truth cannot be read.
- [ ] Keep provider readiness, canonical memory health, and memory
      materialization generation as separate truth rows.
- [ ] Add contract tests for every lifecycle state, unknown values, stale
      generation, duplicate delivery, outage, and secret redaction.

The current `model_drift_update_unavailable` result is a useful repair signal,
but it is not yet a complete public taxonomy. The parent should translate it
into a stable reason code and safe repair action instead of exposing the
process-local error payload verbatim.

## `execute-plans` Handoff

The frontend should consume only the governed BFF projection:

| Reconcile truth | Operator presentation | Conversation action |
|---|---|---|
| `queued` / `reconciling` | Show desired generation and progress; retain prior observation as previous generation | Disabled |
| `ready` | Show observed model/workspace, generation, and observation time | Enabled only for current generation |
| `drifted` | Show desired versus observed values and stable drift keys | Disabled |
| `blocked` | Show safe reason and link to server-owned repair surface | Disabled |
| `failed` | Show safe reason/evidence; Retry only when declared retryable | Disabled |
| `unavailable` / unknown | Show status unavailable; permit refresh only | Disabled |

Client rules:

- do not parse `metadata.openclaw_agent_reconcile` or call OpenClaw directly;
- do not infer readiness from runtime-profile or provider status;
- compare generation and timestamps before applying poll/event responses;
- preserve last-success context with an explicit stale/previous label;
- send an idempotency key for Retry and never mutate provider/registry config
  as a client-side repair shortcut; and
- present state text and repair guidance accessibly, not by color alone.

The operator journey remains two-outcome: persona create/update may succeed
while agent reconciliation is queued, blocked, or failed. The UI must state
both results without rolling back or overstating either one.

## Composition Acceptance Matrix

| Scenario | Parent/BFF evidence | Frontend assertion |
|---|---|---|
| Create/update accepted | Queryable current-generation attempt | Persona mutation success is separate from agent readiness |
| Consumer acknowledgement | Durable timestamp precedes `reconciling` | Progress shown; conversation disabled |
| Successful convergence | Observed fields plus probe precede `ready` | Conversation enabled for that generation only |
| Existing model drift | Stable drift/reason plus desired and observed models | No optimistic active-model label |
| Duplicate delivery | Same idempotent attempt/result | No duplicate mutation or progress card |
| Late older completion | Newer generation remains authoritative | Stale response ignored |
| New attempt fails | Prior success retained but labelled previous | Current generation remains non-ready |
| Read-model outage | `unavailable`, not desired fallback | No readiness claim |
| Memory/provider degradation | Independent health rows | Agent readiness is not substituted for those truths |

Hosted parent closeout still requires a sanitized projection readback tied to
the requested generation and one response through
`model=openclaw/{persona_id}`. This support packet is neither of those proofs.

## Ownership And Handoff

- Parent `OCLAW-PMEM-002` owns adoption, reconciler lifecycle, ordering,
  persistence, taxonomy, probe, and runtime evidence.
- The BFF owner owns the governed projection and fail-closed query semantics.
- The `execute-plans` owner owns rendering and stale-response protection.
- Memory/provider owners retain their independent truth surfaces.
- Reviewer `Codex` should reject any composition that treats desired intent as
  observed readiness or expands this sidecar into canonical implementation.

No canonical truth or primary runtime/registry/governance implementation is
changed by this follow-up.
