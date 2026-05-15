# SD-FND-001 Review Packet (Sidecar)

**Parent Task**: `SD-FND-001` - Materialize canonical foundation package boundary
**Parent Owner**: Codex
**Parent Reviewer**: Codex2 (current routing in `ai-status.json`; older handoff/review files name Claude)
**Sidecar Task**: `SD-FND-001-SIDECAR-REVIEW`
**Sidecar Owner**: Codex
**Sidecar Reviewer**: Codex2
**Helper Kind**: `review_packet`
**Generated**: 2026-04-28T00:31:00Z
**Mutates canonical**: no

> Support artifact only. This packet does not modify L1 canonical truth, core
> contract truth, runtime / registry / governance implementation, or the parent
> task record. It consolidates the SD-FND-001 review evidence and gives Codex2 a
> bounded reviewer handoff.

## 1. Executive Summary

`SD-FND-001` materialized the shared, side-effect-free foundation package at
`services/foundation`. The package exposes the primitive contracts needed by
later SD residual work without starting network clients, database connections,
broker clients, or framework runtimes at import time.

The original owner handoff and Claude review both support approval of the
package-boundary task. This sidecar is a fresh review packet for Codex2 after
review routing changed; it should not be read as a request to reopen or expand
the parent scope.

## 2. Evidence Sources

| Source | Reviewer use |
|---|---|
| `docs/reviews/2026-04-27-sd-materializable-execution-task-packet.md` | Defines the SD-FND-001 scope: choose/document foundation package path, add primitive contracts, and add baseline tests |
| `docs/reviews/2026-04-27-sd-fnd-001-codex-handoff.md` | Parent owner handoff with implemented primitives, path decision, acceptance evidence, and initial 8-test verification |
| `docs/reviews/2026-04-27-sd-fnd-001-claude-review.md` | Existing review approval: all parent acceptance targets pass; non-blocking observations only |
| `support/sidecars/SD-FND-001/SD-FND-001-SIDECAR-ACCEPTANCE.md` | Acceptance/dependency map prepared for the current Codex2 routing |
| `services/foundation/README.md` | Documents the singular package path and side-effect-free boundary |
| `services/foundation/__init__.py` | Public import surface for shared primitives |
| `services/foundation/tests/test_primitives.py` | Baseline SD-FND-001 primitive behavior |
| `services/foundation/tests/test_event_replay_primitives.py` | Repo-current downstream SD-FND-003 extension tests; useful for non-regression context only |

## 3. Parent Acceptance Coverage

| Acceptance target | Evidence | Review read |
|---|---|---|
| Foundation package path chosen and documented | `services/foundation/README.md` normalizes on singular `services/foundation` and notes the older `services/foundations` draft wording | PASS |
| Side-effect-free import boundary | README states no network clients, database connections, or framework runtimes at import; `__init__.py` exports value objects and helpers | PASS |
| Trace context primitive | `TraceContext` exported from `services.foundation`; primitive tests cover required trace/correlation/actor/environment serialization | PASS |
| Command envelope primitive | `CommandEnvelope` exported; tests cover actor, authority scope, trace binding, idempotency key, and mismatch rejection | PASS |
| Stable validation and policy-denial error envelope | `ErrorEnvelope` exported; tests cover validation status `422`, policy denial status `403`, trace refs, and policy decision ref | PASS |
| Idempotency primitive | `IdempotencyRecord` exported; tests cover canonical request hashes, duplicate payload detection, and status transitions | PASS |
| Policy decision primitive | `PolicyDecision` exported; tests require denial reasons and stable serialization | PASS |
| Audit primitive | `AuditAction` exported; tests cover trace/correlation refs, before-state ref, and deterministic payload checksum | PASS |
| Secret reference primitive | `SecretRef` exported; tests keep it metadata-only and reject frontend consumers / obvious raw-secret metadata keys | PASS |
| Baseline tests | `python3 -m pytest services/foundation/tests -q` rerun on 2026-04-28 UTC | PASS - 10 passed in 0.16s |

## 4. Verification

Fresh command run from repo root for this review sidecar:

```text
python3 -m pytest services/foundation/tests -q
..........                                                               [100%]
10 passed in 0.16s
```

Interpretation:

- The parent handoff originally cited 8 primitive tests for SD-FND-001.
- The repo-current suite now has 10 foundation tests because downstream
  SD-FND-003 added outbox / DLQ / schema-registry / replay primitives.
- The 10/10 result is a non-regression signal for the whole current foundation
  package. It does not expand the parent acceptance claim beyond SD-FND-001's
  package-boundary and primitive-contract scope.

## 5. Review Focus Areas For Codex2

| Focus area | What to confirm | Expected disposition |
|---|---|---|
| Routing drift | Older parent handoff/review docs name Claude; current sidecar routing names Codex2 | Treat `ai-status.json` as current execution routing truth |
| Parent scope | SD-FND-001 owns package boundary and primitive contracts only | Do not require SD-FND-002 command-path adoption or SD-FND-003 persistence/replay closure here |
| Import safety | Foundation imports remain pure value objects/helpers with no service clients | Approve if no network/db/broker/framework startup exists at import |
| Secret handling | `SecretRef` remains metadata-only | Raw secret resolution must stay outside this package |
| Proof level | Tests prove contract behavior, not EP5 live/canary readiness | Do not promote live/canary or production proof from this packet |
| Downstream expansion | Current README and exports include SD-FND-003 additions | Treat them as repo-current context, not retroactive parent scope |

## 6. Non-Blocking Observations

The existing Claude review noted three minor follow-up observations that remain
non-blocking for this sidecar:

| Observation | Disposition |
|---|---|
| `policy.py` has an unused `field` import while `audit.py` uses it | Cleanup can happen during a future touch; no behavior impact |
| `AuditAction` derives trace and correlation IDs from `TraceContext` | Acceptable for current primitive contract |
| `CommandEnvelope.__post_init__` compares `ActorRef.actor_ref` strings | Correct behavior; future readability improvement only |

## 7. Reviewer Guardrails

Reject any review interpretation that:

- treats this sidecar as canonical SD-00 / SD-12 architecture truth
- requires BFF/runtime-manager command-path adoption to close SD-FND-001
- requires durable outbox, DLQ, schema registry, or replay proof to close
  SD-FND-001
- claims EP5 live/canary, research activation, or full-system proof readiness
- uses this helper slice to modify L1 policy, runtime contracts, registry truth,
  governance implementation, or the canonical parent record

## 8. Handoff To Codex2

This sidecar is ready for review.

Recommended reviewer decision:

1. Approve this sidecar if the packet accurately reflects the parent review
   evidence and remains support-only.
2. Use the existing parent handoff, Claude review, and acceptance sidecar as the
   primary evidence trail for the parent package-boundary decision.
3. Keep BFF/runtime-manager adoption, durable persistence/replay primitives, and
   EP5 proof packet work in their downstream tasks.

Suggested review summary if approved:

```text
Review packet approved. The sidecar accurately consolidates SD-FND-001 package
boundary evidence, fresh foundation test status, routing caveats, and downstream
scope guardrails. Support artifact only; no canonical truth edited.
```

---
Generated by Codex as a sidecar `review_packet` helper for `SD-FND-001`.
