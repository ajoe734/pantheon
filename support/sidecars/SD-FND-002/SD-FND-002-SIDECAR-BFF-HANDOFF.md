# SD-FND-002 BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `SD-FND-002` - Adopt foundation envelope in BFF and runtime-manager pilot
**Parent Owner**: `Codex2`
**Parent Reviewer**: `Claude`
**Parent Status**: `done` (commit `94264bc`, archived 2026-04-27T16:03:25Z)
**Sidecar Task**: `SD-FND-002-SIDECAR-BFF-HANDOFF`
**Sidecar Owner**: `Claude`
**Sidecar Reviewer**: `Codex` (reassigned from `Claude2` on 2026-04-28)
**Helper Kind**: `bff_handoff_packet`
**Generated**: `2026-04-28`
**Mutates canonical**: `no`

> This is a support artifact only. It does not modify L1 canonical truth, core
> contract truth, runtime / registry / governance implementation, or the parent
> execution record. It packages the BFF / frontend integration story for the
> shared foundation envelope adoption that landed under `SD-FND-002`, so that
> downstream consumers (frontend repo, cross-repo verifier, EP5 packet prep)
> can wire to the new contract surface without reverse-engineering it.

## 1. Executive Summary

`SD-FND-002` landed the shared foundation envelope on **one BFF command path**
and **one runtime-manager action path**:

- BFF pilot: `POST /api/v1/operator/commands`
- runtime-manager pilot: `RuntimeManagerService.execute_kill_switch`

Both paths now build a `TraceContext`, `CommandEnvelope`, `IdempotencyRecord`,
`PolicyDecision`, and `AuditAction` from `services/foundation` and surface a
shared `ErrorEnvelope` shape on policy denial and validation failure. Reviewer
re-ran `pytest services/control-plane/bff/test_governance_command_submission.py
services/runtime-manager/test_runtime_manager.py services/foundation/tests -q`
and observed `59 passed in 2.96s`.

This packet is a frontend / cross-repo facing handoff. It tells integrators:

1. exactly what request and response shape the pilot path now produces
2. which BFF surfaces still use the legacy ad-hoc envelope (i.e. the residual
   adoption gap that frontend code must straddle)
3. how the operator journey looks across the new trace, idempotency, policy,
   audit, and error semantics
4. which artifacts are authoritative for downstream consumers

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Durable task board for parent / sidecar status, owner, reviewer, acceptance, and artifact paths |
| `.orchestrator/task-briefs/sd_fnd_002_sidecar_bff_handoff.md` | Confirms this helper is support-only; reviewer assignment was later moved from `Claude2` to `Codex` by orchestrator handoff |
| `docs/reviews/2026-04-27-sd-fnd-002-codex-handoff.md` | Owner handoff describing implemented adoption, headers, idempotency, error envelope behavior |
| `docs/reviews/2026-04-27-sd-fnd-002-review.md` | Reviewer evidence map and bundled out-of-packet hardening notes |
| `docs/reviews/2026-04-27-sd-materializable-execution-task-packet.md` | Defines the SD foundation gap and the SD-FND-001 / 002 / 003 task split |
| `services/foundation/__init__.py`, `services/foundation/README.md` | Public exports and side-effect-free package boundary established by `SD-FND-001` |
| `services/control-plane/bff/main.py` | BFF pilot path implementation; route handler at `submit_command` line ~11098, foundation context builder at `_build_foundation_command_context` line ~243 |
| `services/control-plane/bff/test_governance_command_submission.py` | Authoritative contract tests for trace propagation, idempotency replay, policy denial envelope, validation error envelope |
| `services/runtime-manager/service.py` | runtime-manager pilot path (`execute_kill_switch`) |
| `services/runtime-manager/test_runtime_manager.py` | runtime-manager pilot tests including `test_execute_kill_switch_emits_foundation_context_and_replays_idempotently` |
| `support/sidecars/SD-FND-001/SD-FND-001-SIDECAR-ACCEPTANCE.md` | Upstream package boundary acceptance and dependency map |

## 3. Pilot Path Contract Surface

### 3.1 Request

`POST /api/v1/operator/commands`

| Header | Required? | Behavior |
|---|---|---|
| `Authorization: Bearer <op:role>` | yes | Identity / role extraction (`_extract_identity`) |
| `X-MFA-Token` | conditional | Used when the command requires verified MFA |
| `X-Trace-Id` | recommended | Propagated into `foundation.trace_context.trace_id`; new trace id is generated when absent |
| `X-Correlation-Id` | recommended | Propagated into `foundation.trace_context.correlation_id` |
| `X-Request-Id` | optional | Stored on the foundation context |
| `X-Idempotency-Key` | recommended | Becomes `foundation.idempotency_record.idempotency_key`; identical key + payload replay the original receipt |

Body shapes accepted by the legacy normaliser remain valid (`command` /
`target` / `action` / `params` / `audit_context` form, plus the published
`command_type` form for ApproveMutation, RejectMutation, RecordSponsorDecision).
SD-FND-002 did not change the body schema; it only added the foundation envelope
around it.

### 3.2 Success response (HTTP 202)

The response body is unchanged in shape (existing `CommandSubmissionResponse`
with `command_id`, `command`, `status`, `accepted_at`, `receipt_id`,
`staleness_warning`). What changed is **what the BFF persists alongside the
record**: every accepted submission now stores a serialized foundation context
under both `foundation` (top-level) and `audit.foundation` on the command
record, so command status reads can replay trace and audit boundary later.

Frontend integrators do not need to consume the persisted foundation block
directly today, but they should:

1. always send and surface `X-Trace-Id` and `X-Correlation-Id` so the persisted
   trace remains useful for downstream status read and incident correlation
2. always send `X-Idempotency-Key` for retried submissions, since duplicate
   keys with matching payload now return the original `receipt_id`

### 3.3 Idempotency replay

| Case | Behavior | HTTP |
|---|---|---|
| Same `X-Idempotency-Key`, same payload | Return original receipt; one command record persisted | 202 |
| Same `X-Idempotency-Key`, different payload | Return idempotency conflict envelope (`error_kind=idempotency_conflict`) with `suggestion` to reuse original payload or pick a new key | 422 (raised via `_foundation_idempotency_conflict_error`) |

### 3.4 Error envelope (policy denial, validation, conflicts)

Errors raised via `_foundation_bff_error` now wrap the legacy `BffError` body in
a shared shape under the FastAPI `detail` field:

```json
{
  "detail": {
    "error": { "code": "...", "message": "...", "details": { ... } },
    "foundation_error": { "error_kind": "policy_denial|validation|idempotency_conflict|...", "trace": { "trace_id": "...", "correlation_id": "..." }, "suggestion": "..." },
    "policy_decision": { "decision": "allow|deny", "decision_id": "...", "reasons": ["..."] },
    "audit_action": { "trace_id": "...", "policy_decision_ref": "...", "before_state_ref": "...", "checksum": "..." }
  }
}
```

| Failure mode | HTTP | `foundation_error.error_kind` | Notes |
|---|---|---|---|
| Role / authority denied | 403 | `policy_denial` | `policy_decision.decision == "deny"`, `audit_action.policy_decision_ref == policy_decision.decision_id` |
| Param shape / missing fields | 422 | `validation` | `audit_action.trace_id` matches submitted `X-Trace-Id` |
| Live runtime scope without `PANTHEON_LIVE_BROKER_ENABLED=true` | 403 | (legacy `error.code == PRECONDITION_NOT_MET`) | Live-broker guard from bundled hardening (see §5) — pre-existing precondition shape; only wrapped under `error`, no `foundation_error` block |
| Concurrent modification (active command on target) | 409 | (legacy `error.code == CONCURRENT_MODIFICATION`) | Wrapped via `_foundation_bff_error`; foundation envelope present |
| Idempotency key + payload mismatch | 422 | `idempotency_conflict` | `suggestion` instructs caller to reuse payload or pick new key |

## 4. BFF Query Gaps & Residual Gates

These describe what the SD-FND-002 pilot **did not** do, so frontend / cross-repo
integrators do not over-claim.

### GAP-FND-002-01 — Adoption is one path only
The foundation envelope is wired only on `POST /api/v1/operator/commands`
(BFF) and `RuntimeManagerService.execute_kill_switch` (runtime-manager). Other
BFF command paths, all read-side queries, and all other runtime-manager actions
still use legacy ad-hoc trace / idempotency / error shapes. Frontend code must
keep its existing handlers for those surfaces and only branch on the new
envelope on the pilot route.

### GAP-FND-002-02 — Persisted foundation context is not exposed via the read API
The serialized foundation context is stored on the command record root and
under `audit.foundation`, but the read endpoint
`GET /api/v1/operator/commands/{command_id}` (`CommandStatusResponse`) was not
extended in this packet. Frontend cannot yet replay the trace / audit boundary
through the public read API; this is a follow-up for cross-repo verification
and the runtime-manager-originated EP5 packet prep.

### GAP-FND-002-03 — Live broker scope guard is bundled, not a foundation primitive
`_ensure_live_broker_scope_allowed` plus `PANTHEON_LIVE_BROKER_ENABLED` arrived
in the same diff as SD-FND-002 but was flagged by the reviewer as
*out-of-packet hardening* (§5). It returns the legacy
`PRECONDITION_NOT_MET` error code, not the new `foundation_error` block.
Treat its UX as a pre-existing precondition gate, not as part of the
foundation envelope adoption.

### GAP-FND-002-04 — Kill-switch persist-order shifted
Runtime-manager now persists kill-switch state **after** the binding action
(was: before). Reviewer flagged that a process crash mid-binding-action will
leave no idempotency ledger entry on disk, while the prior ordering would have
already recorded the safe-mode trigger. Frontend / runbook does not need to
change, but cross-repo verification should keep this on a follow-up list.

### GAP-FND-002-05 — Cross-service idempotency storage is per-service
The pilot uses in-process foundation idempotency on each service. There is no
shared database-backed idempotency store across BFF + runtime-manager yet, so
the same idempotency key sent to both services will not deduplicate at the
cross-service boundary. Frontend should keep treating each service surface as
its own idempotency domain.

## 5. Operator Journey on the Pilot Path

1. **Submit** the operator command.
   - Frontend sends `Authorization`, `X-Trace-Id`, `X-Correlation-Id`,
     `X-Idempotency-Key` (and `X-MFA-Token` when the command requires it).
   - Body uses the legacy `command/target/action/params/audit_context` shape or
     the published `command_type`-style payload (ApproveMutation /
     RejectMutation / RecordSponsorDecision).
2. **Receive** an HTTP 202 receipt or a foundation-shaped error envelope.
   - Success → `command_id`, `receipt_id`, optional `staleness_warning` for
     degraded read-surface UX.
   - Policy denial → 403 with `foundation_error.error_kind=policy_denial` and
     `policy_decision.decision=deny`.
   - Validation failure → 422 with `foundation_error.error_kind=validation`.
   - Idempotency conflict → 422 with `foundation_error.error_kind=idempotency_conflict`.
   - Concurrent target → 409 with `error.code=CONCURRENT_MODIFICATION`
     wrapped under the foundation envelope.
   - Live-broker guard → 403 with `error.code=PRECONDITION_NOT_MET`,
     `error.details.precondition_failed=live_broker_scope` (legacy shape; see
     GAP-FND-002-03).
3. **Retry** with the same `X-Idempotency-Key` and identical payload to replay
   the original receipt.
4. **Poll** `GET /api/v1/operator/commands/{command_id}` for status. The
   response shape is unchanged; the persisted foundation context is internal
   for now (see GAP-FND-002-02).
5. **Surface** trace / correlation ids in operator UI for incident handoff so
   downstream agents and runtime telemetry can stitch the trace.

## 6. Handoff Materials

| Audience | Artifact | Purpose |
|---|---|---|
| Frontend repo (`front-ai-trading-system`) | This packet (`support/sidecars/SD-FND-002/SD-FND-002-SIDECAR-BFF-HANDOFF.md`) | Adoption boundary, header contract, error envelope shape, residual gaps |
| Frontend repo | `services/control-plane/bff/test_governance_command_submission.py` | Executable contract tests for trace, idempotency, policy denial envelope, validation error envelope |
| Frontend repo | `services/control-plane/bff/BFF_API_CONTRACT.md` (existing) | Pre-existing BFF surface inventory; unchanged by SD-FND-002 |
| Cross-repo verify (`CROSS-REPO-SD-VERIFY-001`) | This packet + `docs/reviews/2026-04-27-sd-fnd-002-review.md` | Maps which BFF surfaces are now under foundation envelope vs. legacy and which residual gaps must be tracked |
| EP5 packet prep (`EP5-002-PACKET-PREP-001`) | This packet + runtime-manager handoff | Confirms one BFF and one runtime-manager pilot have foundation envelope; live / canary proof remains gated |
| Cross-service consumers (telemetry, governance) | `services/foundation/__init__.py`, `services/foundation/README.md` | Stable import boundary for shared envelope primitives |

## 7. Verification Pointers

The pilot adoption is verified by tests already in repo. Re-run from repo root
to confirm before extending consumers:

```text
pytest services/control-plane/bff/test_governance_command_submission.py -q
pytest services/runtime-manager/test_runtime_manager.py -q
pytest services/foundation/tests -q
# combined: 59 passed in 2.96s (reviewer rerun on 2026-04-27 UTC)
```

Tests directly relevant to the contract surface in §3:

- `test_submit_command_records_foundation_context_and_replays_idempotency`
- `test_submit_command_policy_denial_returns_foundation_error_envelope`
- `test_submit_command_validation_error_returns_foundation_error_envelope`
- `test_execute_kill_switch_emits_foundation_context_and_replays_idempotently`

This sidecar does not run new tests; it points downstream consumers at the
existing executable evidence.

## 8. Scope Boundary - What Reviewer Should Reject

| Problematic move | Why it is wrong |
|---|---|
| Treating this packet as canonical BFF API contract | Authoritative BFF contract is `services/control-plane/bff/BFF_API_CONTRACT.md` and the route handler / tests; this packet only describes what `SD-FND-002` changed |
| Promoting the pilot to "foundation envelope adopted across all BFF surfaces" | Adoption is one BFF path and one runtime-manager path (GAP-FND-002-01) |
| Treating the live-broker scope guard or kill-switch persist-order shift as part of the foundation envelope contract | They are bundled out-of-packet hardening (review §"Bundled Out-of-Packet Changes"); GAP-FND-002-03 / GAP-FND-002-04 |
| Asking frontend to consume the persisted foundation block via the read API | `GET /api/v1/operator/commands/{command_id}` was not extended in this packet (GAP-FND-002-02) |
| Using this helper to rewrite L1 contract truth or extend BFF routes | Sidecar scope explicitly forbids canonical or runtime implementation changes |
| Promoting EP5 live / canary readiness off this packet | Live / canary proof remains gated under `EP5-002-RUNTIME-LIVE-PROOF-001` and `HUMAN-EP5-002-APPROVAL` |

## 9. Reviewer Checklist

| Check | Expected | Evidence |
|---|---|---|
| Support artifact only | PASS | This sidecar adds only `support/sidecars/SD-FND-002/SD-FND-002-SIDECAR-BFF-HANDOFF.md` |
| No canonical truth edited by sidecar | PASS | No L1 policy doc, BFF route handler, runtime-manager service, or governance implementation file is modified by this helper slice |
| Pilot scope accurately bounded | PASS | §1 and §4 keep the adoption to the named pilot paths; other surfaces stay legacy |
| Header / response / error contract matches repo | PASS | §3 mirrors `submit_command` / `_build_foundation_command_context` / `_foundation_bff_error` and the four named contract tests |
| Bundled out-of-packet hardening tracked separately | PASS | GAP-FND-002-03 (live-broker scope guard) and GAP-FND-002-04 (kill-switch persist-order) are flagged for follow-up, not absorbed into the foundation envelope claim |
| Residual gaps named for downstream consumers | PASS | §4 enumerates GAP-FND-002-01 .. 05 with the integrator impact |

## 10. Handoff to Reviewer (`Codex`)

This sidecar is ready for reviewer use as the BFF / frontend handoff packet for
the already-`done` parent task `SD-FND-002`. Review was reassigned from
`Claude2` to `Codex` after provider capacity failure, so Codex is the active
reviewer for this packet.

What it gives you now:

1. a header / body / response / error contract surface that frontend and
   cross-repo verifiers can wire to without re-reading 11k lines of `bff/main.py`
2. a residual gap list so cross-repo verification (`CROSS-REPO-SD-VERIFY-001`)
   and EP5 packet prep (`EP5-002-PACKET-PREP-001`) can plan the next adoption
   and proof slices without confusing pilot scope with full coverage
3. a pointer set into the executable contract tests that already enforce the
   contract surface, so the parent owner can decide whether to absorb any of
   this prose into a canonical BFF contract doc later

Recommended reviewer stance now:

1. approve the sidecar if §3 / §4 / §5 accurately describe the SD-FND-002
   pilot diff and the bundled hardening boundary
2. keep the residual gaps on the cross-repo verification and EP5 packet prep
   follow-up lists, not as new SD-FND-002 work
3. defer any BFF read-API extension or full-surface adoption to a separate
   downstream task (out of scope for this sidecar)

---
*Generated by Claude as a sidecar `bff_handoff_packet` helper for `SD-FND-002`.
This file is a support artifact and does not modify canonical truth.*
