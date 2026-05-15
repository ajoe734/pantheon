# SD-FND-001 Acceptance and Dependency Map (Sidecar)

**Parent Task**: `SD-FND-001` - Materialize canonical foundation package boundary
**Parent Owner**: `Codex`
**Parent Reviewer**: `Codex2`
**Parent Status**: `review`
**Sidecar Task**: `SD-FND-001-SIDECAR-ACCEPTANCE`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Codex2`
**Helper Kind**: `acceptance_packet`
**Generated**: `2026-04-27`
**Mutates canonical**: `no`

> This is a support artifact only. It does not modify L1 canonical truth, core
> contract truth, runtime / registry / governance implementation, or the parent
> execution record. It packages a reviewer-facing acceptance checklist,
> dependency map, verification evidence, and handoff notes for `SD-FND-001`.

## 1. Executive Summary

`SD-FND-001` is in `review` with `Codex` as owner and `Codex2` as reviewer.
The parent implementation materializes the shared SD-00 / SD-12 foundation
package boundary at `services/foundation` and keeps it side-effect-free so it
can be imported by BFF, runtime-manager, telemetry, governance, and tests
without opening network, database, broker, or framework clients.

The parent handoff in
`docs/reviews/2026-04-27-sd-fnd-001-codex-handoff.md` was created before the
reviewer was reassigned from `Claude` to `Codex2`; `ai-status.json` is the
durable routing truth and now assigns the parent review to `Codex2`.

This sidecar summarizes the current acceptance evidence and makes the task
split explicit: `SD-FND-001` owns the package boundary and primitive tests;
`SD-FND-002` owns BFF/runtime-manager command-path adoption; `SD-FND-003` owns
durable outbox, DLQ, schema-registry, replay, and persistence primitives.

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Durable task board for parent / sidecar status, owner, reviewer, acceptance, and artifact paths |
| `.orchestrator/task-briefs/sd_fnd_001_sidecar_acceptance.md` | Confirms this helper is support-only and must hand off to `Codex2` |
| `docs/reviews/2026-04-27-sd-materializable-execution-task-packet.md` | Defines the SD foundation gap, task split, and acceptance shape |
| `docs/reviews/2026-04-27-sd-fnd-001-codex-handoff.md` | Parent owner handoff claiming the implemented boundary and verification command |
| `docs/02-architecture/consensus/sessions/phase7-2026-04-18-ep4-ep5-execution-proof/planning-session.json` | Shows the underlying EP4 / EP5 planning session is accepted and human-gate approved |
| `services/foundation/README.md` | Documents the canonical singular package path, side-effect-free boundary, and out-of-scope adoption / persistence paths |
| `services/foundation/__init__.py` | Defines the public exports for shared foundation primitives |
| `services/foundation/tests/test_primitives.py` | Covers trace, command, error, idempotency, policy, audit, secret-ref, and serialization behavior |

## 3. Parent Acceptance Checklist

| Parent acceptance target | Evidence to review | Status now |
|---|---|---|
| Foundation package path is chosen and documented | `services/foundation/README.md` normalizes on singular `services/foundation` and notes SD-12's draft `services/foundations` wording | PASS |
| Package remains side-effect-free | README boundary states imports must not start network clients, database connections, broker clients, or framework runtimes | PASS |
| `TraceContext` exists with trace / correlation / actor / environment refs | Public export in `__init__.py`; `test_trace_context_serializes_required_boundary_fields` covers child trace shape and required fields | PASS |
| `CommandEnvelope` exists with actor, authority scope, trace, and idempotency | Public export in `__init__.py`; command tests cover stable shape and trace/idempotency mismatch rejection | PASS |
| `ErrorEnvelope` covers validation and policy-denial shapes | Public export in `__init__.py`; tests assert validation status `422`, policy denial status `403`, decision ref, and trace ref | PASS |
| `IdempotencyRecord` exists with canonical request hash and status transitions | Public export in `__init__.py`; tests cover duplicate payload detection and transition to `succeeded` with result ref | PASS |
| `PolicyDecision` exists with denial reasons and stable serialization | Public export in `__init__.py`; tests require reasons for denials and assert denied decision behavior | PASS |
| `AuditAction` exists with trace refs and deterministic payload checksum | Public export in `__init__.py`; tests assert trace / correlation refs, before-state ref, and checksum from `sha256_checksum` | PASS |
| `SecretRef` is metadata-only and blocks unsafe consumers / raw-secret metadata | Public export in `__init__.py`; tests reject frontend consumers and obvious raw-secret metadata keys | PASS |
| Baseline primitive tests exist and pass | `services/foundation/tests/test_primitives.py`; `pytest services/foundation/tests -q` | PASS |

## 4. Verification Evidence

Command rerun from repo root on 2026-04-27 UTC for this sidecar packet:

1. `pytest services/foundation/tests -q` - PASS, `10 passed in 0.16s`

The current foundation package file surface is:

```text
services/foundation/__init__.py
services/foundation/audit.py
services/foundation/dead_letter.py
services/foundation/envelopes.py
services/foundation/exceptions.py
services/foundation/idempotency.py
services/foundation/outbox.py
services/foundation/policy.py
services/foundation/replay.py
services/foundation/README.md
services/foundation/schema_registry.py
services/foundation/secrets.py
services/foundation/serialization.py
services/foundation/tests/__init__.py
services/foundation/tests/test_event_replay_primitives.py
services/foundation/tests/test_primitives.py
services/foundation/types.py
```

Note: the expanded event replay / outbox / DLQ / schema-registry files are
repo-current downstream foundation surface and do not expand this sidecar's
acceptance claim for `SD-FND-001`; persistence and replay closure remain scoped
to the downstream foundation lane identified in the dependency map.

## 5. Dependency Map

### 5.1 Durable Task Dependencies

| Task | Relationship | Current read |
|---|---|---|
| `SD-FND-001` | parent task | Mainline shared foundation package boundary, currently in `review` |
| `SD-FND-001-SIDECAR-ACCEPTANCE` | support helper | Acceptance and dependency packet only; does not mutate canonical truth |
| `SD-FND-002` | direct downstream task | Depends on `SD-FND-001`; should adopt shared envelopes in one BFF command path and one runtime-manager action path |
| `SD-FND-003` | direct downstream task | Depends on `SD-FND-001`; should add shared outbox / DLQ / schema-registry primitives and replay proof |
| `EP5-002-PACKET-PREP-001` | later proof packet prep | Depends on `SD-FND-002` plus `SD-LIN-TRACE-001`; should consume adopted command-envelope behavior, not this sidecar |
| `CROSS-REPO-SD-VERIFY-001` | later cross-repo verification | Depends on `SD-FND-002` plus `SD-LIN-TRACE-001`; should verify frontend command authority, trace/error UX, and runtime telemetry hooks |

### 5.2 Parallel SD Residual Lanes

| Task | Relationship | Boundary for this review |
|---|---|---|
| `SD-LIN-TRACE-001` | parallel lineage trace lane | May consume trace / correlation semantics later; not required to close `SD-FND-001` |
| `SD-SRC-EVIDENCE-001` | parallel source / evidence / search lane | May use foundation trace refs later; not required to close `SD-FND-001` |
| `SD-CONSULT-001` | parallel consultation domain lane | May adopt shared foundation envelopes later; not required to close `SD-FND-001` |
| `SD-RECON-001` | downstream reconciliation lane | Should benefit from durable trace / idempotency / audit primitives after adoption; not required for this package-boundary task |

### 5.3 Semantic Dependency Chain

| Dependency | Source | Why it matters |
|---|---|---|
| SD foundation gap definition | `docs/reviews/2026-04-27-sd-materializable-execution-task-packet.md` | Requires a shared package path and primitive contracts rather than distributed local conventions |
| Accepted planning state | phase7 `planning-session.json` | Confirms EP4 / EP5 planning is accepted, with EP5 live / canary proof still deferred behind explicit gates |
| Current parent handoff | `docs/reviews/2026-04-27-sd-fnd-001-codex-handoff.md` | Lists implemented primitives, path decision, verification, and deferred follow-up boundaries |
| Package boundary note | `services/foundation/README.md` | Separates side-effect-free value objects from adoption, persistence, middleware, and command-handler work |
| Public import surface | `services/foundation/__init__.py` | Gives later lanes a stable import point for foundation primitives |
| Primitive test coverage | `services/foundation/tests/test_primitives.py` | Proves each parent primitive has executable baseline behavior rather than prose-only acceptance |

## 6. Open Cautions for Review

| Caution | Why it matters |
|---|---|
| Parent handoff reviewer text is stale | The handoff doc says `Claude`, but `ai-status.json` now routes both parent and sidecar review to `Codex2` |
| This sidecar is not the parent implementation | It only packages acceptance and dependency evidence for review |
| Foundation adoption is intentionally deferred | BFF/runtime-manager imports and command-path proof belong to `SD-FND-002`, not `SD-FND-001` |
| Durable persistence primitives are intentionally deferred | Outbox, DLQ, schema registry, replay, and storage belong to `SD-FND-003` |
| No production proof level is promoted | This package boundary does not claim EP5 live / canary proof, research activation, or full-system completion |
| Secret handling is metadata-only | `SecretRef` preserves references and policy metadata; raw secret resolution must stay outside this package |

## 7. Scope Boundary - What Reviewer Should Reject

| Problematic move | Why it is wrong |
|---|---|
| Treating this packet as canonical SD-00 / SD-12 architecture truth | This file is support material only |
| Requiring `SD-FND-001` to complete BFF and runtime-manager adoption | That is the explicit scope of `SD-FND-002` |
| Requiring durable outbox / DLQ / schema registry / replay proof in this parent | That is the explicit scope of `SD-FND-003` |
| Treating primitive tests as proof of live / canary execution readiness | The materialization packet keeps EP5 live / canary proof behind later gates |
| Using this helper task to rewrite L1 policy, runtime contracts, registry truth, or governance implementation | Sidecar scope explicitly forbids canonical or runtime implementation changes |
| Allowing raw secret values to cross the foundation package boundary | The current `SecretRef` contract is metadata-only by design |

## 8. Reviewer Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | This sidecar adds only `support/sidecars/SD-FND-001/SD-FND-001-SIDECAR-ACCEPTANCE.md` |
| No canonical truth edited by sidecar | PASS | No L1 policy docs, contract docs, runtime registry, or governance implementation files were modified in this helper slice |
| Parent acceptance mapped to repo-current evidence | PASS | Sections 3 and 4 tie each primitive and path decision to README, public exports, tests, and rerun verification |
| Dependency chain is explicit | PASS | Section 5 maps downstream `SD-FND-002`, `SD-FND-003`, later EP5 packet prep, and parallel SD residual lanes |
| Review caveats are bounded | PASS | Sections 6 and 7 separate the package-boundary task from adoption, persistence, live proof, and canonical truth changes |

## 9. Handoff to Reviewer (`Codex2`)

This sidecar is ready for reviewer use as the acceptance / dependency packet for
`SD-FND-001` in its current `review` state.

What it gives you now:

1. a checklist that maps each parent primitive to concrete repo evidence
2. fresh verification that `pytest services/foundation/tests -q` passes
3. a dependency map showing which follow-on tasks should absorb adoption,
   persistence, replay, and later proof work
4. review guardrails that keep this helper support-only and prevent overclaiming
   canonical truth, live / canary readiness, or downstream SD completion

Recommended reviewer stance now:

1. approve the sidecar if the packet accurately reflects the parent review
   surface and support-only boundary
2. review the parent task against the concrete `services/foundation/` package
   evidence and the owner handoff
3. keep BFF/runtime-manager adoption, durable outbox / DLQ / schema registry,
   replay proof, and EP5 proof packet asks as follow-up work unless they are
   required by the parent acceptance text

---
*Generated by Codex as a sidecar `acceptance_packet` helper for
`SD-FND-001`. This file is a support artifact and does not modify canonical
truth.*
