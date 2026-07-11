# OCLAW-PMEM-004 BFF Handoff Follow-up 8

**Sidecar Task ID**: `OCLAW-PMEM-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-8`  
**Parent Task**: `OCLAW-PMEM-004`  
**Parent Owner**: `Claude2`  
**Sidecar Owner**: `Codex`  
**Sidecar Reviewer**: `Antigravity`  
**Helper Kind**: `bff_handoff_packet`  
**Generated**: 2026-07-11  
**Mutates Canonical**: `no`

This support-only packet gives the parent owner a fillable frontend dispatch
payload and a final fail-closed check. It does not approve dependencies,
freeze DTO or route names, add fixtures, or change Pantheon, `execute-plans`,
Memory Plane, OpenClaw, provider, runtime, registry, or governance code.

## 1. Dispatch Readiness Decision

The parent should dispatch frontend implementation only when every required
reference below points to reviewed, implemented evidence. A blank, draft, task
assignment, branch name, workspace file, mount, or proposed JSON example is not
a valid reference.

| Required reference | Ready when | Fail closed when |
|---|---|---|
| `OCLAW-PMEM-002` acceptance | Reviewer-approved artifact and revision define runtime routing, source refs, provider dependencies, and invalid-model behavior. | Dependency remains draft or its inventory completeness cannot be represented. |
| `OCLAW-PMEM-003` acceptance | Reviewer-approved artifact and revision define authorized canonical retrieval and independent materialization results. | Workspace/mount presence is the only memory or materialization evidence. |
| Parent BFF revision | Merged/reviewed revision implements the DTO and passes focused fixtures. | Handoff contains only suggested fields or examples. |
| Fixture revision | Versioned fixtures cover all degraded cases in section 3 and match the implemented BFF response. | Frontend must invent missing states, reasons, joins, or readiness rules. |
| Frontend target | An `ajoe734/execute-plans` task names the BFF revision and fixture revision. | Frontend source is requested in Pantheon or direct downstream calls are required. |

The decision is `defer` if any row fails closed. The parent may continue BFF
work, but must not describe the browser contract as implementation-ready.

## 2. Fillable Frontend Dispatch Payload

The parent owner can copy this block only after replacing every placeholder
with accepted evidence:

```text
Dispatch decision: ready | defer
Parent task: OCLAW-PMEM-004
Pantheon BFF revision/PR: <merged-or-review-approved-ref>
Accepted runtime-profile ref: <OCLAW-PMEM-002-artifact-and-revision>
Accepted memory/materialization ref: <OCLAW-PMEM-003-artifact-and-revision>
BFF routes and response revision: <implemented-route-list-and-version>
Fixture manifest: <Pantheon-path-and-revision>
Bounded status/reason vocabulary: <implemented-contract-or-fixture-ref>
Live-smoke freshness rule: <implemented-rule-and-test-ref>
Dependency completeness rule: <implemented-rule-and-test-ref>
Reauth lifecycle/actions: <implemented-rule-and-test-ref>
Frontend repository: ajoe734/execute-plans
Frontend task/branch: <task-id-or-PR>
BFF validation: <exact-commands-and-results>
Residual conditions: <none-or-bounded-list>
```

The dispatch message must say that browser code:

- calls the Pantheon BFF only;
- renders BFF-computed provider usability rather than recomputing it;
- keeps runtime, canonical memory, and derived materialization independent;
- keeps auth, live smoke/freshness, quota provenance, dependency completeness,
  and reauth independently visible;
- treats unknown, unavailable, stale, and failed as real states, not empty or
  healthy defaults.

## 3. Fixture Manifest Completeness

The referenced fixture manifest is complete only if it provides sanitized,
implemented examples for all of these cases:

| Surface | Required cases |
|---|---|
| Persona memory | Available with entries; available empty; not configured; timeout/unreachable; private cross-persona denial. |
| Materialization | Succeeded; absent/unknown; failed while canonical entries remain available. |
| Runtime profile | Valid primary/fallback route; invalid model reference; source unavailable if supported by the accepted contract. |
| Provider health | Auth ready plus fresh passing smoke; auth ready plus missing/stale/failed smoke; auth unavailable. |
| Usage and dependencies | Provider quota known; quota source unknown/not configured; complete dependencies; partial inventory with non-definitive count. |
| Reauth | Idle/startable; awaiting code with advertised code action; terminal failure; success followed by verifying until fresh probe. |

For each fixture, the manifest should identify the implemented route/response
revision and expected operator copy or affordance. It must not silently expand
the BFF's bounded reasons with frontend-only states.

## 4. Frontend Acceptance Handoff

The `execute-plans` task should require component or integration coverage for:

1. runtime routing remaining visible when canonical memory is unavailable;
2. available-empty memory differing from source-unavailable memory;
3. materialization failure not hiding canonical memory;
4. auth-ready plus stale, missing, or failed smoke never appearing usable;
5. unknown quota never appearing as zero, unlimited, or healthy;
6. partial dependency inventory never appearing as zero dependents;
7. code entry appearing only when the active BFF session advertises it;
8. successful reauth remaining `verifying` until a fresh readiness/live-smoke
   observation arrives;
9. Codex, Claude, and OpenClaw degraded combinations using the accepted BFF
   fixtures;
10. strict live-BFF mode with no Memory Plane, OpenClaw adapter, or provider
    request from the browser.

Hosted validation belongs to the parent/frontend delivery flow and must use the
Pantheon-owned frontend host and strict BFF settings described by repository
operations guidance. This sidecar does not claim a dev deployment.

## 5. Parent Absorption Record

When absorbing this packet, the parent should record:

```text
Decision: absorb | absorb-with-conditions | defer
Evidence refs: <dependency, BFF, fixture, and validation refs>
Frontend dispatch: <execute-plans task/PR or not dispatched>
Rejected placeholders: <none or list>
Residual conditions: <none or bounded list>
```

`absorb-with-conditions` must not be translated into frontend readiness when a
condition affects field meaning, source authority, authorization, freshness,
completeness, reauth actions, or fixture fidelity.

## 6. Non-Claims and Handoff

This packet does not claim `OCLAW-PMEM-002` or `OCLAW-PMEM-003` is accepted,
the current persona-memory response is canonical, a BFF DTO or fixture exists,
provider auth proves usability, or frontend work is implemented or deployed.
`Claude2` owns the parent absorption and dispatch decision. `Antigravity`
reviews this support artifact only for accuracy, bounded scope, and handoff
usefulness; review does not promote it into canonical contract truth.
