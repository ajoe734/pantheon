# OCLAW-PMEM-004 BFF Handoff Follow-up 7

**Sidecar Task ID**: `OCLAW-PMEM-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-7`
**Parent Task**: `OCLAW-PMEM-004`
**Parent Owner**: `Claude2`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Antigravity`
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-07-11
**Mutates Canonical**: `no`

This support-only follow-up gives the parent owner a compact absorption decision
record. It does not approve dependency work, freeze DTO names, add routes, or
change BFF, frontend, Memory Plane, runtime-profile, provider, or governance
implementation.

## 1. Absorption Decision Record

The parent owner should fill this record from merged or reviewer-approved
evidence before treating the BFF-to-frontend handoff as implementation-ready.
An assigned task, working branch, workspace file, or mounted credential is not
acceptance evidence.

| Gate | Evidence reference to record | Accept only when | Reject / defer when |
|---|---|---|---|
| Runtime-profile dependency (`OCLAW-PMEM-002`) | Approved artifact, commit/PR, and focused test command | Source refs, routing mode, primary/fallback behavior, and invalid-model failure semantics are review-approved | Draft field names or model refs are the only evidence |
| Memory/materialization dependency (`OCLAW-PMEM-003`) | Approved artifact, commit/PR, and focused test command | Canonical retrieval authorization, unavailable-vs-empty behavior, lineage, and materialization result semantics are review-approved | Workspace or mount presence is used as canonical-memory or materialization-success proof |
| Parent BFF revision | Parent commit/PR, routes, fixture revision, and focused BFF tests | Runtime, canonical memory, materialization, auth, smoke, quota, dependencies, reauth, and usability remain independently observable | One aggregate `ready` flag hides missing or failed evidence |
| Frontend handoff | Pantheon manifest/fixture path plus target `execute-plans` task | Fixtures use implemented BFF shapes and bounded reasons; browser calls only BFF | Browser must infer usability or call Memory Plane, OpenClaw adapter, or provider APIs |

Suggested decision header:

```text
Decision: absorb | absorb-with-conditions | defer
Parent revision: <commit-or-PR>
Accepted dependency refs: <OCLAW-PMEM-002 ref>, <OCLAW-PMEM-003 ref>
BFF fixture revision: <path-or-ref>
Frontend target: ajoe734/execute-plans <task-or-PR>
Residual conditions: <bounded list>
```

## 2. Minimum Parent Evidence Bundle

Before dispatching frontend implementation, the parent-owned bundle should
contain all of the following:

- exact BFF routes and implemented response/fixture revision;
- accepted dependency references for runtime profile and canonical
  memory/materialization;
- bounded `status`, `source`, and sanitized `reason` values actually emitted;
- live-smoke freshness semantics and observed timestamps;
- dependency-inventory completeness semantics;
- reauth states, advertised actions, and the post-success readiness recheck;
- examples for available-empty memory, unavailable memory, failed
  materialization, stale/failed smoke, unknown quota, partial dependencies,
  awaiting-code reauth, and succeeded-then-verifying reauth;
- focused BFF validation commands and their result;
- a statement that frontend code belongs in `ajoe734/execute-plans` and uses
  the BFF exclusively.

If any item is absent, the handoff may remain a design aid but is not a stable
frontend implementation contract.

## 3. Operator-Journey Invariants

### Persona detail

1. Runtime routing remains renderable when canonical memory is unavailable.
2. Available canonical memory with zero entries is distinct from an unreadable
   or unconfigured source.
3. Materialization is a derived-cache result; its failure does not hide
   canonical entries.
4. Authorized diagnostics retain canonical IDs and generation/source lineage
   without leaking another persona's private content or identifiers.

### Provider health and reauth

1. Auth, live smoke and freshness, quota provenance, dependency completeness,
   reauth, and BFF-computed usability render independently.
2. Auth-ready with missing, stale, or failed smoke is never presented as
   usable.
3. Unknown or unconfigured quota is not rendered as zero, unlimited, or
   healthy.
4. Code entry appears only when advertised for the active BFF reauth session.
5. Credential-flow success enters `verifying` until a fresh readiness/live
   smoke result is observed.

## 4. Reviewer Rejection Triggers

The sidecar or parent handoff should be returned for correction if it:

- treats task assignment, draft code, or unreviewed schemas as accepted
  dependency evidence;
- claims canonical memory from an optional-reader empty list, workspace file,
  or mount;
- derives provider usability from auth, reauth completion, quota, or model
  selection alone;
- omits source-unavailable versus available-empty fixtures;
- asks the Pantheon repository to contain `execute-plans` frontend source;
- claims implementation, deployment, or dependency completion without the
  corresponding reviewed evidence.

## 5. Handoff

`Claude2` owns the absorption decision and all parent runtime/contract changes.
`Antigravity` reviews this packet only for support-slice accuracy, boundary
discipline, and usefulness. The parent should compose it with accepted
`OCLAW-PMEM-002` and `OCLAW-PMEM-003` outputs and the earlier OCLAW-PMEM-004
handoff packets; this document itself is not canonical truth or a frontend
implementation contract.
