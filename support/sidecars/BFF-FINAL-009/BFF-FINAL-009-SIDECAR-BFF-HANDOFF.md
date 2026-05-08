# BFF-FINAL-009 · Sidecar: BFF & Frontend Handoff Packet

**Sidecar ID:** BFF-FINAL-009-SIDECAR-BFF-HANDOFF
**Parent task:** BFF-FINAL-009 (Implement v5 interventions contract)
**Owner:** Claude · **Reviewer:** Codex
**Kind:** bff_handoff_packet · **Mutates canonical:** false
**Created:** 2026-05-08

---

## Purpose

This packet supports the current BFF-FINAL-009 owner named in `ai-status.json` and
any frontend consumer picking up the v5 interventions contract.  It identifies BFF query gaps, maps the
operator journey, and lists everything the frontend needs to integrate.

It is a support artifact only.  It does not modify `models.py`, `main.py`, `read_store.py`,
or any canonical truth file.

---

## 1. Current BFF State (as of 2026-05-08)

### Routes already on the `/bff/` prefix

| Method | Path | Delivered by |
|--------|------|--------------|
| `POST` | `/bff/v1/commands` | BFF-FINAL-002/003 |
| `PATCH` | `/bff/agora/journal/{entry_id}` | BFF-FINAL-008 |
| `GET` | `/bff/actions` | BFF-FINAL-004 |
| `POST` | `/bff/mcp-servers/{server_id}/import-tools` | BFF-FINAL-006 |
| `POST` | `/bff/mcp-tools/{tool_id}/grant` | BFF-FINAL-006 |
| `POST` | `/bff/mcp-tools/{tool_id}/revoke` | BFF-FINAL-006 |
| `POST` | `/bff/mcp-tools/{tool_id}/disable` | BFF-FINAL-006 |
| `POST` | `/bff/mcp-tools/{tool_id}/test` | BFF-FINAL-006 |

### v5 intervention routes: **not yet implemented**

`/bff/v5/interventions` appears in the SSE resync map (`_SSE_RESYNC_ROUTES["approval"]`,
currently around line 14382 of `main.py`), but no `@app.get` / `@app.post` handlers exist for it.

### SSE channels already declared

`SSE_CHANNEL_CATALOG` (currently around line 14341 of `main.py`) includes `"sentinel"` and
`"intervention"`.  Buffers and subscriber lists are initialized.  No BFF-FINAL-009
endpoint-specific events are emitted to either channel yet.

---

## 2. BFF Query Gaps for BFF-FINAL-009

### 2a. Missing route handlers (main.py)

| Route | Notes |
|-------|-------|
| `GET /bff/v5/interventions` | HIQ list; aggregates approvals, sentinel findings, incidents, policy exceptions |
| `POST /bff/v5/interventions/{id}/decision` | `InterventionDecisionRequest`; `Idempotency-Key` header required |
| `POST /bff/v5/interventions/{id}/remediation` | `ExecuteRemediationRequest`; confirm token + optional approval + optional two-man |
| `POST /bff/v5/interventions/{id}/two-man-sign` | `TwoManSignRequest`; body must NOT contain `idempotencyKey` |

### 2b. Missing models (models.py)

| Model | Shape | Notes |
|-------|-------|-------|
| `InterventionKind` | `str Enum` | `approval \| sentinel \| incident \| policy_exception` |
| `InterventionStatus` | `str Enum` | `pending \| awaiting_two_man \| awaiting_confirm \| resolved \| escalated` |
| `InterventionItem` | read DTO | `id, kind: InterventionKind, status, title, severity?, created_at, source_ref, allowedActions, evidence_refs` |
| `InterventionListResponse` | `items: List[InterventionItem], page_info, meta` | |
| `InterventionDecisionRequest` | `decision: "approve" \| "reject" \| "defer", reason: str, incident_id?: str` | body `idempotencyKey` must be absent |
| `InterventionDecisionData` | `interventionId, decision, signatureId?` | payload for `CommandResponse<T>` |
| `ExecuteRemediationRequest` | `remediationPlanId: str, reason: str, confirm_token: str` | body `idempotencyKey` absent |
| `RemediationData` | `interventionId, remediationId, applied_changes: list` | payload for `CommandResponse<T>` |
| `TwoManSignRequest` | `signerRole: str, reason: str` | **no `idempotencyKey` in body** |
| `TwoManSignData` | `interventionId, decision, signatureId, approvalId?, completed: bool` | payload for `CommandResponse<T>` |

### 2c. Missing read_store methods (read_store.py)

| Method | Data sources | Notes |
|--------|-------------|-------|
| `list_intervention_items(kind?, status?, severity?)` | `approval_queue_items` + `sentinel_findings` (new) + `incidents` + `policy_exceptions` | Sorted by severity desc, then `created_at` desc |
| `get_intervention_item(intervention_id)` | same union | Returns `None` when not found |
| `list_sentinel_findings(status?, severity?)` | `sentinel_findings` local fallback dataset | No upstream record for sentinel yet; needs fallback dataset key |

### 2d. Missing local fallback dataset key

`read_store` references dataset names as string keys in `_local_fallback(...)`.
`sentinel_findings` is not yet registered.  Add an empty-dict fallback entry and
seed fixture data for tests.

---

## 3. Operator Journey: v5 HIQ

```
Operator opens HIQ panel
   │
   ▼
GET /bff/v5/interventions
   ?kind=sentinel,approval&status=pending
   Authorization: Bearer <operator-token>
   ─────────────────────────────────────────────
   200 {items: [...], page_info: {...}, meta: {...}}
   Each item has allowedActions: {canDecide, canRemediate, canSignTwo}
   │
   ├─[Approval decision needed]────────────────────────────────────┐
   │  POST /bff/v5/interventions/{id}/decision                     │
   │  Idempotency-Key: <uuid>                                      │
   │  {decision: "approve", reason: "...", incident_id?: "..."}   │
   │  ─────────────────────────────────────────────                │
   │  202 CommandResponse<InterventionDecisionData>                 │
   │  → SSE intervention.decided event                             │
   │                                                               │
   ├─[Sentinel remediation]────────────────────────────────────────┤
   │  POST /bff/v5/interventions/{id}/remediation                  │
   │  Idempotency-Key: <uuid>                                      │
   │  X-Confirm-Token: <token>   ← required for high-risk         │
   │  {remediationPlanId: "...", reason: "...", confirm_token: ...}│
   │  ─────────────────────────────────────────────                │
   │  202 CommandResponse<RemediationData>                          │
   │  → SSE sentinel.remediated event                              │
   │                                                               │
   │  [If two-man required → 409 TWO_MAN_REQUIRED]                │
   │  Second operator:                                             │
   │  POST /bff/v5/interventions/{id}/two-man-sign                 │
   │  Idempotency-Key: <uuid>                                      │
   │  {signerRole: "risk_officer", reason: "..."}                  │
   │  ─────────────────────────────────────────────                │
   │  202 CommandResponse<TwoManSignData{completed: true}>          │
   │  → SSE intervention.two_man_completed event                   │
   │                                                               │
   └───────────────────────────────────────────────────────────────┘
```

### Precondition failure responses the frontend must handle

| HTTP | ErrorCode | When |
|------|-----------|------|
| 428 | `CONFIRM_TOKEN_REQUIRED` | Remediation without `X-Confirm-Token` |
| 409 | `APPROVAL_REQUIRED` | Remediation needs prior approval gate |
| 409 | `TWO_MAN_REQUIRED` | Action needs second signer before execution |
| 409 | `CONCURRENT_MODIFICATION` | Another in-flight command for same intervention |
| 409 | `IDEMPOTENCY_CONFLICT` | Same idempotency key, different body |
| 404 | `OBJECT_NOT_FOUND` | Intervention ID not in HIQ |

All precondition failures are `BffErrorEnvelope` (non-2xx).  They include `correlationId`
plus `details.kind`, `details.actionId`, `details.entityType`, `details.entityId`,
and `details.reason`.

---

## 4. Frontend Integration Spec

### 4a. Read surface: list interventions

```
GET /bff/v5/interventions
  ?kind=approval|sentinel|incident|policy_exception   (comma-separated, optional)
  ?status=pending|awaiting_two_man|...                (comma-separated, optional)
  ?severity=critical|high|medium|low                  (optional)
  ?page_token=<opaque>                                (optional)
  ?page_size=<int 1..200, default 20>                 (optional)
  Authorization: Bearer <token>
```

**Response 200:**
```jsonc
{
  "items": [
    {
      "id": "int-<uuid>",
      "kind": "sentinel",            // InterventionKind
      "status": "pending",           // InterventionStatus
      "title": "Drawdown limit breach — persona alpha-01",
      "severity": "critical",
      "created_at": "2026-05-08T00:00:00Z",
      "source_ref": {
        "type": "sentinel_finding",
        "id": "sf-<uuid>"
      },
      "allowedActions": {
        "canDecide": true,
        "canRemediate": false,       // false until decision resolved
        "canSignTwo": false
      },
      "evidence_refs": []            // List[RedactedEvidenceRef | EvidenceRef]
    }
  ],
  "page_info": { "next_page_token": null },
  "meta": {
    "snapshot_at": "2026-05-08T00:00:00Z",
    "surfaces": {
      "interventions": { "status": "available", "snapshot_at": "..." }
    }
  }
}
```

### 4b. Write: decision

```
POST /bff/v5/interventions/{id}/decision
  Authorization: Bearer <token>
  Idempotency-Key: <uuid>            ← required; header only
  Content-Type: application/json

  {
    "decision": "approve",           // "approve" | "reject" | "defer"
    "reason": "Risk within policy.",
    "incident_id": "inc-<uuid>"      // optional
  }
```

**Response 202:**
```jsonc
{
  "status": "accepted",             // ActionCommandStatus
  "data": {
    "interventionId": "int-<uuid>",
    "decision": "approve",
    "signatureId": "sig-<uuid>"
  },
  "meta": null
}
```

### 4c. Write: remediation

```
POST /bff/v5/interventions/{id}/remediation
  Authorization: Bearer <token>
  Idempotency-Key: <uuid>
  X-Confirm-Token: <token>          ← required for high-risk remediation
  Content-Type: application/json

  {
    "remediationPlanId": "rp-<uuid>",
    "reason": "Sentinel escalation — auto-remediation approved."
    // No "idempotencyKey" field in body — 400 if present
  }
```

**Response 202:**
```jsonc
{
  "status": "accepted",
  "data": {
    "interventionId": "int-<uuid>",
    "remediationId": "rem-<uuid>",
    "applied_changes": []
  }
}
```

### 4d. Write: two-man sign

```
POST /bff/v5/interventions/{id}/two-man-sign
  Authorization: Bearer <second-operator-token>
  Idempotency-Key: <uuid>
  Content-Type: application/json

  {
    "signerRole": "risk_officer",
    "reason": "Verified and countersigned."
    // No "idempotencyKey" field in body — rejected if present
  }
```

**Response 202:**
```jsonc
{
  "status": "accepted",
  "data": {
    "interventionId": "int-<uuid>",
    "decision": "execute",
    "signatureId": "sig2-<uuid>",
    "approvalId": "appr-<uuid>",   // present when two-man completes an approval loop
    "completed": true
  }
}
```

### 4e. SSE channels

| Channel | Events | Resync on reconnect |
|---------|--------|---------------------|
| `intervention` | `intervention.decided`, `intervention.two_man_completed`, `intervention.escalated` | `GET /bff/v5/interventions` |
| `sentinel` | `sentinel.finding.created`, `sentinel.remediated`, `sentinel.finding.resolved` | — |
| `approval` | existing approval events | `GET /bff/approvals`, `GET /bff/v5/interventions` |

SSE subscription endpoint (existing): `GET /api/v1/stream/{channel}`

The `intervention` and `sentinel` channels are already declared in `SSE_CHANNEL_CATALOG`
and will work once the BFF-FINAL-009 implementor emits events to them.

### 4f. Auth and role requirements

| Action | Minimum role |
|--------|-------------|
| `GET /bff/v5/interventions` | `operator` (read role) |
| `POST .../decision` | `operator` |
| `POST .../remediation` | `operator` + MFA (high-risk path) |
| `POST .../two-man-sign` | `risk_officer` or `supervisor` (must differ from first signer) |

---

## 5. Implementation Hints for BFF-FINAL-009 Owner

These are non-binding suggestions.  The BFF-FINAL-009 owner decides the final shape.

1. **Reuse `_require_final_command_preconditions`** from `/bff/v1/commands` for remediation
   and two-man-sign admission — it already handles confirm token, approval, and two-man checks.

2. **Reuse `_resolve_final_idempotency_key`** and **`_reject_body_idempotency_key`** for
   all three write endpoints.

3. **Intervention list projection:**  call `read_store.list_approval_queue_items()` for the
   approval subtype, `read_store.list_incidents(status="open,escalated")` for the incident
   subtype, and a new `read_store.list_sentinel_findings()` for sentinel items.  Union, tag
   each with `kind`, sort by severity then `created_at` desc.

4. **`list_sentinel_findings` stub:**  add a `sentinel_findings` key to the local fallback
   dataset map and seed an empty dict so tests can inject fixture data without touching the
   upstream service layer.

5. **SSE emission:** emit an `SseEventEnvelope` to `_sse_buffers["intervention"]` and
   `_broadcast_sse("intervention", ...)` after each successful decision / two-man sign.
   Emit to `_sse_buffers["sentinel"]` after successful remediation.

6. **Two-man signer validation:** reject if `identity.operator_id` matches the stored first
   signer from the intervention record (`409 INVALID_STATE` + reason `same_operator`).

7. **Emergency remediation audit:** even when proceeding, write an audit record using the
   existing `command_store.submit_command(...)` pattern so the action appears in the
   governance audit log.

8. **Test naming convention** (mirrors BFF-FINAL-007 pattern):
   - `test_bff_final_009_intervention_list_returns_200`
   - `test_bff_final_009_decision_accepted`
   - `test_bff_final_009_remediation_requires_confirm_token`
   - `test_bff_final_009_remediation_requires_approval`
   - `test_bff_final_009_two_man_sign_rejects_same_operator`
   - `test_bff_final_009_two_man_sign_rejects_body_idempotency_key`

---

## 6. Open Questions for BFF-FINAL-009 Owner

| # | Question | Impact |
|---|----------|--------|
| Q1 | Does `GET /bff/v5/interventions` need evidence-ref redaction (BFF-FINAL-007 rules)? | If yes: run `redact_evidence_refs` on each item's `evidence_refs` |
| Q2 | Should decision endpoint emit to `approval` SSE channel in addition to `intervention`? | Determines whether existing approval SSE consumers see HIQ decisions |
| Q3 | Is `defer` a valid `InterventionDecisionRequest.decision` value, or only `approve`/`reject`? | Scope of the write model |
| Q4 | Does remediation emit a `CommandReceipt` (async, returns 202 immediately) or blocks until complete? | Based on `/bff/v1/commands` pattern → async |
| Q5 | What `ObjectType` does the two-man target use in the command store? | Need to add `INTERVENTION` to `ObjectType` enum or reuse existing |

---

## 7. Parent Implementation Acceptance Evidence Checklist

- [ ] `GET /bff/v5/interventions` returns 200 with `items`, `page_info`, `meta`
- [ ] Intervention list aggregates ≥ 2 source kinds (approval queue + at least one other)
- [ ] Decision endpoint returns `CommandResponse<InterventionDecisionData>`
- [ ] Remediation returns 428 when `X-Confirm-Token` absent
- [ ] Remediation returns 409 `APPROVAL_REQUIRED` when approval gate missing
- [ ] Remediation returns 409 `TWO_MAN_REQUIRED` when two-man gate not satisfied
- [ ] Two-man sign returns 409 `INVALID_STATE` when same operator signs both legs
- [ ] Two-man sign rejects body `idempotencyKey` (400)
- [ ] SSE `intervention` buffer receives at least one event per decision write
- [ ] Existing BFF test suite still passes (`pytest services/control-plane/bff -q`)

---

*This document is a support artifact.  It does not modify canonical truth.*
*The parent owner named in `ai-status.json` absorbs or discards items at their discretion.*
