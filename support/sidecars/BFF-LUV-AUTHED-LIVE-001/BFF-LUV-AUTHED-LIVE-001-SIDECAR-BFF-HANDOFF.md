# BFF-LUV-AUTHED-LIVE-001 Sidecar BFF Handoff Packet

Task ID: BFF-LUV-AUTHED-LIVE-001-SIDECAR-BFF-HANDOFF
Parent Task: BFF-LUV-AUTHED-LIVE-001
Helper kind: bff_handoff_packet
Owner: Claude
Reviewer: Gemini
Prepared: 2026-05-09T16:10:00Z

## Scope

Support-only sidecar for the BFF-LUV-AUTHED-LIVE-001 parent implementation. This packet does not define canonical architecture, change route truth, or modify runtime behavior. It organizes the current blocking state, the operator journey requiring authenticated coverage, and the concrete checklist the parent owner needs once auth is unblocked.

## Current Evidence Snapshot

### What SEM-006 established (done)

Evidence file: `docs/bff/evidence/BFF-LUV-SEM-006-lupin-dev-live-probe-20260509T113136Z.json`

- Target: `https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io`
- `/openapi.json`: 200, 338 paths registered.
- Final contract anonymous probe (113 routes): 111x 401, 2x 200, zero 404 or 500.
- Local pre-deploy suite: 63 passed, 6 pre-existing warnings.
- `VITE_BFF_MODE=live` is safe at the route-registration level.

### What AUTHED-LIVE-001 still needs

- Authenticated live DTO smoke (2xx with valid shape) for representative route families.
- Non-capital write-flow smoke against at least one governed command surface.
- Evidence published under `docs/bff/evidence/`.
- Explicit `VITE_BFF_REAL_WRITES=true` gate decision.

### Auth blocker

Observed on 2026-05-09 (recorded in `docs/bff/evidence/BFF-LUV-AUTHED-LIVE-001-blocker-20260509.md`):

- Stub colon-format token rejected: `401 INVALID_TOKEN / AUTH_TOKEN_FORMAT`. Strict mode requires a JWT Bearer.
- `PANTHEON_BFF_JWT_SECRET` is not present in the workspace (no `.env`, no GCP secret pulled).
- `gcloud auth print-identity-token` fails with `Reauthentication failed. cannot prompt during non-interactive execution.`
- The lupin dev BFF is running with `PANTHEON_BFF_AUTH_STUB` absent (not set to `true`).

## Auth Resolution Paths

Listed in ascending operator overhead:

| Option | Who acts | Steps | Risk |
|---|---|---|---|
| **A — Enable auth stub on lupin dev** | Human / GCP ops | Set `PANTHEON_BFF_AUTH_STUB=true` on the Cloud Run / GKE service for a bounded smoke window; redeploy. Smoke with `Bearer op-lupin-dev-smoke:operator,admin,reviewer:mfa`. Roll back to strict after smoke. | Low. Stub is gated by env var; no permanent credential exposure. |
| **B — Inject JWT secret** | Human / GCP ops | Pull `PANTHEON_BFF_JWT_SECRET` from GCP Secret Manager and make it available as env to the auto worker or via a local `.env` file. Worker can then mint a HS256 JWT and run the smoke. | Low-medium. Secret must be handled with redaction discipline; not stored in repo. |
| **C — OIDC token via gcloud** | Human | Run `gcloud auth login --update-adc` interactively in the local terminal, then resume the smoke task. Or grant the auto worker's service account the correct IAM scope and refresh ADC. | Medium. Requires human interactive session or service-account IAM update. |
| **D — Provide Bearer token directly** | Human | Obtain a valid operator JWT for `lupin dev` from the IdP / Lovable auth session, paste into env var or file. | Low if short-lived. Token must be redacted in evidence artifacts. |

**Recommended**: Option A (auth stub window) for the fastest unblock, then revoke immediately. Option B if the JWT secret is already in GCP Secret Manager under the current IAM context.

## Operator Journey — Authenticated BFF Coverage Map

Route families that need an authenticated 2xx smoke to complete AUTHED-LIVE-001 acceptance. Grouped by operator journey stage.

### Stage 1 — Session Bootstrap (priority: P0)

| Route | Method | Smoke assertion |
|---|---|---|
| `/bff/me` | GET | 200; DTO includes `operator_id`, `roles`, `auth_mode`. Not empty. |
| `/bff/me/prefs` | GET | 200 or 404 with BFF envelope. No 500. |

Reference: `services/control-plane/bff/test_bff_session_auth_me_contract.py`

### Stage 2 — Strategy and Persona Read Models (priority: P0)

| Route | Method | Smoke assertion |
|---|---|---|
| `/bff/deployments` | GET | 200; list or empty list. |
| `/bff/personas` | GET | 200; list or empty list. |
| `/bff/strategies` | GET | 200; list or empty list. |
| `/bff/capital-allocations` | GET | 200 or 403; no 500. |

Reference: `services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py`

### Stage 3 — Governance and Approvals (priority: P1)

| Route | Method | Smoke assertion |
|---|---|---|
| `/bff/approvals` | GET | 200; list or empty list. |
| `/bff/approvals/{id}` | GET | 200 with probe id (likely 404 BFF envelope). |
| `/bff/interventions` | GET | 200 or empty list. |
| `/bff/audit` | GET | 200; list or empty. |

### Stage 4 — Alerts, Incidents, Artifacts, Runtimes (priority: P1)

| Route | Method | Smoke assertion |
|---|---|---|
| `/bff/alerts` | GET | 200; list. |
| `/bff/alerts/{id}` | GET | 200 or 404 BFF envelope. |
| `/bff/artifacts` | GET | 200; list. |
| `/bff/runtimes` | GET | 200; list. |

### Stage 5 — Agora Core and Daily Brief (priority: P1)

| Route | Method | Smoke assertion |
|---|---|---|
| `/bff/agora/signals` | GET | 200; list. |
| `/bff/agora/signals/{id}` | GET | 200 or 404 BFF envelope. |
| `/bff/agora/journal` | GET | 200; list. |
| `/bff/agora/sessions` | GET | 200; list. |
| `/bff/agora/inbox` | GET | 200; list or empty. |

Reference: `services/control-plane/bff/test_bff_agora_core_contract.py`

### Stage 6 — V5 Loop and Sentinel (priority: P1)

| Route | Method | Smoke assertion |
|---|---|---|
| `/bff/v5/control-room` | GET | 200; control-room envelope. |
| `/bff/v5/loop-status` | GET | 200; loop status envelope. |

Reference: `services/control-plane/bff/test_bff_v5_loop_sentinel_contract.py`

### Stage 7 — MCP, Tools, Skills, Channels (priority: P2)

| Route | Method | Smoke assertion |
|---|---|---|
| `/bff/mcp/tools` | GET | 200; list. |
| `/bff/channels` | GET | 200; list or empty. |

### Stage 8 — Write-Flow Smoke — Non-Capital Targets Only (priority: P0)

The following write surfaces are approved for smoke because they carry no live-capital side effects:

| Route | Method | Smoke payload | Expected |
|---|---|---|---|
| `POST /bff/confirm-tokens` | POST | `{"action": "smoke-probe", "scope": "read_only"}` | 200/201 with token envelope; `token_id` present. |
| `GET /bff/confirm-tokens/{tokenId}` | GET | Use token id from create response | 200 with token detail. |
| `DELETE /bff/confirm-tokens/{tokenId}` | DELETE | Use token id from create response | 200/204; token removed. |
| `POST /bff/v1/commands` (dry-run only) | POST | `{"type": "dry_run_probe", "target": "smoke"}` | 400 validation or 200 with governed receipt envelope; must NOT produce a live-capital order. |

Do NOT run write smoke against:
- `/bff/deployments` (POST/PATCH) — live deployment side effects.
- `/bff/capital-allocations` (POST/PATCH) — live capital side effects.
- `/bff/personas/{id}/actions/bind-strategy` — live binding changes.
- Any route that emits a broker order.

Reference: `services/control-plane/bff/test_final_command_execution_bridge.py`

## Parent Absorption Checklist

Once auth is unblocked, the parent owner should:

1. Run Stage 1 (`/bff/me`) smoke first to confirm token is valid and identity returns correctly.
2. Walk Stages 2–7 read routes and collect actual status codes and minimal DTO shape evidence.
3. Run Stage 8 write-flow smoke against confirm-token create/read/delete only; record token ids with redaction.
4. Record evidence in a new file under `docs/bff/evidence/` using the pattern `BFF-LUV-AUTHED-LIVE-001-authed-smoke-<timestamp>.json`.
5. Include in evidence: target URL, timestamp, route list with actual status codes, per-route DTO shape checks (field names only, no PII or token values), redacted auth source description.
6. Update the BFF-LUV-AUTHED-LIVE-001 artifact (`docs/bff/execution-tasks/2026-05-09-execute-plans-semantic-completion/BFF-LUV-AUTHED-LIVE-001-authenticated-dto-write-smoke.md`) with a "Completion Result" section listing the evidence file and gate outcome.
7. Issue the `VITE_BFF_REAL_WRITES=true` gate decision: safe only if Stage 1–7 return 2xx for all representative routes and Stage 8 confirm-token smoke completes without error.

## Frontend Execute-Plans Cutover Gate

The following gates block `VITE_BFF_REAL_WRITES=true` in the execute-plans frontend:

| Gate | Status | Owner |
|---|---|---|
| Route registration (VITE_BFF_MODE=live safe) | **done** (SEM-006) | Gemini |
| Authenticated live DTO smoke (all Stage 1–7 routes return 2xx) | **blocked on auth** | BFF-LUV-AUTHED-LIVE-001 / Gemini |
| Non-capital write-flow smoke (confirm-token round-trip) | **blocked on auth** | BFF-LUV-AUTHED-LIVE-001 / Gemini |
| BFF transport/session foundation in execute-plans repo | in_progress | BFF-LUV-FE-001 / Codex2 |
| Management Console read adapters | todo | BFF-LUV-FE-002 / Claude |
| Agora/v5/realtime live adapters | todo | BFF-LUV-FE-003 / Gemini2 |
| Safe real write flows in execute-plans | todo | BFF-LUV-FE-004 / Claude2 |

`VITE_BFF_REAL_WRITES=true` should not be enabled until all rows above are done or have documented exceptions.

## Reviewer Handoff Notes

Reviewer (Gemini) should verify:

1. Packet stays support-only: no canonical route snapshot or L1 doc was edited.
2. Auth resolution paths are accurate and do not prescribe an unsupported deployment change.
3. Operator journey route list is consistent with the live anonymous probe routes in `docs/bff/evidence/BFF-LUV-SEM-006-lupin-dev-live-probe-20260509T113136Z.json`.
4. Write-flow smoke scope excludes all live-capital routes.
5. Frontend cutover gate table is consistent with current task board status in `ai-status.json`.

Source references for reviewer:

- `docs/bff/execution-tasks/2026-05-09-execute-plans-semantic-completion/BFF-LUV-AUTHED-LIVE-001-authenticated-dto-write-smoke.md`
- `docs/bff/evidence/BFF-LUV-AUTHED-LIVE-001-blocker-20260509.md`
- `docs/bff/evidence/BFF-LUV-SEM-006-lupin-dev-live-probe-20260509T113136Z.json`
- `services/control-plane/bff/main.py` (lines 191–350 for auth configuration)
- `services/control-plane/bff/test_bff_session_auth_me_contract.py`
- `services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py`

This packet is ready for Gemini review and parent-owner absorption decisions.
