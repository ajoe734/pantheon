# BFF-FINAL-009 · Sidecar: BFF & Frontend Handoff Packet

**Sidecar ID:** BFF-FINAL-009-SIDECAR-BFF-HANDOFF
**Parent task:** BFF-FINAL-009 (Implement v5 interventions contract)
**Owner:** Claude2 · **Reviewer:** Claude
**Kind:** bff_handoff_packet · **Mutates canonical:** false
**Updated:** 2026-05-08 (revision by Claude2 — reflects implementation state as of BFF-FINAL-009 in_progress)

> **Revision note:** Original packet written by Claude (commit dba218e9, 2026-05-08).
> This revision updates sections 1–4 and 7 to reflect routes, models, and tests now
> delivered by BFF-FINAL-009 owner. Remaining gaps are clearly marked.

---

## Purpose

This packet supports the current BFF-FINAL-009 owner named in `ai-status.json` and
any frontend consumer picking up the v5 interventions contract.  It identifies BFF query gaps, maps the
operator journey, and lists everything the frontend needs to integrate.

It is a support artifact only.  It does not modify `models.py`, `main.py`, `read_store.py`,
or any canonical truth file.

---

## 1. Current BFF State (as of 2026-05-08, post-implementation)

### v5 intervention routes: delivery status

| Method | Path | Status | Notes |
|--------|------|--------|-------|
| `GET` | `/bff/v5/interventions` | ✅ **Delivered** | Returns `InterventionListResponse`; filters by `kind`, `status` |
| `POST` | `/bff/v5/interventions/{id}/remediate` | ✅ **Delivered** | Guarded: TWO_MAN_REQUIRED, APPROVAL_REQUIRED, CONFIRM_TOKEN_REQUIRED |
| `POST` | `/bff/v5/interventions/{id}/decision` | ❌ **Not yet implemented** | See section 2a |
| `POST` | `/bff/v5/interventions/{id}/two-man-sign` | ❌ **Not yet implemented** | See section 2a |

### Models: delivery status

| Model | Status | Actual delivered shape |
|-------|--------|----------------------|
| `InterventionStatus` | ✅ **Delivered** | `pending \| remediated \| dismissed \| escalated` |
| `InterventionKind` | ✅ **Delivered** | `hiq_sentinel \| risk_breach \| strategy_drift \| loop_anomaly` |
| `InterventionRecord` | ✅ **Delivered** | `intervention_id, kind, status, target_type, target_id, triggered_at, triggered_by, remediation_action?, remediated_at?, two_man_signature_id?, correlation_id?, description` |
| `InterventionListResponse` | ✅ **Delivered** | `items: List[InterventionRecord], count: int, generated_at: str` |
| `CommandType.REMEDIATE_SENTINEL_INTERVENTION` | ✅ **Delivered** | `"RemediateSentinelIntervention"` |
| `ObjectType.SENTINEL_INTERVENTION` | ✅ **Delivered** | Present in ObjectType enum |
| `InterventionDecisionRequest` | ❌ **Not yet implemented** | — |
| `InterventionDecisionData` | ❌ **Not yet implemented** | — |
| `ExecuteRemediationRequest` | ❌ **Not yet implemented** | Route uses generic `Dict[str, Any]` body |
| `TwoManSignRequest` | ❌ **Not yet implemented** | — |
| `TwoManSignData` | ❌ **Not yet implemented** | — |

### Note: actual vs. originally suggested shapes

The delivered implementation diverges from the original sidecar suggestions in model names and enum values.
The canonical source of truth is `models.py`; the suggestions in the original sidecar v1 are advisory only.

Divergences:
- `InterventionStatus` values differ (`remediated`/`dismissed` instead of `awaiting_two_man`/`awaiting_confirm`/`resolved`)
- `InterventionKind` values differ (`hiq_sentinel`/`risk_breach`/`strategy_drift`/`loop_anomaly` instead of `approval`/`sentinel`/`incident`/`policy_exception`)
- Route path is `/remediate` (not `/remediation`) to match `action="remediate_sentinel_intervention"`
- `InterventionRecord` uses `intervention_id` (not `id`) as the primary key field

### SSE channels already declared and wired

`SSE_CHANNEL_CATALOG` includes `"sentinel"` and `"intervention"`.
`GET /bff/v5/interventions` is registered in the `approval` SSE resync route map
(`_SSE_RESYNC_ROUTES["approval"]`).  Events are not yet emitted by the delivered
implementation (no `_broadcast_sse` calls in the GET handler or remediate handler).

### Previously delivered routes (from earlier BFF-FINAL tasks)

| Method | Path | Delivered by |
|--------|------|--------------|
| `POST` | `/bff/v1/commands` | BFF-FINAL-002/003 |
| `PATCH` | `/bff/agora/journal/{entry_id}` | BFF-FINAL-008 |
| `GET` | `/bff/actions` | BFF-FINAL-004 |
| `POST` | `/bff/mcp-servers/{server_id}/import-tools` | BFF-FINAL-006 |

---

## 2. BFF Query Gaps for BFF-FINAL-009 (remaining)

### 2a. Missing route handlers (main.py)

| Route | Notes |
|-------|-------|
| `POST /bff/v5/interventions/{id}/decision` | Decision endpoint: approve / reject / defer |
| `POST /bff/v5/interventions/{id}/two-man-sign` | Second-operator co-sign; must verify signer ≠ first signer |

### 2b. Missing models (models.py)

| Model | Suggested shape | Notes |
|-------|-----------------|-------|
| `InterventionDecisionRequest` | `decision: "approve" \| "reject" \| "defer", reason: str, incident_id?: str` | `idempotencyKey` must NOT be in body |
| `InterventionDecisionData` | `interventionId, decision, signatureId?` | Payload for `CommandResponse<T>` |
| `ExecuteRemediationRequest` | `remediationPlanId: str, reason: str` | Formal model for `/remediate` body (currently `Dict`) |
| `RemediationData` | `interventionId, remediationId, applied_changes: list` | Payload for `CommandResponse<T>` |
| `TwoManSignRequest` | `signerRole: str, reason: str` | **no `idempotencyKey` in body** |
| `TwoManSignData` | `interventionId, decision, signatureId, approvalId?, completed: bool` | Payload for `CommandResponse<T>` |

### 2c. SSE emission gaps

The remediate endpoint (`/bff/v5/interventions/{id}/remediate`) does not currently
emit SSE events on success.  Frontend consumers expecting real-time updates on the
`sentinel` or `intervention` channels will not receive them until the owner adds
`_broadcast_sse("sentinel", ...)` and `_broadcast_sse("intervention", ...)` calls.

---

## 3. Operator Journey: v5 HIQ

```
Operator opens HIQ panel
   │
   ▼
GET /bff/v5/interventions
   ?kind=hiq_sentinel,risk_breach&status=pending
   Authorization: Bearer <operator-token>
   ─────────────────────────────────────────────
   200 InterventionListResponse { items: [...], count, generated_at }
   Each InterventionRecord has: intervention_id, kind, status, target_type,
   target_id, triggered_at, two_man_signature_id?
   │
   ├─[Sentinel remediation — primary path]─────────────────────────────────────┐
   │  POST /bff/v5/interventions/{id}/remediate                                 │
   │  Idempotency-Key: <uuid>                                                   │
   │  X-Confirm-Token: <token>   ← required (428 CONFIRM_TOKEN_REQUIRED)       │
   │  X-MFA-Token: <token>       ← required for high-risk                      │
   │  {reason: "...", remediationPlanId: "..."}                                 │
   │                                                                             │
   │  Precondition failures (before execution):                                  │
   │    409 TWO_MAN_REQUIRED   — two_man_signature_id absent                    │
   │    409 APPROVAL_REQUIRED  — approval gate not satisfied                     │
   │    428 CONFIRM_TOKEN_REQUIRED — X-Confirm-Token header missing              │
   │                                                                             │
   │  → 202 CommandResponse (accepted + queued)                                 │
   │  → SSE: not yet wired (gap)                                                │
   │                                                                             │
   ├─[Decision — not yet implemented]──────────────────────────────────────────┤
   │  POST /bff/v5/interventions/{id}/decision                                  │
   │  Idempotency-Key: <uuid>                                                   │
   │  {decision: "approve"|"reject"|"defer", reason: "..."}                     │
   │  → 202 CommandResponse<InterventionDecisionData>   (planned)               │
   │                                                                             │
   ├─[Two-man sign — not yet implemented]──────────────────────────────────────┤
   │  POST /bff/v5/interventions/{id}/two-man-sign                              │
   │  Idempotency-Key: <uuid>  (header only; body must NOT contain the key)    │
   │  {signerRole: "risk_officer", reason: "..."}                               │
   │  → 202 CommandResponse<TwoManSignData{completed: true}>   (planned)        │
   │                                                                             │
   └────────────────────────────────────────────────────────────────────────────┘
```

### Precondition failure responses the frontend must handle

| HTTP | ErrorCode | When | Route |
|------|-----------|------|-------|
| 428 | `CONFIRM_TOKEN_REQUIRED` | Remediation without `X-Confirm-Token` header | `/remediate` ✅ |
| 409 | `APPROVAL_REQUIRED` | Remediation needs prior approval gate | `/remediate` ✅ |
| 409 | `TWO_MAN_REQUIRED` | Action needs second signer (`two_man_signature_id` absent) | `/remediate` ✅ |
| 409 | `CONCURRENT_MODIFICATION` | Another in-flight command for same intervention | shared |
| 409 | `IDEMPOTENCY_CONFLICT` | Same idempotency key, different body | shared |
| 404 | `OBJECT_NOT_FOUND` | Intervention ID not found | planned |

All precondition failures are `BffErrorEnvelope` (non-2xx).  They include `correlationId`
plus `details.kind`, `details.actionId`, `details.entityType`, `details.entityId`,
and `details.reason`.

---

## 4. Frontend Integration Spec

### 4a. Read surface: list interventions (DELIVERED)

```
GET /bff/v5/interventions
  ?kind=hiq_sentinel|risk_breach|strategy_drift|loop_anomaly   (optional)
  ?status=pending|remediated|dismissed|escalated               (optional)
  Authorization: Bearer <token>
```

**Response 200:**
```jsonc
{
  "items": [
    {
      "intervention_id": "...",           // primary key (not "id")
      "kind": "hiq_sentinel",             // InterventionKind
      "status": "pending",                // InterventionStatus
      "target_type": "persona",
      "target_id": "alpha-01",
      "triggered_at": "2026-05-08T00:00:00Z",
      "triggered_by": "sentinel",         // default
      "remediation_action": null,         // set after remediation
      "remediated_at": null,
      "two_man_signature_id": null,       // set when two-man gate passed
      "correlation_id": null,
      "description": ""
    }
  ],
  "count": 1,
  "generated_at": "2026-05-08T00:00:00Z"
}
```

### 4b. Write: remediation (DELIVERED)

```
POST /bff/v5/interventions/{id}/remediate
  Authorization: Bearer <token>
  Idempotency-Key: <uuid>
  X-Confirm-Token: <token>          ← required
  X-MFA-Token: <token>              ← required for admin/high-risk path
  Content-Type: application/json

  {
    "reason": "Sentinel escalation — remediation approved.",
    "remediationPlanId": "rp-<uuid>"   // optional; currently Dict-typed
    // No "idempotencyKey" field — generic Dict body accepted
  }
```

**Response 202:** `CommandResponse` (accepted + queued via command_store)

### 4c. Write: decision (PLANNED — not yet implemented)

```
POST /bff/v5/interventions/{id}/decision
  Authorization: Bearer <token>
  Idempotency-Key: <uuid>            ← header only, must NOT appear in body
  Content-Type: application/json

  {
    "decision": "approve",           // "approve" | "reject" | "defer"
    "reason": "Risk within policy.",
    "incident_id": "inc-<uuid>"      // optional
  }
```

**Planned response 202:** `CommandResponse<InterventionDecisionData>`

### 4d. Write: two-man sign (PLANNED — not yet implemented)

```
POST /bff/v5/interventions/{id}/two-man-sign
  Authorization: Bearer <second-operator-token>
  Idempotency-Key: <uuid>
  Content-Type: application/json

  {
    "signerRole": "risk_officer",
    "reason": "Verified and countersigned."
    // No "idempotencyKey" field in body — reject if present
  }
```

**Planned response 202:** `CommandResponse<TwoManSignData>`

### 4e. SSE channels

| Channel | Events | Resync on reconnect | Status |
|---------|--------|---------------------|--------|
| `intervention` | `intervention.decided`, `intervention.two_man_completed`, `intervention.escalated` | `GET /bff/v5/interventions` | Channel declared; events not yet emitted |
| `sentinel` | `sentinel.finding.created`, `sentinel.remediated`, `sentinel.finding.resolved` | — | Channel declared; events not yet emitted |
| `approval` | existing approval events | `GET /bff/approvals`, `GET /bff/v5/interventions` | Resync target already wired |

SSE subscription endpoint (existing): `GET /api/v1/stream/{channel}`

### 4f. Auth and role requirements

| Action | Minimum role | Status |
|--------|-------------|--------|
| `GET /bff/v5/interventions` | `operator` (read role) | ✅ Enforced |
| `POST .../remediate` | `operator` + MFA + confirm token + two-man | ✅ Enforced |
| `POST .../decision` | `operator` | Planned |
| `POST .../two-man-sign` | `risk_officer` or `supervisor` (must differ from first signer) | Planned |

---

## 5. Implementation Hints for BFF-FINAL-009 Owner (remaining gaps)

These are non-binding suggestions for the remaining two write endpoints.

1. **Decision endpoint** should reuse `_resolve_final_idempotency_key` and call
   `_reject_body_idempotency_key(payload)` before processing, matching the pattern
   used in the remediate handler.

2. **Two-man-sign endpoint** must verify `identity.operator_id` differs from the stored
   first signer on the intervention record (409 `INVALID_STATE`, reason `same_operator`).

3. **SSE emission for remediate:** add `_broadcast_sse("sentinel", SseEventEnvelope(...))`
   and optionally `_broadcast_sse("intervention", ...)` after successful remediation
   so frontend listeners receive real-time updates without requiring a poll.

4. **Formal request models:** consider replacing `Dict[str, Any]` body on `/remediate`
   with `ExecuteRemediationRequest` pydantic model for cleaner validation and OpenAPI docs.

5. **Test naming convention** (follows existing tests in `test_v5_interventions.py`):
   - `test_bff_final_009_decision_accepted`
   - `test_bff_final_009_decision_rejects_body_idempotency_key`
   - `test_bff_final_009_two_man_sign_rejects_same_operator`
   - `test_bff_final_009_two_man_sign_rejects_body_idempotency_key`
   - `test_bff_final_009_remediate_emits_sentinel_sse_event`

---

## 6. Open Questions for BFF-FINAL-009 Owner

| # | Question | Impact |
|---|----------|--------|
| Q1 | Does `GET /bff/v5/interventions` need evidence-ref redaction (BFF-FINAL-007 rules)? | `InterventionRecord` currently has no `evidence_refs` field; owner decides if needed |
| Q2 | Should decision endpoint emit to `approval` SSE channel in addition to `intervention`? | Determines whether existing approval SSE consumers see HIQ decisions |
| Q3 | Is `defer` a valid `InterventionDecisionRequest.decision` value? | Scope of the write model |
| Q4 | Should `/remediate` SSE emission go to `sentinel` only, or also `intervention`? | Frontend consumers need to know which channels to subscribe |
| Q5 | Should `two_man_signature_id` on `InterventionRecord` be set by the BFF on receipt of two-man-sign, or by downstream? | Determines whether BFF must mutate `_V5_INTERVENTIONS_STORE` after two-man sign |

---

## 7. Parent Implementation Acceptance Evidence Checklist

### Already verified (by existing tests in test_v5_interventions.py)

- [x] `GET /bff/v5/interventions` returns 200 with `items`, `count`, `generated_at`
- [x] Intervention list filters by `status` query parameter
- [x] `GET /bff/v5/interventions` requires `Authorization` header
- [x] Remediation returns 409 `TWO_MAN_REQUIRED` when `twoManSignatureId` absent
- [x] Remediation returns 409 `APPROVAL_REQUIRED` when approval gate missing
- [x] Remediation returns 428 `CONFIRM_TOKEN_REQUIRED` when `X-Confirm-Token` absent
- [x] Remediation with all preconditions returns 202
- [x] `POST /bff/v1/commands` with `RemediateSentinelIntervention` also enforces two-man
- [x] `GET /bff/v5/interventions` listed in approval SSE resync routes
- [x] `RemediateSentinelIntervention` present in action catalog
- [x] `CommandType.REMEDIATE_SENTINEL_INTERVENTION` present
- [x] `ObjectType.SENTINEL_INTERVENTION` present
- [x] `InterventionRecord` model fields correct

### Remaining acceptance criteria (not yet tested)

- [ ] Decision endpoint returns `CommandResponse<InterventionDecisionData>`
- [ ] Decision endpoint rejects body `idempotencyKey` (400)
- [ ] Two-man sign returns 409 `INVALID_STATE` when same operator signs both legs
- [ ] Two-man sign rejects body `idempotencyKey` (400)
- [ ] SSE `intervention` or `sentinel` buffer receives at least one event per remediation write
- [ ] Existing BFF test suite still passes after new endpoints added (`pytest services/control-plane/bff -q`)

---

*This document is a support artifact.  It does not modify canonical truth.*
*The parent owner named in `ai-status.json` absorbs or discards items at their discretion.*
