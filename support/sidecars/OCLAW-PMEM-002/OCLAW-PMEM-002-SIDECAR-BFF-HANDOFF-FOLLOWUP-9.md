# OCLAW-PMEM-002 BFF / Frontend Handoff Follow-up 9

Status: support-only contract-freeze proposal; not canonical truth or runtime proof
Parent task: `OCLAW-PMEM-002`
Sidecar task: `OCLAW-PMEM-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-9`
Owner: Codex2
Reviewer: Codex

## Purpose And Boundary

This packet makes the BFF-first assignment from follow-up 8 concrete enough
for a parent-selected BFF owner and a later `execute-plans` owner to compose
without inventing readiness semantics independently. It changes no reconciler,
BFF, frontend, Persona Registry, Memory Plane, provider policy, governance, or
canonical contract. All route names, write ownership, schema publication, and
adoption decisions remain with the parent owner.

The audited baseline is `origin/dev` at `970566a81`. The parent branch
`origin/task/OCLAW-PMEM-002` remains at `db8e7ca0f`, the evidence-only anchor
audited in follow-up 7. On the audited baseline, persona creation and update
write desired reconcile metadata and
`GET /bff/personas/{persona_id}/runtime-profile` recomputes desired runtime
inputs. No persona-scoped BFF route was found that reads an authoritative
reconcile attempt/result and projects current observed readiness. Therefore the
frontend dependency identified in follow-up 8 remains open.

## Proposed Projection Minimum

The parent-selected contract may use different field or route names, but it
must preserve these independently sourced facts:

| Truth group | Minimum information | Must not be inferred from |
|---|---|---|
| Request | attempt id, persona id, requested generation, requested time | agent existence or aggregate sync membership |
| Consumer | acknowledged time and lifecycle | desired metadata alone |
| Desired | identity, workspace, model, generation | observed readback |
| Observed | identity, workspace, model, generation, observation time | desired runtime profile |
| Probe | safe result code, probe time, evidence reference | provider mount/auth readiness |
| Outcome | lifecycle, stable reason code, retryable, safe repair action, completion time | raw CLI output or exception text |
| History | prior successful generation and observation, explicitly labelled previous | current-generation readiness |
| Snapshot | server snapshot time and contract/schema version | client receive time |

Provider readiness, canonical memory health, and memory materialization health
must remain separate referenced truth. None may promote agent reconcile state.

## Lifecycle And Fail-Closed Matrix

The chosen schema should publish an enum or equivalent versioned mapping. The
frontend may enable conversation only for the `ready` row below.

| Server state | Required server evidence | Operator behavior |
|---|---|---|
| `queued` | durable request exists; no acknowledgement | show queued; disable conversation |
| `reconciling` | durable consumer acknowledgement exists | show progress; disable conversation |
| `ready` | current desired generation equals observed generation and a successful sanitized current-generation probe is recorded | show ready; conversation may be enabled |
| `drifted` | desired and observed facts differ | show both values; disable conversation |
| `blocked` | stable policy/dependency reason prevents progress | show safe reason/repair action; disable conversation |
| `failed` | terminal attempt outcome with safe reason | show failure; disable conversation; gate Retry by `retryable` |
| `unavailable` | result owner/read path unavailable or payload cannot be trusted | show unavailable; disable conversation |
| unknown value | serialization or client mapping cannot recognize lifecycle | fail closed as unavailable; never treat as ready |

A previous successful generation is history only. It must never fill missing
current observed/probe evidence or keep conversation enabled during a newer
queued, reconciling, blocked, failed, drifted, or unavailable attempt.

## BFF Owner Assignment

**Repository:** `ajoe734/pantheon`
**Merge target:** `dev`
**Composition:** parent `OCLAW-PMEM-002` owner selects the authoritative
attempt/result write owner and the governed projection route.

The BFF slice should deliver:

1. authoritative durable attempt/result reads, not a projection reconstructed
   from `metadata.openclaw_agent_reconcile` or `PersonaRuntimeProfile`;
2. persona and generation scoped ordering so late generation N completion
   cannot replace N+1 truth;
3. the minimum projection and lifecycle behavior above;
4. safe 404/authorization behavior and unavailable semantics for result-owner
   failure or malformed/unknown lifecycle;
5. response redaction that excludes credentials, tokens, provider payloads,
   raw stdout/stderr, command lines, and exception text; and
6. contract tests covering duplicate delivery, acknowledgement ordering,
   current-generation probe gating, late completion, prior-success history,
   owner outage, unknown lifecycle, and redaction.

The BFF-to-frontend handoff is incomplete unless it records the chosen write
owner, route, schema/enum version, focused test command, PR number, merge SHA,
and one sanitized representative response.

## Conditional Frontend Assignment

**Repository:** `ajoe734/execute-plans`
**Merge target:** `main`
**Start condition:** the Pantheon projection is merged and its versioned
contract or sanitized response is available.

The frontend owner must consume only the governed BFF projection. It must:

- keep persona mutation success separate from agent reconcile readiness;
- enable conversation only for current-generation observed `ready` with probe
  evidence;
- render desired and observed facts together for drift/convergence;
- label any prior success as a previous generation;
- order responses by generation first and server observation/snapshot time
  second, discarding stale regressions;
- fail closed on missing fields, unavailable state, or unknown lifecycle;
- show Retry only for `retryable=true` and send a fresh server-compatible
  idempotency key; and
- navigate only server-owned repair actions, never directly mutate provider,
  registry, memory, or OpenClaw configuration.

Frontend tests should cover all lifecycle rows, unknown/malformed data, late
response ordering, failed-new-generation history, Retry gating, and accessible
non-ready messaging.

## Composition Receipt Required From Parent

Before the parent claims this handoff was absorbed, it should publish one
receipt containing:

- adopted route and schema/enum version;
- authoritative write/read owner and idempotency key rule;
- Pantheon PR and merge SHA plus focused contract-test result;
- `execute-plans` PR and merge SHA plus state-machine test result;
- deployed BFF and frontend SHA ancestry;
- sanitized current-generation projection and successful
  `model=openclaw/{persona_id}` response tied to that generation; and
- explicit residual risks for provider, canonical memory, or materialization
  conditions not proven by the reconcile projection.

Local mocks, desired metadata, runtime-profile readiness, provider readiness,
agent existence, aggregate sync membership, or an unmerged anchor are not a
composition receipt or hosted proof.

## Reviewer Checklist

- [ ] Only this support artifact and task-scoped coordination records changed.
- [ ] No route name or implementation owner is falsely presented as canonical.
- [ ] Desired, observed, probe, provider, canonical-memory, and materialization
      truth remain separate.
- [ ] Every non-ready, unavailable, or unknown condition fails closed in the
      operator journey.
- [ ] The parent adoption receipt requires merged and deployed cross-repository
      identities plus a current-generation hosted response.

Reviewer `Codex` should approve this packet only as a support handoff. Approval
does not prove or implement the parent BFF slice, frontend slice, provider
availability, memory materialization, or hosted OpenClaw readiness.
