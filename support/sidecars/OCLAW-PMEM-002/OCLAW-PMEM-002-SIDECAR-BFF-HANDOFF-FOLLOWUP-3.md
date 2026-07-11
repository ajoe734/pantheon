# OCLAW-PMEM-002 BFF / Frontend Handoff Follow-up 3

Status: support-only composition packet; not canonical truth
Parent task: `OCLAW-PMEM-002`
Sidecar task: `OCLAW-PMEM-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-3`
Owner: Codex2
Reviewer: Codex

## Purpose And Boundary

This follow-up converts the earlier query-gap and operator-journey material into
a small parent-owned delivery sequence. It does not select a canonical route or
schema, and it does not modify the reconciler, BFF, frontend, registry, Memory
Plane, runtime policy, or governance implementation. The parent owner may
compose, amend, or reject these support recommendations.

The current branch still exposes desired reconcile intent through
`metadata.openclaw_agent_reconcile` on persona create/update and exposes desired
configuration through `GET /bff/personas/{persona_id}/runtime-profile`. No
persona-scoped `openclaw-reconcile` read route is present. Therefore neither
surface is sufficient evidence that an agent is reachable or current.

## Minimum Parent Delivery Slices

The parent can keep review and rollback narrow by composing in this order:

| Slice | Parent-owned output | Completion signal | Explicitly not implied |
|---|---|---|---|
| A. Durable attempt | Idempotent attempt keyed by persona and sync generation, with requested and acknowledged timestamps | Duplicate delivery returns the same attempt | Agent is reachable |
| B. Observed result | Persisted observed identity, workspace, model, generation, probe result, and redacted failure evidence | Current generation is queryable after execution | Memory is fresh |
| C. BFF projection | Governed desired/observed response with lifecycle, drift, retryability, repair action, snapshot time, and truth level | Operator can distinguish queued, ready, drifted, blocked, failed, and unavailable | Provider readiness equals agent readiness |
| D. Frontend consumption | `execute-plans` renders the projection, preserves last success, rejects stale generations, and fails closed | Chat is enabled only for current-generation observed readiness | Client may infer truth from persona metadata |
| E. Hosted proof | Sanitized projection plus a response through `model=openclaw/{persona_id}` | Evidence links the request generation to the observed agent and probe | This sidecar itself is runtime proof |

Slices A and B belong with the parent reconciler implementation. Slice C is a
BFF handoff. Slice D belongs in the separate `execute-plans` repository. Slice E
is parent closeout evidence. A partial merge should advertise only the slices
actually completed.

## Route And Data Ownership Map

Route naming remains a parent decision. If a dedicated route is chosen, the
earlier proposed shape can be exposed at
`GET /bff/personas/{persona_id}/openclaw-reconcile`. If an existing runtime
status route is extended instead, the observed reconcile result must remain a
named sub-projection and must not overwrite desired runtime-profile semantics.

| Datum | Write owner | BFF responsibility | Frontend responsibility |
|---|---|---|---|
| Desired workspace/model/generation | Persona runtime-profile / reconcile command path | Project without calling it observed | Label as desired |
| Attempt lifecycle and idempotency key | Reconcile attempt store | Return stable lifecycle and safe timestamps | Poll or refresh without creating duplicate mutations |
| Observed agent fields and reachability | Reconciler after post-operation probe | Redact and project current plus last-success evidence | Enable chat only for current-generation ready |
| Drift keys and reason code | Reconciler result taxonomy | Preserve stable enums; unknown or unavailable fails closed | Render desired versus observed and server-owned repair action |
| Provider readiness | Provider readiness owner | Return a reference or separate health row | Never substitute it for persona reconcile state |
| Canonical memory and workspace materialization | Memory Plane / bridge owners | Keep health and generation separate from reconcile readiness | Render as separate truth rows |

The BFF must not reconstruct observed truth from
`metadata.openclaw_agent_reconcile.status`, a desired runtime profile, provider
mount state, or the existence of an agent id.

## Required Ordering And Conflict Rules

1. Persist the desired generation and attempt before reporting the command as
   accepted.
2. Move `queued` to `reconciling` only after durable consumer acknowledgement.
3. Persist observed fields and probe evidence before marking the generation
   `ready`.
4. Use compare-and-set or equivalent ordering so generation N cannot overwrite
   N+1, even if N finishes later.
5. Preserve the last successful observation during a newer attempt, but label
   it with its generation and never use it to declare the newer generation
   ready.
6. Make retries idempotent. A retry may create a new attempt only under an
   explicit server-owned retry/version rule, not because a client poll was
   repeated.
7. On read-store or reconciler-result unavailability, return degraded or
   unavailable truth. Do not fall back to desired-only readiness.

## Focused Test Ownership

### Parent reconciler tests

- duplicate delivery for the same generation returns one durable attempt;
- consumer acknowledgement is recorded before execution state;
- observed model/workspace/identity/SOUL/generation and reachability precede
  `ready`;
- an older completion cannot replace a newer desired or observed generation;
- a failed newer attempt preserves, but does not promote, last-success data;
- provider/CLI failures produce redacted stable reason codes and retry policy.

### BFF contract tests

- create/update response remains honest about desired intent;
- the chosen reconcile query distinguishes every lifecycle state and exposes
  `truth_level` plus snapshot time;
- desired and observed values are both returned during drift;
- unknown state and read-model failure are fail-closed;
- secrets, raw CLI output, and unredacted exception text are absent;
- provider readiness, canonical memory health, and materialization generation
  remain separate from reconcile readiness.

### `execute-plans` tests

- conversation entry is disabled for queued, reconciling, drifted, blocked,
  failed, unavailable, and unknown states;
- a newer desired generation retains the prior observed display with a stale or
  previous-generation label;
- stale polling/event responses cannot regress the displayed generation;
- retry appears only when the BFF declares it retryable and uses an idempotency
  key;
- repair links use the server-provided action and do not mutate registry or
  provider configuration directly.

## Parent Acceptance Checklist

Before absorbing this packet, the parent owner and reviewer should be able to
answer yes to all applicable items:

- [ ] The authoritative attempt/result store and write owner are named.
- [ ] The current generation has durable consumer acknowledgement.
- [ ] `ready` requires a successful current-generation probe.
- [ ] Desired and observed values remain distinct in storage and projection.
- [ ] Ordering protects newer generations from late completion.
- [ ] Failure codes and evidence are stable, safe, and redacted.
- [ ] Provider, reconcile, canonical memory, and materialization health remain
      separate.
- [ ] BFF and frontend tests cover unavailable and unknown fail-closed states.
- [ ] Hosted evidence includes a sanitized readback and an actual response via
      `model=openclaw/{persona_id}`.
- [ ] Any unfinished slice is recorded as residual risk rather than described
      as delivered.

## Handoff

`OCLAW-PMEM-002` owns adoption and all canonical/runtime choices. The BFF owner
should implement only the governed projection selected by the parent. The
`execute-plans` owner should consume that projection without direct OpenClaw
calls or persona-metadata parsing. Reviewer `Codex` should confirm that this
packet remains support-only and that no desired-state surface is accepted as
observed readiness.

No canonical truth or runtime implementation is changed by this follow-up.
