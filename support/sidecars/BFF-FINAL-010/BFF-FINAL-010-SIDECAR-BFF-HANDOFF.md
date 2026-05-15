# BFF-FINAL-010 · Sidecar: BFF & Frontend Handoff Packet

**Sidecar ID:** BFF-FINAL-010-SIDECAR-BFF-HANDOFF
**Parent task:** BFF-FINAL-010 (Verify and hand off final BFF contract)
**Owner:** Claude2 · **Reviewer:** Codex
**Kind:** bff_handoff_packet · **Mutates canonical:** false
**Created:** 2026-05-08

---

## Purpose

This packet supports the BFF-FINAL-010 parent owner and any frontend consumer preparing
to consume the Pantheon BFF final contract.  It aggregates the complete route surface,
module-level delivery status, outstanding pre-010 gaps, and the frontend integration
contract across all nine BFF-FINAL sub-tasks.

This is a support artifact only.  It does not modify `models.py`, `main.py`,
`action_catalog.py`, `command_executor.py`, L1 canonical truth, or any
runtime/registry/governance code.

---

## 1. Source Snapshot

Inputs read for this sidecar pass (2026-05-08, updated 2026-05-08 closeout):

- `.orchestrator/task-briefs/bff_final_010_sidecar_bff_handoff.md`
- `ai-status.json`
- `docs/bff/execution-tasks/2026-05-07-final/BFF-FINAL-001-contract-foundation.md`
- `docs/bff/execution-tasks/2026-05-07-final/BFF-FINAL-002-idempotency-command-envelope.md`
- `docs/bff/execution-tasks/2026-05-07-final/BFF-FINAL-003-precondition-errors.md`
- `docs/bff/execution-tasks/2026-05-07-final/BFF-FINAL-004-action-catalog.md`
- `docs/bff/execution-tasks/2026-05-07-final/BFF-FINAL-005-sse-approval-ask.md`
- `docs/bff/execution-tasks/2026-05-07-final/BFF-FINAL-006-mcp-tool-import.md`
- `docs/bff/execution-tasks/2026-05-07-final/BFF-FINAL-007-evidence-redaction.md`
- `docs/bff/execution-tasks/2026-05-07-final/BFF-FINAL-008-agora-journal-merge-patch.md`
- `docs/bff/execution-tasks/2026-05-07-final/BFF-FINAL-009-v5-interventions.md`
- `support/sidecars/BFF-FINAL-006/BFF-FINAL-006-SIDECAR-BFF-HANDOFF.md`
- `support/sidecars/BFF-FINAL-009/BFF-FINAL-009-SIDECAR-BFF-HANDOFF.md`
- `ai-task-archive/tasks/BFF-FINAL-007.json`

---

## 2. BFF-FINAL Task Delivery Status

| Task | Title | Status | Notes |
|------|-------|--------|-------|
| BFF-FINAL-001 | Contract foundation | ✅ **done** | `ActionCommandStatus`, `CommandResponse<T>`, `BffErrorEnvelope`, error codes |
| BFF-FINAL-002 | Idempotency and command envelope | ✅ **done** | Header-only `Idempotency-Key`, replay/conflict pattern |
| BFF-FINAL-003 | Precondition errors | ✅ **done** | Final non-2xx precondition error surface |
| BFF-FINAL-004 | Canonical action catalog | ✅ **done** | `GET /bff/actions`, 20 CommandType entries |
| BFF-FINAL-005 | SSE approval and ask channels | ✅ **done** | 21-channel catalog; approval/ask event types |
| BFF-FINAL-006 | MCP server tool import contract | ✅ **done** | Closeout commit `08ac4543`; 6 MCP import tests pass |
| BFF-FINAL-007 | Evidence redaction | ✅ **done** | EvidenceKind capability gate, `RedactedEvidenceRef` |
| BFF-FINAL-008 | Agora journal merge patch | ✅ **done** | `PATCH /bff/agora/journal/{id}` final contract |
| BFF-FINAL-009 | v5 interventions contract | ✅ **done** | R1/R2/R3 resolved (commits `32574279`, `11dd738f`); closeout commit `c0eb50cf` |

---

## 3. Pre-010 Gaps That Must Close Before BFF-FINAL-010

### 3a. BFF-FINAL-006: ✅ closed (commit 08ac4543)

BFF-FINAL-006 is `done`.  Closeout commit `08ac4543` ("BFF-FINAL-006 record MCP import contract
closeout") landed on 2026-05-08.  Verification: `test_mcp_tool_import.py` (6 passed),
`test_final_contract_primitives.py` (5 passed).  This gate is cleared for BFF-FINAL-010.

### 3b. BFF-FINAL-009: R1, R2, and R3 all resolved; done (closeout commit c0eb50cf)

R1 and R2 were implemented by Claude in commit `32574279` (role validator added, fail-open stub
removed) and verified with 80 passing tests.  A subsequent Codex re-review found a third issue,
which was then implemented in commit `11dd738f`:

| # | Issue | Status |
|---|-------|--------|
| R3 | `/bff/v1/commands` accepted top-level `twoManSignatureId`/`secondOperatorId` but stored only `cmd.params`, losing the alias downstream | ✅ Fixed in `11dd738f`: `_stored_command_params` now normalizes top-level aliases into `params["two_man_signature_id"]` for `RemediateSentinelIntervention`; two regression tests added and passing |

BFF-FINAL-009 is `done`.  Closeout commit `c0eb50cf` landed on 2026-05-08.
This gate is cleared for BFF-FINAL-010.

### 3c. BFF-FINAL-009: incomplete routes (documented in BFF-009 sidecar)

These remain missing from `main.py` and are not acceptance-blocking for BFF-FINAL-009 itself,
but the BFF-FINAL-010 parent must decide whether they are in scope for -010 or deferred:

| Route | Notes |
|-------|-------|
| `POST /bff/v5/interventions/{id}/decision` | Decision endpoint: approve/reject/defer |
| `POST /bff/v5/interventions/{id}/two-man-sign` | Second-operator co-sign |

---

## 4. Complete BFF Route Surface (as of 2026-05-08)

### 4a. Command surface

| Method | Path | Delivered by | Auth |
|--------|------|-------------|------|
| `POST` | `/bff/v1/commands` | BFF-FINAL-002/003 | operator |
| `POST` | `/api/v1/operator/commands` | BFF-FINAL-001 (legacy compat) | operator |

### 4b. Action catalog

| Method | Path | Delivered by | Auth |
|--------|------|-------------|------|
| `GET` | `/bff/actions` | BFF-FINAL-004 | operator |

### 4c. SSE stream

| Method | Path | Delivered by | Notes |
|--------|------|-------------|-------|
| `GET` | `/api/v1/stream/{channel}` | BFF-FINAL-005 | 21 valid channels |

### 4d. MCP tool import (BFF-FINAL-006)

| Method | Path | Delivered by | Auth |
|--------|------|-------------|------|
| `POST` | `/bff/v1/mcp/servers/{server_id}/import-tools` | BFF-FINAL-006 | operator/admin |
| `POST` | `/bff/mcp-servers/{server_id}/import-tools` | BFF-FINAL-006 (alias) | operator/admin |
| `POST` | `/bff/v1/mcp/servers/{server_id}/tools/{tool_id}/actions/{action}` | BFF-FINAL-006 | operator/admin |
| `POST` | `/bff/mcp-tools/{tool_id}/{action}` | BFF-FINAL-006 (alias) | operator/admin |

Valid `action` values: `grant`, `revoke`, `disable`, `test`.

### 4e. Agora journal (BFF-FINAL-008)

| Method | Path | Delivered by | Auth |
|--------|------|-------------|------|
| `PATCH` | `/bff/agora/journal/{entry_id}` | BFF-FINAL-008 | operator |

Required: `Content-Type: application/merge-patch+json`, `Idempotency-Key` header.

### 4f. v5 Interventions (BFF-FINAL-009)

| Method | Path | Status | Auth |
|--------|------|--------|------|
| `GET` | `/bff/v5/interventions` | ✅ delivered | operator |
| `POST` | `/bff/v5/interventions/{id}/remediate` | ✅ delivered | operator + MFA + confirm token + two-man |
| `GET` | `/bff/approvals` | ✅ delivered | operator |
| `POST` | `/bff/v5/interventions/{id}/decision` | ❌ not implemented | — |
| `POST` | `/bff/v5/interventions/{id}/two-man-sign` | ❌ not implemented | — |

---

## 5. Final Contract Primitives Reference

### 5a. Response envelope

```jsonc
// CommandResponse<T> — all write/command endpoints
{
  "status": "accepted" | "queued" | "completed",   // ActionCommandStatus only
  "data": { ... },                                  // required; shape is T
  "meta": {
    "idempotency": {
      "idempotencyKey": "<uuid>",
      "replayed": false
    }
  }
}
```

**No `requires_*` value may appear as `status`.**

### 5b. Error envelope

```jsonc
// BffErrorEnvelope — all non-2xx responses
{
  "error": {
    "code": "APPROVAL_REQUIRED",    // BffErrorCode enum value
    "message": "...",
    "correlationId": "...",
    "details": {
      "kind": "precondition_failed",
      "actionId": "...",
      "entityType": "...",
      "entityId": "...",
      "reason": "..."
    }
  }
}
```

### 5c. Final error code catalog

| Code | HTTP | When |
|------|------|------|
| `CONFIRM_TOKEN_REQUIRED` | 428 | `X-Confirm-Token` header absent on critical route |
| `APPROVAL_REQUIRED` | 409 | Approval gate not satisfied |
| `TWO_MAN_REQUIRED` | 409 | Second-signer precondition not met |
| `IDEMPOTENCY_CONFLICT` | 409 | Same idempotency key, different payload |
| `SSE_REPLAY_UNAVAILABLE` | 409 | Replay requested beyond supported replay window |
| `INVALID_PARAMS` | 400 | Missing or malformed required headers/params |
| `INVALID_REQUEST` | 400 | Body contains `idempotencyKey` / malformed payload |
| `AUTHZ_DENIED` | 403 | Operator lacks role or capability |
| `OBJECT_NOT_FOUND` | 404 | Target entity not found |
| `CONCURRENT_MODIFICATION` | 409 | In-flight command for the same entity |
| `INVALID_STATE` | 409 | Entity state does not allow the requested action (e.g., same-operator two-man sign) |

### 5d. Idempotency rules (all write surfaces)

- Idempotency key must be in the `Idempotency-Key` header.
- `X-Idempotency-Key` is accepted as a compatibility alias.
- A body field named `idempotencyKey` is **rejected** with 400 `INVALID_REQUEST`.
- Same key + same payload returns the original result with `replayed=true`.
- Same key + different payload returns 409 `IDEMPOTENCY_CONFLICT`.

---

## 6. SSE Channel Catalog

21 channels from BFF-FINAL-005:

```
approval  ask  artifact  runtime  mcp  skill  channel  tool
ranking   rebalance  evolution  research  signal  inbox
journal   postmortem  loop  sentinel  intervention  audit  system
```

Subscription endpoint: `GET /api/v1/stream/{channel}`

Per-channel reply metadata headers: `X-SSE-Channel`, `X-SSE-Replay-*`

Resync routes on reconnect:

| Channel | Resync route |
|---------|-------------|
| `approval` | `GET /bff/approvals`, `GET /bff/v5/interventions` |
| `ask` | `GET /bff/agora/ask/sessions/{id}` |
| `intervention` | `GET /bff/v5/interventions` |

**SSE emission gaps to note:**
- `sentinel` and `intervention` channels are declared but the delivered `/remediate` handler does not yet emit events (gap noted in BFF-009 sidecar).
- This remains a post-BFF-FINAL-009 follow-up; it is not blocking BFF-FINAL-009 acceptance itself.

---

## 7. Action Catalog Reference (BFF-FINAL-004)

Endpoint: `GET /bff/actions` → `BffActionCatalogResponse`

Catalog covers all 20 `CommandType` values.  Risk levels:

| Risk | CommandTypes | Guards |
|------|-------------|--------|
| CRITICAL | `ActivateKillSwitch`, `HardRollback`, `LiquidateAll`, `RemediateSentinelIntervention` | `requires_two_man=True`, `requires_confirm_token=True`, `requires_approval=True` |
| HIGH | `PauseRuntime`, `IssueRiskOff`, `IssueSafeMode`, `ExecuteRollback` | `requires_confirm_token=True` |
| MEDIUM | `SoftRollback`, `AdjustRiskParams`, etc. | Varies |
| LOW | Read-adjacent actions | Standard auth only |

Frontend notes:
- Do not render a CRITICAL action button without all three guard prerequisites gathered.
- Catalog `version: "v1"` and `generated_at` are included in each response for cache staleness detection.

---

## 8. Evidence Redaction Contract (BFF-FINAL-007)

Read surfaces that include `evidence_refs` must apply the `EVIDENCE_CAPABILITY_MAP`:

| EvidenceKind | Required capability |
|---|---|
| alert | `risk.alert.read` |
| incident | `risk.incident.read` |
| job | `job.read` |
| audit | `audit.read` |
| metric | `metric.read` |
| strategy | `strategy.view` |
| persona | `persona.view` |
| deployment | `deployment.read` |
| runtime | `runtime.read` |
| policy | `policy.read` |
| approval | `approval.read` |
| artifact | `artifact.read` |
| signal | `agora.signal.read` |
| journal | `agora.journal.read` |
| postmortem | `postmortem.read` |

Redacted refs shape:

```jsonc
{
  "evidenceId": "...",
  "kind": "incident",
  "redacted": true,
  "requiredCapability": "risk.incident.read",
  "reason": "AUTHZ_INSUFFICIENT_CAPABILITY"
}
```

Frontend must:
- Render redacted ref rows with a lock icon and the `requiredCapability` string.
- Never silently omit redacted evidence; show redaction count when references are filtered.

---

## 9. Frontend Integration Checklist

### 9a. Command surface

- [ ] All command writes use `POST /bff/v1/commands` with idempotency header.
- [ ] UI does not send `idempotencyKey` in the request body.
- [ ] UI handles `accepted`, `queued`, and `completed` as final success statuses.
- [ ] UI does not treat any `requires_*` value as a success status.
- [ ] UI renders `BffErrorEnvelope.error.code` for all non-2xx responses.
- [ ] UI handles `CONFIRM_TOKEN_REQUIRED` (428) by collecting confirm token before resubmit.
- [ ] UI handles `APPROVAL_REQUIRED` (409) by routing to approval queue.
- [ ] UI handles `TWO_MAN_REQUIRED` (409) by prompting second-operator sign.
- [ ] UI handles `IDEMPOTENCY_CONFLICT` (409) by showing a change-detected notice.

### 9b. Action catalog

- [ ] Frontend fetches `GET /bff/actions` to build `ActionDescriptor[]` UI state.
- [ ] CRITICAL-risk actions are gated behind all three precondition checks before submission.
- [ ] Catalog `version` and `generated_at` are used for frontend cache invalidation.

### 9c. SSE channels

- [ ] Frontend subscribes to channels from the 21-channel catalog only.
- [ ] On reconnect, frontend uses the designated resync route per channel before re-subscribing.
- [ ] Frontend handles `SSE_REPLAY_UNAVAILABLE` (409) gracefully by falling back to REST poll.

### 9d. MCP tool import

- [ ] Import screen sends `Idempotency-Key` header (not body `idempotencyKey`).
- [ ] Import response renders `importedTools` and `rejectedTools` separately.
- [ ] Lifecycle actions use explicit `grant`/`revoke`/`disable`/`test` routes.
- [ ] UI does not expose standalone tool creation path.
- [ ] `lean_direct` tools in live scope are visually quarantined when returned as rejected/disabled.

### 9e. Agora journal

- [ ] Merge patch sends `Content-Type: application/merge-patch+json`.
- [ ] Merge patch sends `Idempotency-Key` header (not body).
- [ ] `PATCH` response includes required `data` field with updated entry.
- [ ] Audit diff (`before`/`after`) is stored server-side; frontend does not need to reconstruct it.

### 9f. v5 Interventions

- [ ] `GET /bff/v5/interventions` uses `intervention_id` (not `id`) as the record primary key.
- [ ] UI recognises `InterventionKind` values: `hiq_sentinel`, `risk_breach`, `strategy_drift`, `loop_anomaly`.
- [ ] UI recognises `InterventionStatus` values: `pending`, `remediated`, `dismissed`, `escalated`.
- [ ] Remediation sends all three required preconditions: `Idempotency-Key`, `X-Confirm-Token`, and `twoManSignatureId`.
- [ ] UI subscribes to `sentinel` and `intervention` channels for real-time updates (events not yet emitted — deferred, see §11 D3).
- [ ] Decision and two-man-sign routes are not yet available; UI should hide or disable those flows until a follow-on task delivers them (deferred, see §11 D1/D2).
- [ ] Remediation payload may send `twoManSignatureId` or `secondOperatorId` either as top-level keys or inside `params`; the BFF normalizes top-level aliases into stored params automatically (commit `11dd738f`).  Sending inside `params` remains the canonical form; top-level is accepted as a convenience alias.

### 9g. Evidence redaction

- [ ] UI renders redacted evidence ref rows with `requiredCapability` shown.
- [ ] UI does not silently drop redacted evidence items.
- [ ] `redactedCount` is shown in list surfaces that cap the result set.

---

## 10. BFF-FINAL-010 Parent Acceptance Evidence Checklist

These map directly to the acceptance criteria in `ai-status.json`:

### "All BFF tests pass"

- [x] BFF-FINAL-006 closeout commit `08ac4543` landed; `test_mcp_tool_import.py` 6 passed.
- [x] BFF-FINAL-009 R1 fix (role enforcement on remediate) — resolved in commit `32574279`.
- [x] BFF-FINAL-009 R2 fix (fail-closed executor stub) — resolved in commit `32574279`.
- [x] BFF-FINAL-009 R3 fix (v1 two-man alias propagation into stored/executor params) — implemented in commit `11dd738f`.
- [x] BFF-FINAL-009 Codex review approval recorded in `ai-status.json`; full BFF suite passed (457, 36 pre-existing warnings).
- [x] BFF-FINAL-009 `done` closeout complete — closeout commit `c0eb50cf` (2026-05-08).
- [ ] Full suite command: `python3 -m pytest services/control-plane/bff -q` shows 0 failures (run as part of BFF-FINAL-010 verification).

### "Cleanup pass complete"

- [ ] No unresolved `TODO`, `FIXME`, or `STUB` comments added by BFF-FINAL tasks remain in production paths.
- [ ] `_V5_INTERVENTIONS_STORE` and other dev-local stubs are gated by `PANTHEON_ENV=dev` or equivalent.
- [x] `command_executor.py` stub fallback (`stub=True` path) removed in commit `32574279`.
- [x] v1 command handler normalizes top-level two-man aliases into `cmd.params` — implemented in commit `11dd738f`.
- [ ] `datetime.utcnow()` deprecation warnings in `read_store.py` (36 existing) are pre-existing and known; do not block closeout but should be filed as a follow-up.

### "Delivery note written"

- [ ] `docs/bff/execution-tasks/2026-05-07-final/BFF-FINAL-010-contract-verification.md` exists.
- [ ] Delivery note records exact final test command and pass count.
- [ ] Delivery note lists all nine BFF-FINAL artifact paths.

### "Coordination response emitted"

- [ ] `.coordination/responses/BFF-2026-05-07-final-backend-delivery.yaml` exists.
- [ ] Response format is `execute-plans`-consumable.
- [ ] Response lists delivered routes, SSE channels, error codes, and any deferral notes.

---

## 11. Known Deferred Items (Out of BFF-FINAL Scope)

These are gaps the parent owner should explicitly mark as deferred rather than silently leaving open:

| # | Item | Reason deferred |
|---|------|----------------|
| D1 | `POST /bff/v5/interventions/{id}/decision` | Not in BFF-FINAL-009 acceptance; may land in a follow-on task |
| D2 | `POST /bff/v5/interventions/{id}/two-man-sign` | Same as D1 |
| D3 | SSE event emission from `/remediate` | Post-BFF-FINAL-009 follow-up; R3 alias fix is complete; SSE emission is a separate deferred item |
| D4 | Read projection for MCP tools (`GET /bff/mcp-tools`, `GET /bff/mcp-servers/{id}/tools`) | BFF-FINAL-006 did not add these; flagged in BFF-006 sidecar §3b |
| D5 | `datetime.utcnow()` deprecation warnings in `read_store.py` | Pre-existing; 36 warnings; no functional impact |
| D6 | Multi-replica SSE replay store | BFF HA policy explicitly defers this per BFF-FINAL-005 |

---

## 12. Verification Commands Used For This Sidecar

No runtime tests were run.  This sidecar only creates and updates a support handoff packet; it
must not mutate the in-progress parent implementation.

Reference reading commands (closeout, 2026-05-08):

```bash
# Confirm BFF-FINAL-006 done status and closeout commit
python3 -c "import json; d=json.load(open('ai-task-archive/tasks/BFF-FINAL-006.json')); print(d['task']['status'], d['task']['delivery']['commit'])"
# -> done  08ac454332fe17a0b31af4d574c9f10464fcb91f

# Confirm BFF-FINAL-009 done status in archive
python3 -c "import json; d=json.load(open('ai-task-archive/tasks/BFF-FINAL-009.json')); print(d['task']['status'], d['task']['delivery']['commit'])"
# -> done  c0eb50cf3b9844807d790086b9ed23e47c2cf95e

# Confirm R3 commit subject and changed files
git show --stat 11dd738f
# -> BFF-FINAL-009: normalize top-level two-man aliases into stored params
```

BFF-FINAL-010 final verification gate: BFF-FINAL-009 is `done` (closeout commit `c0eb50cf`).
BFF-FINAL-010 may proceed with its own verification pass.

---

*This document is a support artifact.  It does not modify canonical truth.*
*The parent owner named in `ai-status.json` decides whether to absorb, amend, or discard these items.*
