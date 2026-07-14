# OCLAW-PMEM-004 BFF Handoff Follow-up 6

**Sidecar Task ID**: `OCLAW-PMEM-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-6`
**Parent Task**: `OCLAW-PMEM-004`
**Parent Owner**: `Claude2`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Antigravity`
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-07-11
**Mutates Canonical**: `no`

This support packet gives the parent owner a dependency-acceptance gate and a
bounded BFF-to-frontend handoff manifest. It does not define a canonical DTO,
approve dependency contracts, add routes, or change Pantheon or frontend
runtime code.

## 1. Dependency Acceptance Gate

The parent must inspect the accepted outputs of `OCLAW-PMEM-002` and
`OCLAW-PMEM-003` before freezing BFF field names or frontend fixtures. Task
assignment, an implementation branch, or a workspace file is not accepted
dependency evidence.

| Dependency | Evidence the parent must obtain | BFF may consume after acceptance | Must remain unavailable/unknown before acceptance |
|---|---|---|---|
| `OCLAW-PMEM-002` | Reviewer-approved runtime-profile contract and tests, including source refs and invalid-model behavior | Persona/model routing, workspace reference, sync generation, provider dependencies | Canonical routing field names, complete dependency counts, or provider usability inferred from a model ref |
| `OCLAW-PMEM-003` | Reviewer-approved canonical retrieval/materialization result contract and tests, including authorization and failure semantics | Canonical memory summaries and independently identified materialization attempt/result evidence | Latest materialization success, source-entry lineage, or canonical memory inferred from workspace/mount presence |

If either dependency is not accepted, the parent may prepare adapters and test
harnesses, but should keep the corresponding projection explicitly
`unavailable` or `unknown`. It must not copy a draft dependency schema into a
new BFF truth.

## 2. Parent-Owned Composition Boundaries

The parent BFF composition should preserve five independently observable
groups:

1. persona runtime profile from the accepted runtime-profile lane;
2. canonical persona memory from the Memory Plane retrieval facade;
3. derived workspace materialization from accepted bridge evidence;
4. provider auth/live-smoke/quota evidence from existing assistant provider
   seams;
5. reauth lifecycle plus a post-completion readiness recheck.

The following joins are parent-owned, not browser-owned:

| Join | Required invariant |
|---|---|
| Runtime profile to provider dependencies | Aggregate primary/fallback refs server-side and expose whether the inventory is complete. Partial inventory is not a definitive zero. |
| Canonical memory to materialization | Join using accepted persona, generation, and source-entry references. Materialization failure must not erase canonical entries. |
| Auth to live smoke | Keep evidence and timestamps separate. Auth-ready with missing, stale, or failed smoke is not usable. |
| Reauth to readiness | Credential-flow success enters verifying state until a fresh readiness/live-smoke result is obtained. |
| Quota to observed usage | Preserve quota source and BFF-observed usage coverage. Unknown/not-configured is neither zero nor unlimited. |

## 3. BFF-to-Frontend Handoff Manifest

Before requesting implementation in `ajoe734/execute-plans`, the parent should
publish one task-scoped manifest containing:

- BFF route and response version or fixture revision;
- source/status/reason semantics for runtime, memory, materialization, auth,
  smoke, quota, dependencies, and reauth;
- the bounded reason vocabulary actually implemented by the BFF;
- timestamps and freshness rule used for live smoke;
- dependency completeness semantics;
- allowed reauth actions for each lifecycle state;
- sanitized example fixtures for all rows in the test matrix below;
- explicit statement that browser calls go only to the BFF;
- Pantheon commit/PR and focused BFF validation commands.

The manifest belongs in Pantheon as support/contract evidence. Frontend source,
tests, and build configuration belong only in the separate `execute-plans`
repository.

## 4. Minimum Fixture Matrix

| Fixture | Required observable result |
|---|---|
| Canonical memory available and empty | Available source, zero entries, no unavailable reason. |
| Memory Plane not configured | Unavailable with stable sanitized reason; never empty-success copy. |
| Memory Plane timeout/unreachable | Unavailable with distinct bounded reason and retry-safe UI state. |
| Cross-persona private memory request | No private content or identifying metadata leakage. |
| Canonical entries with failed materialization | Entries remain visible; derived-cache failure is separate. |
| Auth ready with fresh successful smoke | Usable only if the BFF-computed state says usable. |
| Auth ready with stale/failed/missing smoke | Degraded or unknown, never usable. |
| Quota source absent | Numeric quota remains null/unknown, never zero or unlimited. |
| Dependency inventory partial | Completeness false and count non-definitive. |
| Reauth awaiting code | Code action appears only when advertised by the BFF. |
| Reauth succeeded before fresh probe | Verifying, not usable. |

Frontend component tests should use the exact accepted BFF fixtures for Codex,
Claude, and OpenClaw combinations. Browser code must not reconstruct usability
or query Memory Plane, the OpenClaw adapter, or providers directly.

## 5. Operator Journey Acceptance

### Persona detail

1. Runtime routing remains visible when canonical memory is unavailable.
2. Canonical memory and derived materialization have separate headings and
   statuses.
3. Available-empty, source-unavailable, and materialization-failed use distinct
   copy and remediation affordances.
4. Canonical IDs and generation/source references are available for authorized
   diagnosis without leaking another persona's private data.

### Provider health and reauth

1. Each provider row shows auth, smoke observation/freshness, quota provenance,
   dependency completeness, and reauth state separately.
2. The headline status renders only BFF-computed usability.
3. The UI starts, polls, and submits code only through BFF reauth routes and
   only for advertised actions.
4. Successful credential completion displays verifying until a fresh provider
   probe completes.

## 6. Parent Absorption Checklist

- [ ] Record accepted `OCLAW-PMEM-002` and `OCLAW-PMEM-003` artifact/commit
  references before DTO freeze.
- [ ] Replace the persona-memory optional-reader false-empty behavior with a
  canonical Memory Plane facade result.
- [ ] Keep runtime, canonical memory, and materialization statuses independent.
- [ ] Keep auth, smoke, quota provenance, dependency completeness, reauth, and
  usability independently testable.
- [ ] Publish the BFF-to-frontend manifest and fixtures from an implemented,
  tested BFF revision.
- [ ] Route frontend work to `ajoe734/execute-plans` and validate in strict live
  BFF mode without direct downstream calls.

## 7. Non-Claims and Handoff

This packet does not claim that either dependency is accepted, that current BFF
memory is canonical, that any proposed field name is canonical, that provider
auth proves usability, or that frontend work is implemented or deployed.
`Claude2` decides whether to absorb this support material and owns all parent
runtime/contract changes. `Antigravity` reviews only the packet's accuracy,
scope boundary, and usefulness as a non-canonical handoff.
