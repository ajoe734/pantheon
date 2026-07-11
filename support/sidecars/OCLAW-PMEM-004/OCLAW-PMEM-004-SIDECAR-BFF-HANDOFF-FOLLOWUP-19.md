# OCLAW-PMEM-004 BFF Handoff Follow-up 19

**Sidecar Task ID**: `OCLAW-PMEM-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-19`  
**Parent Task**: `OCLAW-PMEM-004`  
**Parent Owner**: `Claude2`  
**Sidecar Owner**: `Codex`  
**Sidecar Reviewer**: `Antigravity`  
**Helper Kind**: `bff_handoff_packet`  
**Generated**: 2026-07-11  
**Mutates Canonical**: `no`

This support-only packet gives the parent and reviewer a composition-intake
gate for BFF and frontend handoff evidence. It does not accept dependency work,
define canonical DTOs, modify runtime/BFF/frontend code, or authorize frontend
dispatch.

## 1. Current Disposition

**Frontend dispatch remains `defer`.** Durable task state records parent
`OCLAW-PMEM-004` as `todo`, dependent on `OCLAW-PMEM-002` and
`OCLAW-PMEM-003`. The parent must not treat route presence, a workspace diff,
a mutable branch, a proposed fixture, or another sidecar packet as evidence
that either dependency has been accepted and composed.

This gate may change only when the intake bundle below is complete and the
reviewer can reproduce its claims against immutable revisions.

## 2. Composition Intake Bundle

The parent should submit one bundle containing all fields below. Missing,
mutable, or mutually inconsistent references make the bundle inadmissible.

```text
Accepted OCLAW-PMEM-002 revision: <immutable reviewed commit>
Accepted OCLAW-PMEM-003 revision: <immutable reviewed commit>
Composed Pantheon BFF revision: <immutable commit containing both>
Fixture manifest revision: <immutable commit>
Compatibility boundary: <none, or explicit version rule plus proof>
Focused commands and results: <exact reproducible commands>
Changed response fields/reasons/actions: <complete bounded list>
Known unresolved conditions: <none, or list retaining defer>
Proposed execute-plans task: <not-dispatched, or pinned task after approval>
```

All behavioral assertions should resolve to the same composed BFF revision and
matching fixture revision. If the bundle uses different revisions, it must
state a compatibility boundary and include a focused test that crosses it.

## 3. Reviewer Intake Checklist

| Gate | Minimum reproducible proof | Reject when |
|---|---|---|
| Dependency identity | Reviewed immutable commits for `OCLAW-PMEM-002` and `OCLAW-PMEM-003` | Only task IDs, branch names, route listings, or prose are supplied |
| Composition ancestry | Composed BFF commit contains both accepted revisions, or records a tested compatibility boundary | One dependency is absent, unreviewed, or represented only by an uncommitted diff |
| Persona/profile join | Fixture and test preserve persona identity plus profile generation and fail closed on mismatch | Browser or BFF silently joins stale/mismatched generations |
| Canonical memory | Tests distinguish available-with-items, available-empty, unauthorized, malformed, timeout, unreachable, and unconfigured | Failure or denial becomes an authoritative empty list |
| Isolation | Cross-persona denial test proves no private content, IDs, counts, or identifying metadata leak | A denial payload reveals protected identity or inventory information |
| Materialization | Attempt/result identity, source-memory generation, observation time, and failure remain separate from canonical memory | Workspace/cache presence is used as proof of materialization success |
| Provider usability | BFF-computed result requires auth, fresh passing live smoke, and complete dependency inventory | Mount, auth, quota, or reauth completion alone implies usable |
| Quota provenance | Source, coverage, window, observed time, freshness, and unknown values survive the projection | Unknown/stale quota is rendered as zero, unlimited, or healthy |
| Reauth safety | Role/MFA, opaque session, advertised actions, expiry, and post-success verification have focused tests | UI invents actions or changes directly from credential success to usable |
| Fixture fidelity | Revision-locked fixtures enumerate bounded statuses, reasons, actions, timestamps, identities, and completeness | Fixtures use values the composed BFF cannot emit |

One failed or unproven row retains `defer`; a partial bundle is not
`absorb-with-conditions` when the condition affects authority, authorization,
identity, freshness, completeness, action safety, or fixture fidelity.

## 4. Minimum Revision-Locked Fixture Set

The frontend handoff should contain at least these independent scenarios:

1. Valid runtime profile with canonical memory unavailable for a bounded
   reason; runtime fields remain visible.
2. Authorized available-empty canonical memory, distinct from timeout,
   unauthorized, malformed, unreachable, and unconfigured responses.
3. Canonical memory available while the latest materialization attempt fails;
   entries remain visible and cache lineage remains separate.
4. Persona/profile generation mismatch and cross-persona denial, both failing
   closed without protected metadata leakage.
5. Mixed Codex, Claude, and OpenClaw rows where auth, smoke, dependency
   completeness, quota, and usability differ without pool-wide flattening.
6. Known quota with failed smoke, plus unknown and stale quota without numeric
   substitution.
7. Reauth awaiting code, verifying, fresh-probe success, fresh-probe failure,
   expiry, and disallowed-action responses.
8. One child source failing while sibling evidence remains independently
   visible and is neither erased nor promoted.

Each fixture records its route, request identity, expected HTTP status,
response envelope, relevant identities/generations, observation/freshness
inputs, and expected operator-visible result. Sanitized fixtures must preserve
semantics without containing credentials, provider tokens, or private memory.

## 5. Operator Journey Acceptance

Using BFF responses only, the eventual frontend must be able to show:

1. Persona route/profile identity separately from canonical memory and derived
   materialization.
2. Precise unavailable or denied states instead of reassuring empty states.
3. Provider auth, live smoke, quota provenance, dependent-persona completeness,
   and reauth lifecycle as independent evidence.
4. Only actions advertised by the BFF for the active opaque reauth session.
5. `verifying` after credential success, followed by usability only when the
   BFF reports a fresh passing probe and complete dependencies.

The browser must call Pantheon BFF routes only. It must not call Memory Plane,
the OpenClaw adapter, or provider APIs directly, and it must not compute its own
provider usability from child fields.

## 6. Reviewer Verdict Record

```text
Verdict: accept-for-parent-composition | request-changes
Reviewed dependency revisions: <refs>
Reviewed composed BFF revision: <ref>
Reviewed fixture revision: <ref>
Commands rerun and results: <exact commands/results>
Checklist rows passed: <list>
Rejected or unresolved rows: <none or list>
Frontend dispatch disposition: ready | defer
Reason: <bounded explanation>
```

`accept-for-parent-composition` means only that this intake bundle is suitable
for parent `Claude2` to absorb. It does not approve `OCLAW-PMEM-004`, dependency
tasks, an `execute-plans` implementation, deployment, or hosted behavior.

## 7. Ownership Boundary

Parent owner `Claude2` owns dependency acceptance, canonical field names, BFF
composition, fixtures, and any frontend dispatch. Reviewer `Antigravity`
reviews this artifact only for current-state accuracy, support-only scope, and
handoff usefulness. Frontend work, when authorized, belongs in
`ajoe734/execute-plans`, never under the Pantheon checkout.
