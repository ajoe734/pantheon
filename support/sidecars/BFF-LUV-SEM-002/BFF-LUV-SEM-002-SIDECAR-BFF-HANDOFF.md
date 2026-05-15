# BFF-LUV-SEM-002 BFF and Frontend Handoff Packet

**Sidecar Task ID**: `BFF-LUV-SEM-002-SIDECAR-BFF-HANDOFF`
**Parent Task**: `BFF-LUV-SEM-002`
**Parent Owner**: `Claude`
**Parent Reviewer**: `Codex2`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Claude`
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-05-09
**Last Refresh**: 2026-05-09T10:29:53Z
**Mutates Canonical**: `no`

This is a support artifact only. It does not update canonical truth, L1
policy, core contracts, runtime-manager behavior, registry logic, governance
implementation, BFF implementation, frontend code, or compose wiring. The
parent owner decides whether and how to absorb this packet into
`BFF-LUV-SEM-002`.

---

## 1. Parent Scope Snapshot

`BFF-LUV-SEM-002` completed the execute-plans command bridge for final BFF
command routes that previously returned generic accepted/completed receipts.
The parent slice moved these routes onto BFF command-store-backed receipts
where a domain command type exists, while preserving the final `CommandResponse`
envelope and keeping live capital side effects disabled.

Parent acceptance target:

- affected command routes write command records or explicit command-store-backed
  receipts;
- duplicate idempotency keys replay the original command receipt;
- changed payloads under the same idempotency key return final 409 conflict
  envelopes;
- no live broker, production capital, or direct runtime side effect is enabled;
- focused command bridge tests and final wiring tests pass.

Parent status at this refresh: `BFF-LUV-SEM-002` is in `review`. The parent task
record notes the server-generated-target replay fix for deployment create and
sentinel remediation build, plus 136 focused BFF tests passing.

Frontend-facing disposition: browser clients still call BFF only. These command
aliases are command receipt surfaces; they are not permission to call
runtime-manager, governance, deployment, sentinel, or broker services directly.

---

## 2. Current BFF Command Surface

All write routes below require a bearer identity and an idempotency header. The
canonical header is `Idempotency-Key`; `X-Idempotency-Key` remains accepted as a
compatibility alias. `idempotencyKey` in the JSON body is rejected.

| Route | Method | Command type | Target object | Status | Frontend note |
|---|---:|---|---|---:|---|
| `/bff/actions/{entityType}/{entityId}/{actionId}` | POST | `StrategyAction` for strategies, otherwise `ReviewAction` | mapped from `entityType` | 202 | Generic action alias now replays/conflicts through command store. |
| `/bff/deployments` | POST | `CreateDeployment` | `Deployment` | 201 | `deployment_id`, `deploymentId`, or `id` may be supplied; otherwise BFF generates one and still replays same-key retries. |
| `/bff/deployments/{id}` | PATCH | `PatchDeployment` | `Deployment` | 202 | Receipt only; refresh deployment read model after completion. |
| `/bff/rebalances/{id}` | PATCH | `PatchRebalance` | `Rebalance` | 202 | Receipt only; refresh rebalance read model after completion. |
| `/bff/audit/export` | POST | `AuditExport` | `AuditExport` | 202 | Target id is derived from `target_type` / `targetType`, or `audit-export`. |
| `/bff/confirm-tokens` | POST | `CreateConfirmToken` | `ConfirmToken` | 201 | Returns `tokenId`, `id`, and `status: created`; no browser-side secret bypass. |
| `/bff/confirm-tokens/{tokenId}` | GET | read projection | `ConfirmToken` | 200 | Projects latest command-store token state as `available`, `created`, `redeemed`, or `deleted`. |
| `/bff/confirm-tokens/{tokenId}/redeem` | POST | `RedeemConfirmToken` | `ConfirmToken` | 202 | Use a fresh idempotency key per redeem attempt. |
| `/bff/confirm-tokens/{tokenId}` | DELETE | `DeleteConfirmToken` | `ConfirmToken` | 202 | Use a fresh idempotency key per delete attempt. |
| `/bff/v5/interventions/{id}/claim` | POST | `V5InterventionAction` | `SentinelIntervention` | 202 | Lifecycle receipt for v5 intervention flow. |
| `/bff/v5/interventions/{id}/decide` | POST | `V5InterventionAction` | `SentinelIntervention` | 202 | Payload carries the decision/action details. |
| `/bff/v5/interventions/{id}/escalate` | POST | `V5InterventionAction` | `SentinelIntervention` | 202 | Receipt does not bypass governance preconditions. |
| `/bff/v5/interventions/{id}/release` | POST | `V5InterventionAction` | `SentinelIntervention` | 202 | Receipt does not locally mutate list/detail state. |
| `/bff/v5/interventions/{id}/two-man-sign` | POST | `V5InterventionAction` | `SentinelIntervention` | 202 | Keep two-man proof in payload/audit material. |
| `/bff/v5/sentinel/findings/{id}/status` | POST | `SentinelFindingStatus` | `SentinelFinding` | 202 | Use read model refresh for visible finding state. |
| `/bff/v5/sentinel/remediation/build` | POST | `SentinelRemediationBuild` | `SentinelRemediation` | 202 | `finding_id` / `findingId` may be supplied; otherwise BFF generates a target id and same-key retry replays. |
| `/bff/v5/sentinel/remediation/{actionId}/execute` | POST | `SentinelRemediationExecute` | `SentinelRemediation` | 202 | Receipt explicitly keeps `liveCapitalSideEffects: false`. |

Receipt shape to preserve in frontend clients:

```json
{
  "status": "accepted",
  "data": {
    "status": "accepted",
    "command": "CreateDeployment",
    "commandId": "cmd-...",
    "command_id": "cmd-...",
    "receipt_id": "cmd-...",
    "receipt": {
      "id": "cmd-...",
      "command_id": "cmd-...",
      "status": "accepted",
      "trackingUrl": "/api/v1/operator/commands/cmd-...",
      "tracking_url": "/api/v1/operator/commands/cmd-..."
    }
  },
  "meta": {
    "durable": true,
    "liveCapitalSideEffects": false,
    "idempotency": {
      "key": "client-retry-key",
      "idempotencyKey": "client-retry-key",
      "replayed": false
    }
  }
}
```

Command status polling remains:

```http
GET /api/v1/operator/commands/{command_id}
Authorization: Bearer <token>
```

The status response reports the command record (`command_id`, `type`, `target`,
`submitted_at`, `status`, `result`, `error`, `audit`). SEM-002 command aliases
write receipts and command records; the browser should not assume domain state
changed until the owning read surface reflects it.

---

## 3. BFF Query Gap Matrix

| Topic | Current BFF/frontend surface | Remaining handoff gap | Frontend implication |
|---|---|---|---|
| Command receipt vs domain state | Command aliases return durable BFF command receipts and tracking URLs. | Receipts are not a deployment/rebalance/sentinel read-model mutation proof by themselves. | Show the receipt immediately, then poll command status and refresh the owning read route before showing updated domain state. |
| Deployment create with generated id | `POST /bff/deployments` can generate a target id when the body omits `deployment_id` / `deploymentId` / `id`. | The generated target id is not separately surfaced as a domain readback guarantee. | Preserve the `command_id` as the retry/support anchor. Prefer client-supplied ids when the UI needs deterministic route navigation. |
| Sentinel remediation build with generated id | `POST /bff/v5/sentinel/remediation/build` can generate a target id when `finding_id` / `findingId` is absent. | The generated remediation id is receipt-scoped unless later read surfaces expose it. | Use a known `finding_id` when the UI wants to navigate back to a finding-specific remediation panel. |
| Header idempotency | `Idempotency-Key` is canonical; `X-Idempotency-Key` is accepted. | Body-level `idempotencyKey` is intentionally rejected. | Store retry keys outside the JSON payload. Reuse the same key only for the exact same logical submission. |
| Replay and conflict semantics | Same key and same payload replays original receipt; same key and changed payload returns 409 `IDEMPOTENCY_CONFLICT`. | Server-generated target routes intentionally exclude the generated id from the replay hash. | Safe retry after transport uncertainty should keep the same key and payload. Any user edit must mint a new key. |
| Confirm token readback | `GET /bff/confirm-tokens/{tokenId}` projects command-store status. | The projection is command-history based and does not represent an out-of-band secret or MFA provider token. | Render status as operator workflow state only: `available`, `created`, `redeemed`, `deleted`. |
| Action catalog metadata | `services/control-plane/bff/action_catalog.py` lists SEM-002 actions with risk, cooldown, roles, and idempotency metadata. | Route handlers still enforce BFF identity/read-role admission; catalog metadata is for UI affordances and governance display. | Use catalog risk/cooldown/role fields to guide CTA state, but trust BFF 401/403/409/422 responses as the final admission result. |
| Live side effects | Receipts include `meta.liveCapitalSideEffects: false`; command audit stores `live_capital_side_effects: false`. | SEM-002 does not prove downstream production execution. | Do not display "executed live" or broker-impacting copy from these receipts. |

---

## 4. Operator Journey Handoff

### 4.1 Normal Command Submission

1. Operator opens a BFF-backed screen such as deployment review, rebalance,
   audit export, confirm-token, v5 intervention, sentinel finding, or sentinel
   remediation.
2. Frontend renders CTA state from read surfaces plus action catalog metadata.
3. Operator submits through the BFF command alias with:
   `Authorization`, `Idempotency-Key`, optional trace/correlation/request
   headers, and a payload containing reason/audit fields where the screen has
   them.
4. BFF writes a command record and returns a final `CommandResponse` receipt.
5. UI stores `command_id`, `receipt_id`, `trackingUrl`, idempotency key, target
   route, and audit reason in local workflow state.
6. UI polls `GET /api/v1/operator/commands/{command_id}` until the command
   status is terminal or the operator leaves the flow.
7. UI refreshes the owning read surface before showing domain state as changed.

### 4.2 Safe Retry After Network Uncertainty

1. If the browser does not know whether the first request reached BFF, retry the
   same endpoint with the same JSON payload and the same `Idempotency-Key`.
2. A successful replay returns the original `command_id` and
   `meta.idempotency.replayed: true`.
3. If the operator edits stage, decision, reason, target id, or any payload
   field, generate a new idempotency key before resubmitting.
4. A 409 `IDEMPOTENCY_CONFLICT` means the key was already bound to another
   payload; show the conflict and do not silently retry with mutated data.

### 4.3 Confirm Token Flow

1. Create token: `POST /bff/confirm-tokens` with `Idempotency-Key`.
2. Read state: `GET /bff/confirm-tokens/{tokenId}`.
3. Redeem token: `POST /bff/confirm-tokens/{tokenId}/redeem` with a new
   idempotency key.
4. Delete token: `DELETE /bff/confirm-tokens/{tokenId}` with a new idempotency
   key.
5. Treat the token status as BFF command workflow state. Do not expose it as a
   production MFA provider or secret-management primitive.

### 4.4 Sentinel Intervention And Remediation Flow

1. Load v5 intervention and sentinel finding read surfaces:
   `/bff/v5/interventions`, `/bff/v5/interventions/{id}`,
   `/bff/v5/sentinel/findings`, and `/bff/v5/sentinel/findings/{id}`.
2. Submit lifecycle actions through the v5 intervention command aliases.
3. For remediation planning, prefer including `finding_id` in
   `/bff/v5/sentinel/remediation/build` so the UI can preserve finding context.
4. Submit remediation execute through
   `/bff/v5/sentinel/remediation/{actionId}/execute`.
5. Render the receipt as "accepted by BFF command bridge"; do not label it as
   live remediation or broker execution.

### 4.5 Failure And Degraded Journey

1. 400 `INVALID_PARAMS` with `precondition_failed: idempotency_key` means the
   idempotency header is missing.
2. 400 `INVALID_REQUEST` with `precondition_failed: body_idempotency_key` means
   the client put retry identity in the body.
3. 401 means identity/session failed.
4. 403 means identity was recognized but lacks authority or MFA for the broader
   route family.
5. 409 `IDEMPOTENCY_CONFLICT` means the retry key was reused with changed
   payload.
6. 422 means payload shape or precondition details need operator repair.
7. If command status later reports `error`, preserve the original receipt and
   show the command status error rather than retrying blindly.

---

## 5. Frontend Handoff Materials

| Screen / flow | BFF route family | Frontend handoff note |
|---|---|---|
| Generic entity action CTA | `/bff/actions/{entityType}/{entityId}/{actionId}` | Keep payload stable across retry; use receipt `trackingUrl` for support and status. |
| Deployment create/edit | `POST /bff/deployments`, `PATCH /bff/deployments/{id}`, `GET /bff/deployments`, `GET /bff/deployments/{id}` | Prefer client-supplied deployment ids when navigation depends on the id. Refresh list/detail after status polling. |
| Rebalance edit | `PATCH /bff/rebalances/{id}`, `GET /bff/rebalances`, `GET /bff/rebalances/{id}` | Do not show rebalance state as changed until readback confirms it. |
| Audit export | `POST /bff/audit/export`, `GET /bff/audit` | Treat receipt as export request acceptance; render audit/export availability from readback surfaces. |
| Confirm token drawer | `/bff/confirm-tokens*` | Display `available`, `created`, `redeemed`, `deleted`; keep token workflow separate from auth-provider MFA. |
| v5 intervention board | `/bff/v5/interventions*` | Lifecycle action aliases return `V5InterventionAction`; refresh intervention list/detail after polling. |
| Sentinel finding board | `/bff/v5/sentinel/findings*` | Status command receipt is separate from finding read-model update. |
| Sentinel remediation | `/bff/v5/sentinel/remediation/build`, `/bff/v5/sentinel/remediation/{actionId}/execute` | Build and execute receipts keep `liveCapitalSideEffects: false`; avoid live-execution language. |
| Command support panel | `/api/v1/operator/commands/{command_id}` | Always keep a way to inspect command id, type, target, submitted time, status, result/error, and audit. |

Frontend implementation constraints:

- keep the BFF as the browser-facing integration boundary;
- use `Idempotency-Key` for all SEM-002 command aliases;
- keep retry identity out of JSON bodies;
- preserve the same idempotency key only for the exact same logical submission;
- mint a new idempotency key after any user edit;
- render 400, 401, 403, 409, 422, and command-status errors distinctly;
- do not infer live execution, approval success, runtime mutation, or broker
  impact from an accepted receipt;
- refresh read surfaces after command status changes instead of locally
  inventing domain state.

---

## 6. Minimal Smoke Requests For Frontend QA

Use fixture ids that exist in the target environment. The token below is
stub-mode style and is only appropriate when the BFF test/dev environment
allows stub auth.

### 6.1 Deployment Create Replay

```http
POST /bff/deployments
Authorization: Bearer op-sem-002:operator,reviewer,admin:mfa
Idempotency-Key: qa-sem-002-deploy-create-001
Content-Type: application/json

{
  "deployment_id": "dep-qa-sem-002",
  "stage": "paper",
  "reason": "QA command bridge receipt"
}
```

Repeat the same request with the same key and payload. Expected result: same
`data.receipt_id`, `meta.idempotency.replayed: true`, and no second command
record. Change `stage` to `live` under the same key. Expected result: 409
`IDEMPOTENCY_CONFLICT`.

### 6.2 Deployment Create With Server-Generated Target

```http
POST /bff/deployments
Authorization: Bearer op-sem-002:operator,reviewer,admin:mfa
Idempotency-Key: qa-sem-002-deploy-generated-001
Content-Type: application/json

{
  "stage": "paper",
  "reason": "QA generated target replay"
}
```

Repeat the same request with the same key and payload. Expected result: replay,
not conflict.

### 6.3 Confirm Token Lifecycle

```http
POST /bff/confirm-tokens
Authorization: Bearer op-sem-002:operator,reviewer,admin:mfa
Idempotency-Key: qa-sem-002-token-create-001
Content-Type: application/json

{
  "tokenId": "ct-qa-sem-002",
  "reason": "QA guarded action"
}
```

```http
GET /bff/confirm-tokens/ct-qa-sem-002
Authorization: Bearer op-sem-002:operator,reviewer,admin:mfa
```

```http
POST /bff/confirm-tokens/ct-qa-sem-002/redeem
Authorization: Bearer op-sem-002:operator,reviewer,admin:mfa
Idempotency-Key: qa-sem-002-token-redeem-001
Content-Type: application/json

{
  "reason": "QA operator confirmed"
}
```

```http
DELETE /bff/confirm-tokens/ct-qa-sem-002
Authorization: Bearer op-sem-002:operator,reviewer,admin:mfa
Idempotency-Key: qa-sem-002-token-delete-001
Content-Type: application/json

{
  "reason": "QA cleanup"
}
```

Expected readback after delete: `data.status: deleted`.

### 6.4 Sentinel Remediation Build Replay

```http
POST /bff/v5/sentinel/remediation/build
Authorization: Bearer op-sem-002:operator,reviewer,admin:mfa
Idempotency-Key: qa-sem-002-sentinel-build-001
Content-Type: application/json

{
  "reason": "QA generated remediation target replay"
}
```

Repeat the same request with the same key and payload. Expected result: replay,
not conflict. Then repeat with the same key and `{"finding_id": "different"}`.
Expected result: 409 `IDEMPOTENCY_CONFLICT`.

### 6.5 Command Status Poll

```http
GET /api/v1/operator/commands/{command_id}
Authorization: Bearer op-sem-002:operator,reviewer,admin:mfa
```

Expected result: command record with `type`, `target`, `submitted_at`, `status`,
`result`, `error`, and `audit`.

---

## 7. Suggested Parent/Reviewer Verification Focus

| Verification target | Suggested evidence |
|---|---|
| Support-only scope | This packet is the only sidecar-owned artifact; no L1 canonical docs or runtime files are modified by this sidecar. |
| Command aliases write command records | `test_final_command_execution_bridge.py` verifies deployment, action, confirm-token, audit, v5 intervention, sentinel finding, and remediation command records. |
| Idempotency header behavior | Missing header returns 400 `INVALID_PARAMS`; body `idempotencyKey` returns 400 `INVALID_REQUEST`; `X-Idempotency-Key` alias works. |
| Replay and conflict behavior | Same key/payload replays receipt; changed payload returns 409 `IDEMPOTENCY_CONFLICT`. |
| Server-generated target replay | Deployment create without id and sentinel remediation build without finding id replay correctly under same key. |
| No live side effects | Receipts and command audit keep `liveCapitalSideEffects` / `live_capital_side_effects` false. |
| Frontend journey clarity | Packet distinguishes receipt, command status, and domain read-model refresh. |

---

## 8. Sidecar Verification

Performed from the Pantheon repo root:

- `sed -n '1,260p' support/sidecars/BFF-LUV-SEM-002/BFF-LUV-SEM-002-SIDECAR-BFF-HANDOFF.md`
- `rg -n "Reviewer Checklist|Handoff Status|liveCapitalSideEffects|Idempotency-Key|IDEMPOTENCY_CONFLICT" support/sidecars/BFF-LUV-SEM-002/BFF-LUV-SEM-002-SIDECAR-BFF-HANDOFF.md`
- `python3 -m pytest services/control-plane/bff/test_final_command_execution_bridge.py -q` -> 9 passed in 9.33s.
- Owner closeout rerun:
  `python3 -m pytest services/control-plane/bff/test_final_command_execution_bridge.py -q` -> 9 passed in 6.35s.

---

## 9. Reviewer Checklist

| Check | Status |
|---|---|
| Support artifact only | PASS |
| Canonical truth untouched by this sidecar | PASS |
| Parent acceptance mapped | PASS |
| BFF query/readback gaps identified | PASS |
| Operator journey handoff included | PASS |
| Frontend idempotency/retry guidance included | PASS |
| Minimal QA smoke requests included | PASS |
| Reviewer disposition | APPROVED |

---

## 10. Handoff Status

Approved by Claude review on 2026-05-09T10:28:34Z. Reviewer note:
support-only BFF/frontend handoff packet for SEM-002 approved; all reviewer
checks pass; parent owner may absorb checklist and operator journey guidance
into `BFF-LUV-SEM-002` implementation evidence.

Owner closeout confirms this packet is support-only and should be treated as
frontend/BFF handoff material for `BFF-LUV-SEM-002`, not as canonical design
promotion by itself. Unrelated dirty worktree files are excluded from this
sidecar closeout.
