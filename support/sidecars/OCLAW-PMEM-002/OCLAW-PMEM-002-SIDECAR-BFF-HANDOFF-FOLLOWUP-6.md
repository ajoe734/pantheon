# OCLAW-PMEM-002 BFF / Frontend Handoff Follow-up 6

Status: support-only dispatch packet; not canonical truth or runtime proof
Parent task: `OCLAW-PMEM-002`
Sidecar task: `OCLAW-PMEM-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-6`
Owner: Codex2
Reviewer: Codex

## Purpose And Boundary

This packet converts the post-merge gap identified in follow-up 5 into two
bounded downstream work items and a parent composition gate. It does not modify
the reconciler, BFF, frontend, Persona Registry, Memory Plane, governance, or
canonical contracts. Route and schema names remain parent/BFF-owner decisions.

The audit baseline is `origin/dev` at `a333f1556`. The parent implementation
present there still exposes desired reconcile intent from persona create/update
and a process-local aggregate `SyncReport`; no persona-scoped reconcile query,
durable attempt/result record, or observed reachability projection was found.
The newer parent task anchor `db8e7ca0f` is not part of this baseline and must
not be treated as delivered or composed until its task PR is merged and audited.

## Dispatch A — Durable Result And BFF Projection

**Owner:** parent reconciler owner plus Pantheon BFF owner  
**Repository:** `ajoe734/pantheon`  
**Dependency:** authoritative reconcile attempt/result storage chosen by parent

Deliver a persona-scoped, governed read model. A dedicated
`GET /bff/personas/{persona_id}/openclaw-reconcile` route is the least ambiguous
option, but an explicitly named observed sub-projection is acceptable.

Minimum contract:

- key attempts by persona, requested `sync_generation`, and an opaque attempt id;
- expose `queued`, `reconciling`, `ready`, `drifted`, `blocked`, `failed`, and
  `unavailable`, with unknown values failing closed;
- return desired and observed model/workspace/generation separately;
- include requested, acknowledged, completed, observed, and snapshot times;
- include reachability/probe evidence before permitting `ready`;
- retain last success as previous-generation evidence, never current readiness;
- return stable redacted reason code, retryability, repair action, and safe
  evidence references; and
- keep provider readiness and canonical memory/materialization health separate.

Required ordering tests:

1. duplicate delivery for the same generation returns the same durable result;
2. `reconciling` follows durable consumer acknowledgement;
3. observed readback and a sanitized successful probe precede `ready`;
4. completion for generation N cannot overwrite N+1;
5. a failed N+1 preserves N only as labelled historical evidence;
6. store/read failure returns unavailable rather than desired-only success; and
7. secrets, raw stdout/stderr, and exception text never enter the response.

Dispatch A is incomplete if it only expands
`metadata.openclaw_agent_reconcile` or serializes the aggregate `SyncReport`.

## Dispatch B — `execute-plans` Operator Consumption

**Owner:** frontend owner assigned by parent  
**Repository:** `ajoe734/execute-plans` (never a directory in Pantheon)  
**Dependency:** Dispatch A contract merged and available from the dev BFF

Implement the operator journey against the governed BFF projection:

- show persona mutation success separately from OpenClaw agent readiness;
- show desired and observed generation/model/workspace side by side during drift;
- enable conversation only for current-generation observed `ready` with probe
  evidence;
- disable conversation for queued, reconciling, drifted, blocked, failed,
  unavailable, and unknown states;
- retain last success with a `previous generation` label during a newer attempt;
- reject stale poll/event results using generation plus snapshot/observed time;
- expose Retry only when `retryable=true`, using an idempotency key;
- navigate server-owned repair actions without directly mutating provider or
  registry configuration; and
- render provider, agent reconcile, canonical memory, and materialization as
  distinct truth rows.

Frontend tests must cover every lifecycle value, an unknown value, stale
response ordering, failed-new-generation/last-success behavior, retry gating,
and accessible text for non-ready states. Mock-only evidence is contract proof,
not hosted readiness proof.

## Cross-Repository Composition Gate

The parent owner should not claim the parent acceptance complete until all
applicable rows pass:

| Gate | Required evidence | Fail-closed verdict |
|---|---|---|
| Durable consumption | Queryable attempt with request and acknowledgement | `pending` metadata is insufficient |
| Current truth | Desired/observed values and authoritative generation | Aggregate batch membership is insufficient |
| Reachability | Sanitized response through `model=openclaw/{persona_id}` tied to current attempt | Agent existence is insufficient |
| Ordering | Duplicate and late-generation automated tests | Last writer wins is rejected |
| BFF safety | Unavailable/unknown and secret-redaction tests | Desired-state fallback is rejected |
| Frontend safety | All non-ready states disable conversation | Optimistic readiness is rejected |
| Deployment identity | Merged Pantheon and `execute-plans` SHAs plus dev ancestry/readback | Branch-only anchors are rejected |

The parent reviewer should re-audit any changes after `a333f1556`, especially
the unmerged parent anchor, before accepting these gates as satisfied.

## Ownership And Composition

- `OCLAW-PMEM-002` owns adoption, durable lifecycle, reconcile ordering, probe,
  result taxonomy, and final hosted evidence.
- The BFF owner owns the governed projection and fail-closed serialization.
- The `execute-plans` owner owns operator rendering and stale-response defense.
- Provider and Memory Plane owners retain their independent truth surfaces.
- Reviewer `Codex` should verify this packet remains support-only and reject
  readiness inferred from desired intent, batch output, or an unmerged commit.

No canonical truth or primary runtime/registry/governance implementation is
changed by this follow-up.
