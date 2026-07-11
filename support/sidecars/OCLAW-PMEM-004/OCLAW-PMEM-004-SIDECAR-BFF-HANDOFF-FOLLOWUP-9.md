# OCLAW-PMEM-004 BFF Handoff Follow-up 9

**Sidecar Task ID**: `OCLAW-PMEM-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-9`  
**Parent Task**: `OCLAW-PMEM-004`  
**Parent Owner**: `Claude2`  
**Sidecar Owner**: `Codex`  
**Sidecar Reviewer**: `Antigravity`  
**Helper Kind**: `bff_handoff_packet`  
**Generated**: 2026-07-11  
**Mutates Canonical**: `no`

This support-only packet is the last-mile contract-freeze checklist for the
parent BFF owner and the later `execute-plans` implementer. It does not freeze
a DTO, approve dependencies, change a route, create canonical truth, or claim
that BFF or frontend delivery is ready.

## 1. Current Decision

**Frontend dispatch: `defer`.** The repository evidence inspected for this
packet establishes the intended boundary and existing integration points, but
does not establish reviewed dependency revisions, an implemented aggregate
DTO revision, a matching fixture manifest, or a frontend task pinned to those
revisions. The parent owner may change this decision only by filling every
evidence cell in section 2 with reviewed, versioned artifacts.

## 2. Parent Contract-Freeze Record

The parent should copy and complete this table in its implementation or review
artifact. A task assignment, proposed JSON block, branch name, workspace file,
mount, or unreviewed diff is not implementation evidence.

| Freeze item | Evidence required | Accepted reference |
|---|---|---|
| Runtime profile authority | Reviewed `OCLAW-PMEM-002` artifact/revision defining routing refs, provider relations, source refs, and invalid-model behavior. | `<required>` |
| Memory authority | Reviewed `OCLAW-PMEM-003` artifact/revision defining authorized canonical retrieval, scope enforcement, and materialization evidence. | `<required>` |
| Persona memory DTO | Implemented route revision and tests distinguishing available-empty from unavailable, preserving canonical IDs/scope, and separating materialization result. | `<required>` |
| Provider pool DTO | Implemented route revision and tests separating auth, fresh live smoke, quota provenance, dependency completeness, usability, and reauth. | `<required>` |
| Status/reason vocabulary | Bounded values implemented in schema/tests, including unknown, stale, unavailable, failed, and partial/incomplete states where applicable. | `<required>` |
| Freshness policy | Implemented rule/test specifying when a live smoke is fresh enough to support `usable`. | `<required>` |
| Reauth actions | Implemented role/MFA-gated start/status/code flow, advertised next actions, redaction, and post-success verification rule. | `<required>` |
| Fixture manifest | Versioned sanitized fixtures matching the implemented responses and degradation vocabulary. | `<required>` |
| Frontend dispatch | `ajoe734/execute-plans` task/PR pinned to the BFF and fixture revisions. | `<required>` |

Any `<required>` cell left blank keeps the decision at `defer`.

## 3. Freeze Invariants

The implemented BFF contract is suitable for frontend handoff only if all of
these remain server-owned and testable:

1. `usability` is computed by the BFF; browser code does not derive it from a
   credential mount, auth state, quota, or selected model.
2. Auth-ready without a fresh passing live smoke is not usable.
3. Unknown quota is not serialized or rendered as zero, unlimited, or healthy.
4. An incomplete persona-profile inventory does not produce a definitive zero
   dependency count.
5. Canonical memory retrieval and derived OpenClaw materialization have
   independent statuses and reasons.
6. Available canonical memory with zero entries differs from a missing,
   unauthorized, timed-out, or unreachable source.
7. Workspace or mount presence never proves canonical retrieval,
   materialization success, provider auth, or provider usability.
8. Reauth success enters `verifying`; only a subsequent fresh readiness/live
   smoke observation may change usability.
9. Code entry is offered only for the active opaque session when the BFF
   advertises that action; secrets and provider tokens never enter the DTO.
10. Private persona memory is filtered or denied server-side before an
    operator-safe response is built.

## 4. Minimal Fixture Matrix

The parent fixture revision must cover these cross-surface combinations, not
only isolated happy paths:

| Fixture | Required observable result |
|---|---|
| Runtime valid; memory available-empty | Runtime remains visible; memory shows a genuine empty state with source evidence. |
| Runtime valid; Memory Plane unreachable | Runtime remains visible; memory is unavailable with a bounded reason. |
| Canonical memory available; materialization failed | Canonical entries remain visible; cache/materialization failure is separate. |
| Auth ready; smoke missing or stale | Provider is unknown/degraded, never usable. |
| Auth ready; smoke failed; quota known | Failure remains visible; quota data does not override usability. |
| Auth unavailable; dependency inventory partial | Neither auth nor dependency count is presented as healthy/zero. |
| Reauth awaiting code | Only the advertised code action is enabled for the opaque session. |
| Reauth succeeded; new probe pending | State is verifying, not usable. |
| Codex, Claude, OpenClaw mixed degradation | Each provider row preserves its own evidence and reason without pool-wide flattening. |

Every fixture should identify the route/DTO revision, expected bounded reason,
allowed UI actions, and whether its observation is fresh or stale.

## 5. Frontend Dispatch Capsule

Once section 2 is complete, the parent can hand off this bounded instruction:

```text
Implement against Pantheon BFF revision <ref> and fixture revision <ref> in
ajoe734/execute-plans. Use Pantheon BFF routes only. Render BFF-computed
usability and preserve independent runtime, canonical-memory,
materialization, auth, live-smoke/freshness, quota-provenance,
dependency-completeness, and reauth states. Do not call Memory Plane,
OpenClaw adapter, or provider APIs from the browser. Validate in strict live
BFF mode using the accepted degraded fixtures before hosted smoke.
```

Frontend acceptance must prove different copy/actions for available-empty and
unavailable memory, materialization failure without hiding canonical entries,
unknown quota, incomplete dependencies, stale/failed smoke, awaiting-code,
and post-reauth verifying. Frontend source belongs only in `execute-plans`.

## 6. Absorption and Non-Claims

The parent absorption note should record `absorb`, `absorb-with-conditions`, or
`defer`; dependency/BFF/fixture revisions; the frontend task/PR if dispatched;
and every residual condition. A condition affecting authority, authorization,
field meaning, freshness, completeness, actions, or fixture fidelity blocks a
`ready` dispatch.

This packet does not claim `OCLAW-PMEM-002` or `OCLAW-PMEM-003` is accepted,
the current persona-memory route reads canonical Memory Plane, a provider-pool
aggregate or fixture manifest has been implemented, or frontend work is built
or deployed. `Claude2` owns absorption and contract freeze. `Antigravity`
reviews only this support artifact's accuracy, scope, and usefulness; approval
does not promote it into canonical contract truth.
