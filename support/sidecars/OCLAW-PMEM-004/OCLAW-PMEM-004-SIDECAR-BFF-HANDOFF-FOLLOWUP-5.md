# OCLAW-PMEM-004 BFF Handoff Follow-up 5

- **Sidecar Task ID**: `OCLAW-PMEM-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-5`
- **Parent Task**: `OCLAW-PMEM-004`
- **Parent Owner**: `Claude2`
- **Sidecar Owner**: `Codex`
- **Sidecar Reviewer**: `Antigravity`
- **Helper Kind**: `bff_handoff_packet`
- **Generated**: 2026-07-11
**Mutates Canonical**: `no`

This support-only follow-up gives the parent owner a dependency-gated delivery
sequence for the BFF and frontend surfaces. It does not approve a DTO, alter
Memory Plane or runtime-profile truth, add routes, or modify Pantheon or
`execute-plans` runtime code. The parent owner decides whether to absorb it.

## 1. Current Gate

The durable task state still records `OCLAW-PMEM-001`, `OCLAW-PMEM-002`, and
`OCLAW-PMEM-003` as `todo`. Consequently, the parent must not treat existing
files or route shapes as accepted dependency outputs. Before implementation,
the parent should obtain these accepted inputs:

| Dependency | Evidence required by this parent | Parent must not substitute |
|---|---|---|
| `OCLAW-PMEM-001` | Canonical runtime-profile fields, routing validation, provider-reference semantics, and error behavior. | Current incidental response fields or frontend model selection. |
| `OCLAW-PMEM-002` | Reconciliation result/source identity for persona agent, workspace, model, and SOUL state. | Workspace or mount presence as reconciliation success. |
| `OCLAW-PMEM-003` | Authorized canonical retrieval plus materialization attempt/result identity, generation, source IDs, and sanitized failure semantics. | The existing optional BFF reader or workspace files as canonical memory. |

If any accepted output omits a required join key, the parent should report the
specific contract gap to that dependency owner instead of inventing a local
BFF identity rule.

## 2. Implementation-Ready BFF Cut Lines

Once the dependencies are accepted, the parent can deliver two independent
BFF projections.

### Persona runtime and memory

1. Reuse the accepted runtime-profile projection and its fail-closed errors.
2. Replace the optional-reader false-empty behavior in
   `GET /bff/personas/{persona_id}/memory` with the accepted Memory Plane read
   boundary.
3. Join materialization evidence only through accepted persona, generation,
   and source-entry references.
4. Preserve independent statuses for runtime profile, canonical memory, and
   derived materialization. Failure in one must not erase evidence from the
   others.
5. Enforce persona-private authorization before projecting entry content or
   identifying metadata.

### Provider pool and reauth

1. Compose provider auth/probe evidence, quota provenance, BFF-observed usage,
   accepted runtime-profile dependencies, and reauth lifecycle server-side.
2. Return a BFF-computed usability state; auth or mounted credentials alone
   cannot make a provider usable.
3. Retain observation time and bounded sanitized reasons for missing, stale,
   or failed smoke evidence.
4. Mark dependency inventory incomplete when any accepted profile cannot be
   read; do not return a definitive zero.
5. After reauth succeeds, expose `verifying` until a fresh readiness/live-smoke
   result is available.

The exact endpoint and envelope names remain parent-owned. These cut lines are
behavioral guards, not a canonical schema declaration.

## 3. Contract Test Handoff

The parent should lock the BFF semantics before handing fixtures to the
frontend repository.

| Test | Required proof |
|---|---|
| available empty memory | Canonical source answered; status is available, count is zero, and no unavailable reason is present. |
| unavailable memory | Not configured, timeout, and unreachable cases carry stable sanitized reasons and never use empty-state success semantics. |
| private scope denial | A caller cannot observe another persona's private entry content or identifying metadata. |
| materialization failure | Canonical entries remain visible while derived materialization is independently failed or unknown. |
| runtime failure independence | Memory evidence is not rewritten merely because runtime-profile resolution fails, and vice versa. |
| false-ready provider | Auth ready plus failed, stale, or missing smoke never yields usable. |
| incomplete dependencies | Partial profile inventory returns incomplete/non-definitive dependency data. |
| unknown quota | Missing quota source leaves numeric values null/unknown; limited BFF usage is labeled with its coverage. |
| code-required reauth | Code submission is advertised only by the lifecycle state that requires it. |
| post-reauth verification | Credential-flow success remains verifying until a fresh probe completes. |

## 4. Frontend Fixture Packet

After the BFF tests establish a stable DTO, hand `ajoe734/execute-plans` one
fixture for each row above. The frontend should:

- render runtime, canonical memory, and derived materialization independently;
- distinguish valid empty memory from source unavailability;
- render auth, smoke, quota provenance, dependencies, and reauth separately;
- use only BFF-computed usability for its headline provider state;
- show code entry only when the BFF advertises it;
- show post-reauth verification rather than immediate success; and
- call only Pantheon BFF routes, never Memory Plane, OpenClaw adapter, or a
  provider directly.

Frontend implementation and tests belong in the separate `execute-plans`
repository. Hosted dev validation should use live BFF mode, strict fallback,
and safe write defaults.

## 5. Parent Absorption Sequence

- [ ] Confirm dependency tasks are accepted and record their merge/evidence
  references.
- [ ] Compare accepted join keys with the three dependency rows in section 1;
  return precise gaps to dependency owners.
- [ ] Implement and test canonical memory availability and authorization first.
- [ ] Compose materialization evidence without promoting derived cache to truth.
- [ ] Compose provider evidence and server-owned usability.
- [ ] Publish stable BFF fixtures to the frontend owner.
- [ ] Validate frontend degraded states and reauth lifecycle against those
  fixtures.
- [ ] Run hosted smoke only after the Pantheon and `execute-plans` commits are
  independently traceable.

## 6. Non-Claims and Handoff

This packet does not claim any dependency is accepted, that current BFF memory
is canonical, that provider readiness is proven, or that either runtime
repository has been implemented or deployed. It does not authorize the parent
to bypass dependency contracts. `Claude2` owns the parent implementation and
absorption decision. `Antigravity` reviews this sidecar only for accuracy,
support-only scope, and preservation of canonical boundaries.
