# OCLAW-PMEM-002 BFF / Frontend Handoff Follow-up 11

Status: support-only redispatch receipt; not canonical truth or runtime proof
Parent task: `OCLAW-PMEM-002`
Sidecar task: `OCLAW-PMEM-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-11`
Owner: Codex2
Reviewer: Codex

## Verdict

This redispatch found no new BFF or reconciler implementation to compose. The
follow-up 10 BFF-first dependency remains open, so another frontend handoff is
not actionable yet. This packet records the unchanged boundary and gives the
parent owner a concrete stop/redispatch rule; it changes no reconciler, BFF,
frontend, registry, Memory Plane, governance, or canonical contract.

Audit baseline: `origin/dev` at `565d910f35c2`. The parent branch
`origin/task/OCLAW-PMEM-002` remains at `db8e7ca0f2e0`; relative to current
`origin/dev`, its diff is limited to the parent task brief and execution-task
document. Since the follow-up 10 merge, the only relevant path change is that
support packet's finalization commit. No authoritative persona-scoped reconcile
result projection has landed.

Current BFF behavior therefore still exposes desired request metadata through
`metadata.openclaw_agent_reconcile` and recomputes desired inputs through
`GET /bff/personas/{persona_id}/runtime-profile`. Neither is evidence that a
consumer acknowledged the request, that the current generation matches the
observed OpenClaw agent, or that a sanitized current-generation probe passed.

## Parent-Owner Action

Do not dispatch frontend implementation from this packet. First assign a BFF
owner to adopt and deliver the implementation contract in follow-up 10. The
parent composition receipt must name:

1. the durable attempt/result write owner;
2. the exact governed persona-scoped read route and schema version;
3. the idempotency key and generation ordering rule;
4. the lifecycle mapping and fail-closed behavior;
5. the sanitized current-generation probe evidence; and
6. the Pantheon PR, merge SHA, focused tests, and representative response.

`ready` must remain impossible unless desired generation equals observed
generation and the sanitized probe succeeded for that generation. Desired
metadata, runtime-profile success, a stored agent id, provider readiness, an
aggregate sync report, or a previous-generation success cannot substitute for
those facts.

Only after that BFF receipt exists should the parent assign the conditional
`execute-plans` slice described in follow-up 10. The frontend must consume the
governed projection, reject stale responses, keep prior success labelled as
history, enable chat only for current-generation `ready`, and expose Retry only
when the server declares it safe.

## Redispatch Stop Rule

Do not create another identical `bff_handoff_packet` follow-up unless at least
one of these inputs changed:

- a durable result owner or adopted route/schema is named;
- a Pantheon BFF implementation PR or parent implementation commit exists;
- reviewer feedback requests a concrete packet correction; or
- the parent owner supplies a composition question not answered by follow-up
  10.

If none changed, the accurate status is “awaiting parent-selected BFF
implementation,” not a new support deliverable. This avoids accumulating
support artifacts that could be mistaken for implementation progress.

## Composition References

- Full BFF implementation contract, required tests, conditional frontend gate,
  and receipt template: `OCLAW-PMEM-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-10.md`.
- Earlier proposed query shape and operator journey:
  `OCLAW-PMEM-002-SIDECAR-BFF-HANDOFF.md`.
- Parent acceptance remains the execution packet
  `docs/bff/execution-tasks/2026-07-03-openclaw-persona-memory-gap/OCLAW-PMEM-002-openclaw-agent-reconcile.md`.

## Reviewer Checklist

- [ ] Baseline and parent-branch identities are reproducible.
- [ ] No desired-only surface is represented as observed reconcile truth.
- [ ] Provider, agent reconcile, canonical memory, and materialization truth
  remain separate.
- [ ] The packet adds no canonical route, schema, storage owner, or lifecycle
  decision.
- [ ] Frontend dispatch remains gated on a merged governed BFF projection.
- [ ] Approval is explicitly support-only and does not satisfy parent
  acceptance or hosted proof.

Reviewer `Codex` should either approve this as the final unchanged-state
redispatch receipt or request a concrete correction. The parent owner decides
whether and how to absorb it into the canonical implementation lane.
