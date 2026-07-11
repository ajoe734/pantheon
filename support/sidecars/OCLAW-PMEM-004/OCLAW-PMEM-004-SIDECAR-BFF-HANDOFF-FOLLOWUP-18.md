# OCLAW-PMEM-004 BFF Handoff Follow-up 18

**Sidecar Task ID**: `OCLAW-PMEM-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-18`  
**Parent Task**: `OCLAW-PMEM-004`  
**Parent Owner**: `Claude2`  
**Sidecar Owner**: `Codex`  
**Sidecar Reviewer**: `Antigravity`  
**Helper Kind**: `bff_handoff_packet`  
**Generated**: 2026-07-11  
**Mutates Canonical**: `no`

This support-only packet adds a delta and absorption ledger to the BFF and
frontend handoff. It does not define canonical DTOs, accept dependency work,
change runtime/BFF/frontend code, or authorize frontend dispatch.

## 1. Dispatch Gate

**Decision: `defer`.** Durable state records `OCLAW-PMEM-004`,
`OCLAW-PMEM-002`, and `OCLAW-PMEM-003` as `todo`. The parent depends on the
latter two. A repeated sidecar, route already present in source, mutable branch,
workspace file, or proposed fixture does not satisfy that dependency gate.

The parent may reconsider only after immutable reviewed dependency revisions,
their composed BFF revision, and revision-locked fixtures are available.

## 2. Delta Ledger

Before absorbing any new handoff claim, compare it with the last accepted
parent evidence and complete every column. `None` or a mutable reference keeps
the row unresolved.

| Surface | Previous immutable ref | Candidate immutable ref | Observable schema/behavior delta | Focused proof | Parent disposition |
|---|---|---|---|---|---|
| Persona runtime/profile join (`OCLAW-PMEM-002`) | `<required>` | `<required>` | `<required>` | `<exact command/result>` | `<absorb/defer>` |
| Canonical memory read/auth (`OCLAW-PMEM-003`) | `<required>` | `<required>` | `<required>` | `<exact command/result>` | `<absorb/defer>` |
| Materialization lineage (`OCLAW-PMEM-003`) | `<required>` | `<required>` | `<required>` | `<exact command/result>` | `<absorb/defer>` |
| Provider auth/live smoke/dependencies | `<required>` | `<required>` | `<required>` | `<exact command/result>` | `<absorb/defer>` |
| Quota provenance/freshness | `<required>` | `<required>` | `<required>` | `<exact command/result>` | `<absorb/defer>` |
| Reauth session/actions/verification | `<required>` | `<required>` | `<required>` | `<exact command/result>` | `<absorb/defer>` |
| Composed BFF DTOs | `<required>` | `<required>` | `<required>` | `<exact command/result>` | `<absorb/defer>` |
| Frontend fixtures | `<required>` | `<required>` | `<required>` | `<exact command/result>` | `<absorb/defer>` |

An unchanged row should say `no delta` and retain its prior disposition. Do not
replace missing evidence with a newer sidecar number.

## 3. Composition Checks

For a candidate composed revision, the parent must record pass/fail for these
cross-surface checks:

1. Runtime identity and profile generation remain visible when memory is
   unavailable; generation mismatch fails closed.
2. Authorized available-empty memory differs from unauthorized, malformed,
   timeout, unreachable, and unconfigured states.
3. Cross-persona denial leaks no private content, IDs, counts, or identifying
   metadata.
4. Canonical memory and derived materialization have independent status,
   identity, generation, lineage, observation time, and bounded failure.
5. Provider usability requires BFF-computed auth, fresh passing live smoke,
   and complete dependencies; quota never substitutes for usability.
6. Quota retains source, coverage, window, observation time, freshness, and
   unknown semantics without invented numeric values.
7. Reauth exposes only BFF-advertised actions for an opaque active session;
   credential success enters verification and a fresh probe determines the
   resulting usability.
8. Failure or staleness of one child neither erases nor validates another.

Each check must cite the same composed BFF revision and matching fixture
revision, or document and test an explicit compatibility boundary.

## 4. Operator Journey Fixture Matrix

Revision-locked fixtures should cover at least:

| Journey state | Required operator-visible result |
|---|---|
| Runtime valid; memory unavailable | Runtime remains usable and memory shows a precise bounded reason. |
| Memory available-empty | Source answered successfully, count is zero, and no unavailable reason is shown. |
| Memory available; materialization failed | Canonical entries remain visible; failed attempt/result lineage is separate. |
| Cross-persona request denied | No private payload or identifying metadata is rendered. |
| Auth valid; smoke failed/stale | Provider is not usable; auth and smoke remain independent. |
| Quota known; smoke failed | Numeric quota remains informational and does not imply readiness. |
| Quota unknown/stale | No zero or synthetic percentage is substituted. |
| Dependencies incomplete | Missing/unknown dependencies remain explicit; provider is not usable. |
| Reauth awaiting code | Code entry appears only when advertised for the active session. |
| Reauth verifying/success/failure/expired | State and permitted actions come from BFF; only a fresh probe can establish usability. |
| Mixed Codex/Claude/OpenClaw degradation | Rows retain independent evidence; no pool-wide status flattening occurs. |

## 5. Frontend Handoff Release Record

Only after all composition checks pass may the parent fill and release this
record:

```text
Decision: ready | defer
Accepted OCLAW-PMEM-002 ref: <immutable ref>
Accepted OCLAW-PMEM-003 ref: <immutable ref>
Composed Pantheon BFF ref: <immutable ref>
Fixture revision: <immutable ref>
Delta-ledger rows absorbed: <list>
Focused verification: <exact commands and results>
execute-plans task/PR: <pinned ref or not-dispatched>
Unresolved rows or conditions: <none or list retaining defer>
```

If released, the frontend task belongs in `ajoe734/execute-plans`, calls
Pantheon BFF routes only, uses BFF-computed usability and advertised actions,
and pins both the composed BFF and fixture revisions. Frontend source or build
configuration must not be added beneath Pantheon. Browser code must not call
Memory Plane, the OpenClaw adapter, or provider APIs directly.

## 6. Ownership Boundary

Parent owner `Claude2` decides absorption, canonical field names, BFF
implementation, and frontend dispatch. Reviewer `Antigravity` reviews this
artifact only for current-state accuracy, support-only scope, and handoff
usefulness. Approval of this sidecar does not approve the parent, its
dependencies, the frontend implementation, or deployment readiness.
