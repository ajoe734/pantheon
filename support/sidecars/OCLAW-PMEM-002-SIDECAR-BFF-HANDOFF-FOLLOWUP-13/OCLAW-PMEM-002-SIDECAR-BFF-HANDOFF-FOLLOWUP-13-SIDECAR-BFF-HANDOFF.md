# OCLAW-PMEM-002 Follow-up 13 Sidecar BFF Handoff

Status: support-only composition packet; not canonical truth or runtime proof
Parent sidecar: `OCLAW-PMEM-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-13`
Helper task: `OCLAW-PMEM-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-13-SIDECAR-BFF-HANDOFF`
Owner: Codex
Reviewer: Codex2
Generated: 2026-07-11

## Recommendation

Return this packet to the parent owner without starting frontend work. The
parent sidecar's reviewed conclusion remains unchanged: desired reconcile
metadata exists, but no accepted BFF projection proves a consumer-owned,
current-generation OpenClaw reconcile result or a reachable
`openclaw/{persona_id}` agent.

This packet does not name a canonical route, schema, lifecycle, storage owner,
or frontend component. Those decisions remain with the `OCLAW-PMEM-002` owner.

## Observed BFF Gap

The current BFF create and patch paths persist
`metadata.openclaw_agent_reconcile` as a request with `pending` or `blocked`
status. `GET /bff/personas/{persona_id}/runtime-profile` projects desired
workspace, model routing, sync generation, and memory policy. These surfaces
do not, by themselves, expose consumer acknowledgement or observed agent
state.

Before frontend dispatch, the parent owner should provide an accepted,
sanitized projection that answers these queries without conflating desired and
observed state:

| Operator query | Required evidence boundary |
|---|---|
| Was the latest reconcile request consumed? | Request identity/generation joined to a consumer acknowledgement |
| Does the current OpenClaw agent match the desired profile? | Observed agent identity, workspace, model route, SOUL/render generation, and comparison result |
| Is `openclaw/{persona_id}` reachable now? | Current-generation sanitized probe result with observation time and bounded failure reason |
| What failed and what may the operator do? | Terminal or retryable result, precise reason, and server-advertised repair action |
| Is evidence still current? | Source, observed time, freshness policy, and generation mismatch rejection |

Provider readiness, canonical Memory Plane health, workspace materialization,
desired metadata, and a prior-generation success must remain independent from
current reconcile readiness.

## Minimum Result States

The eventual owner-approved contract should distinguish at least:

- request pending and not yet acknowledged;
- processing for the same request/generation;
- succeeded with matching observed agent and fresh reachability proof;
- failed with a bounded reason and repair action;
- blocked before consumption because desired routing/profile is invalid;
- stale or generation-mismatched evidence, which must not satisfy readiness;
- unavailable observation, which must not be rendered as success or an empty
  result.

These are handoff requirements, not canonical enum proposals.

## Operator Journey

Once the parent supplies merged BFF evidence and executable fixtures, the
frontend journey should:

1. show desired runtime profile separately from the latest reconcile result;
2. show request/generation identity, consumer acknowledgement, observation
   time, freshness, and observed-vs-desired comparison;
3. label reachability only from a current-generation probe;
4. keep provider, memory, and materialization health independently visible;
5. offer only BFF-advertised retry or repair actions, with their preconditions;
6. preserve the last bounded failure while a new attempt is pending, without
   implying that retry restored readiness.

## Frontend Start Gate

Do not assign an `ajoe734/execute-plans` implementation until all of the
following are pinned to immutable refs:

- accepted owner decision for the durable result owner and projection;
- merged Pantheon BFF implementation;
- sanitized success, pending, failed, stale/mismatched, blocked, and
  unavailable fixtures;
- focused contract tests proving generation joins and fail-closed behavior;
- a frontend task that targets the accepted BFF/fixture revisions and uses
  strict live-BFF mode.

No frontend source belongs in this Pantheon support slice.

## Reviewer Checklist

- [ ] The packet remains support-only and does not claim parent acceptance.
- [ ] Desired reconcile metadata is not represented as observed truth.
- [ ] Reachability requires a fresh current-generation probe.
- [ ] Generation mismatch, stale evidence, and unavailable observation fail
  closed.
- [ ] Provider, memory, materialization, desired profile, reconcile, and probe
  evidence remain separate.
- [ ] No route, schema, storage owner, enum, or browser-invented action is
  promoted to canonical truth.
- [ ] Frontend work remains deferred until merged BFF and fixture refs exist.

Reviewer `Codex2` should approve only the accuracy and usefulness of this
support packet. Parent owner `Codex2` decides whether to absorb it while
closing the parent sidecar; the `OCLAW-PMEM-002` owner retains implementation
and canonical composition authority.

## Evidence Consulted

- Parent sidecar artifact on
  `origin/task/OCLAW-PMEM-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-13`.
- `docs/bff/execution-tasks/2026-07-03-openclaw-persona-memory-gap/OCLAW-PMEM-002-openclaw-agent-reconcile.md`.
- Current BFF persona create, patch, and runtime-profile implementation and
  focused contract tests under `services/control-plane/bff`.
