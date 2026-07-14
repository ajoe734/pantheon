# OCLAW-PMEM-002 BFF / Frontend Handoff Follow-up 8

Status: support-only downstream handoff; not canonical truth or runtime proof
Parent task: `OCLAW-PMEM-002`
Sidecar task: `OCLAW-PMEM-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-8`
Owner: Codex2
Reviewer: Codex

## Purpose And Boundary

This packet turns the open verdict in follow-up 7 into an assignable BFF-first
handoff. It does not modify the reconciler, BFF, frontend, Persona Registry,
Memory Plane, provider policy, governance, or canonical contracts. The parent
owner retains every route, schema, storage, and implementation decision.

The composition baseline is `origin/dev` at `21e5a6d23`, which includes
follow-up 7 through PR #3259. Follow-up 7 established that the parent evidence
anchor does not provide durable reconcile results, a governed observed-state
projection, or frontend consumption. Those gaps remain prerequisites rather
than evidence that can be inferred from desired metadata or aggregate sync
output.

## BFF-First Assignment Packet

The parent owner should assign one Pantheon delivery slice before opening the
frontend slice.

### Pantheon BFF contract slice

**Repository:** `ajoe734/pantheon`
**Merge target:** `dev`
**Composition owner:** `OCLAW-PMEM-002` parent owner plus assigned BFF owner

Deliver an operator-safe persona-scoped reconciliation projection backed by an
authoritative attempt/result owner. A dedicated route such as
`GET /bff/personas/{persona_id}/openclaw-reconcile` remains the least ambiguous
option, but its name is not selected by this packet.

The slice is ready for frontend consumption only when it provides:

- a stable attempt identity and requested `sync_generation`;
- lifecycle values for queued, acknowledged/reconciling, ready, drifted,
  blocked, failed, and unavailable;
- separate desired and observed identity, workspace, model, and generation;
- requested, acknowledged, completed, observed, and snapshot timestamps;
- current-generation reachability/probe evidence as a prerequisite for ready;
- labelled prior-success evidence that cannot promote a newer generation;
- stable redacted reason code, retryability, repair action, and safe evidence
  references; and
- separate references or truth rows for provider readiness, canonical memory,
  and memory materialization.

The projection must fail closed when its result owner is unavailable or emits
an unknown lifecycle value. It must not reconstruct observed readiness from
`metadata.openclaw_agent_reconcile`, runtime-profile readiness, agent id
existence, provider mount state, or aggregate `SyncReport` membership.

### Required BFF acceptance evidence

| Scenario | Required assertion |
|---|---|
| Duplicate delivery | The same persona and generation resolve to one durable attempt/result under the server-owned idempotency rule |
| Consumer start | `reconciling` appears only after durable acknowledgement |
| Successful reconcile | Observed readback and sanitized probe evidence are persisted before ready |
| Late completion | Generation N cannot replace desired or observed truth for N+1 |
| Failed newer generation | The last success remains visible only as previous-generation evidence |
| Result owner outage | The BFF returns unavailable/degraded truth and never desired-only success |
| Unknown lifecycle | Serialization fails closed to unavailable/degraded behavior |
| Failure detail | Tokens, credentials, raw stdout/stderr, CLI payloads, and exception text are absent |
| Truth separation | Provider, agent reconcile, canonical memory, and materialization health cannot imply one another |

The merge handoff should name the chosen write owner, route, contract-test
command, PR number, merge SHA, and any enum or schema version the frontend must
consume. Without those items, the frontend dependency is not satisfied.

## Conditional `execute-plans` Assignment

**Repository:** `ajoe734/execute-plans` (never materialized inside Pantheon)
**Merge target:** `main`
**Dependency:** the Pantheon projection above is merged and available to the
frontend owner as a versioned contract or captured response

The frontend owner should then:

1. report persona mutation and OpenClaw reconciliation as separate outcomes;
2. enable conversation only for current-generation observed ready with probe
   evidence;
3. disable conversation for queued, reconciling, drifted, blocked, failed,
   unavailable, and unknown states;
4. display desired and observed values together during drift or convergence;
5. retain an older success with an explicit previous-generation label;
6. reject stale poll/event results using generation plus snapshot/observation
   time;
7. offer Retry only when the BFF declares it retryable and send an idempotency
   key; and
8. navigate server-owned repair actions without directly changing provider,
   registry, or OpenClaw configuration.

Frontend contract tests must cover every known lifecycle, an unknown value,
late response ordering, failed-new-generation history, retry gating, and
accessible non-ready text. The frontend must consume the BFF projection rather
than call OpenClaw or parse persona metadata.

## Cross-Repository Release Gate

Neither slice closes the parent alone. Parent closeout should record all of:

- merged Pantheon PR and merge SHA containing the authoritative result owner
  and governed BFF projection;
- merged `execute-plans` PR and merge SHA consuming that projection;
- deployed SHA ancestry for the dev BFF and frontend;
- a sanitized current-generation projection readback;
- an actual response through `model=openclaw/{persona_id}` tied to that
  generation; and
- explicit residual risk for any unproven provider or memory-materialization
  condition.

Mock responses, local tests, desired metadata, agent existence, or an unmerged
task anchor cannot substitute for the hosted response and deployment identity.

## Parent And Reviewer Handoff

- `OCLAW-PMEM-002` decides whether to adopt this split and owns the durable
  lifecycle, ordering, observed probe, taxonomy, and parent closeout evidence.
- The assigned Pantheon BFF owner publishes the fail-closed projection and its
  contract evidence before frontend dispatch.
- The assigned `execute-plans` owner renders only the published projection and
  owns stale-response defenses and operator-state tests.
- Reviewer `Codex` should accept this file only as a support handoff and reject
  any claim that it implements or proves Dispatch A, Dispatch B, or hosted
  readiness.

No canonical truth or primary runtime, registry, governance, BFF, or frontend
implementation is changed by this follow-up.
