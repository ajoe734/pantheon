# BFF Write-Gap Closure Spec — 2026-05-28 (BE View)

| | |
|---|---|
| **Doc ID** | `BFF_WRITE_GAP_SPEC_2026-05-28` |
| **Version** | 1.1 |
| **Date** | 2026-05-28 |
| **Author** | Pantheon Operator (rewrite from Lovable FE spec) |
| **Audience** | BE / BFF owners (workers under `EPIC-WRITE-GAP-*`) |
| **Probe env** | `https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io` |
| **Probe auth** | `Authorization: Bearer pantheon-dev-browser:reviewer`, `X-Dry-Run: 1` |
| **Upstream FE spec** | `execute-plans/.lovable/specs/be-requirements/BE_WRITE_GAP_SPEC_2026-05-28.md` (origin/main, 506 lines) |
| **Related FE requirement** | `BE Requirements — Management AI Multi-Turn Assistant` v2026-06-03 |

## 0. Why this exists

Lovable FE shipped `withWriteFallback` 30-min overlay + `LiveStatusBanner` so the UI does not crash, but **15 write endpoints** are 404 / 405 / 410 on dev BFF. Until BE closes the gap, the user-facing flows below cannot complete end-to-end:

- Persona Onboarding Wizard (draft → paper_owner → live_owner) — 2 dev personas stuck
- Every high-risk action (retire, promote_live, force-transition, break-glass) — confirm endpoint missing
- 5 Agora writes (signal/feedback/triage/coaching/postmortem) — all return 4xx
- v5 Intervention batch decide — single-item works, batch does not
- Management AI multi-turn panel — FE sends conversation/UI context, but BE must persist session turns, pass UI context to OpenClaw, and return allowlisted actions.

The 15-endpoint surface, classification, and acceptance criteria come straight from two FE probes ran on 2026-05-28:

- `execute-plans/scripts/probe-bff-write-paths.mjs` → 31 endpoints, 8 open
- `execute-plans/scripts/probe-persona-onboarding-endpoints.mjs` → 8 endpoints, 7 open

This doc translates the FE-view spec into BE-view tickets so workers can land routes without reading 506 lines of FE narrative.

## 1. Headline

| Severity | Open routes | EPIC |
|---|---|---|
| **P0 lifecycle / confirm** | 4 | `EPIC-WRITE-GAP-P0-LIFECYCLE` |
| **P0 wizard middle** | 4 | `EPIC-WRITE-GAP-P0-WIZARD` |
| **P1 runtime + agora** | 6 | `EPIC-WRITE-GAP-P1-AGORA` |
| **P2 batch + sentinel** | 2 | `EPIC-WRITE-GAP-P2-MISC` |
| **P0 management AI multi-turn** | 2 contract gaps | `EPIC-MGMT-AI-MULTITURN` |
| **OPS redeploy + live verify** | 1 | `EPIC-WRITE-GAP-OPS` |
| **Total** | **17 routes + 2 contract gaps** | — |

### Open endpoints — index

```
P0  POST /bff/personas/{id}/actions/AdvanceLifecycle              410 deprecated, no replacement
P0  POST /bff/capital-pools/{id}/actions/ApprovePool              410 deprecated, no replacement
P0  POST /bff/runtimes/{id}/actions/StartRuntime                  410 deprecated, no replacement
P0  POST /bff/command-confirmations/{token}/confirm                404 missing — blocks every high-risk action
P0  POST /api/v1/bindings                                          405 wizard step 2c
P0  POST /api/v1/deployment-plans                                  405 wizard step 3
P0  POST /api/v1/approval-decisions                                405 wizard step 4
P0  GET  /api/v1/operator/persona-management/{id} (+data.health)   404 / health field missing — wizard F4
P1  POST /bff/runtimes                                             405 entity create
P1  POST /bff/agora/signals                                        405
P1  POST /bff/agora/feedback                                       404
P1  POST /bff/agora/inbox/{id}/triage                              404
P1  POST /bff/agora/skill-coaching                                 404
P1  POST /bff/agora/postmortems                                    405
P2  POST /bff/v5/interventions/batch-decide                        405
P2  (informational) Sentinel rule coverage for 6 HealthReasonCode  rule engine
P0  POST /bff/management/nl/ask                                     Management AI multi-turn context/actions contract
P0  GET  /bff/management/ai/conversations                           visible session list for frontend resync
P0  GET  /bff/management/ai/conversations/{sessionId}               full session readback; ignores trace_id
```

## 2. Cross-cutting contract (apply to every endpoint)

From Pack D §D-Headers / §D21 / §D26 — restated so workers do not need to chase references.

### 2.1 Required request headers

| Header | Required | Note |
|---|---|---|
| `Authorization: Bearer <jwt>` | yes | |
| `Content-Type: application/json` | yes (write) | |
| `Idempotency-Key` | yes (write) | Replay window ≥ 24h, return original response |
| `X-Correlation-Id` | yes | Echoed in `meta.correlationId` |
| `X-Request-Id` | yes | Per-request UUID |
| `X-BFF-Api-Version: 2026-05-07` | yes | Reject mismatched with `VERSION_MISMATCH` |
| `X-Dry-Run: 1` | optional | Validate only, do not persist, return `meta.dryRun=true` |

### 2.2 Response envelope

Success: `{ "data": …, "meta": { "correlationId", "snapshot_at", "dryRun" } }`. Action commands return **HTTP 202** + `data.commandId` + `data.status ∈ {"accepted","queued","running"}` per Pack D `ActionCommandStatus`.

Error: Pack D §D21 26-code allowlist. Body shape:
```json
{
  "error": {
    "code": "ONE_OF_26_CANONICAL_CODES",
    "i18nKey": "errors.<CODE>",
    "message": "human-readable",
    "retryable": false,
    "userActionable": true,
    "details": {}
  },
  "meta": { "correlationId": "…" }
}
```

26 codes: `VALIDATION_FAILED`, `RESOURCE_NOT_FOUND`, `FORBIDDEN`, `UNAUTHENTICATED`, `CONFLICT`, `PRECONDITION_FAILED`, `RATE_LIMITED`, `IDEMPOTENCY_REPLAY`, `VERSION_MISMATCH`, `OPERATION_NOT_ALLOWED`, `MANDATE_BREACH`, `STATE_TRANSITION_INVALID`, `APPROVAL_REQUIRED`, `TWO_MAN_REQUIRED`, `CONFIRM_TOKEN_EXPIRED`, `CONFIRM_TOKEN_INVALID`, `COOLDOWN_ACTIVE`, `BREAK_GLASS_REQUIRED`, `MEMO_REQUIRED`, `INSUFFICIENT_PERMISSIONS`, `DEPENDENCY_FAILURE`, `UPSTREAM_TIMEOUT`, `INTERNAL_ERROR`, `MAINTENANCE`, `NOT_IMPLEMENTED`, `METHOD_NOT_ALLOWED`.

### 2.3 Audit chain (Pack D §D26)

Every write must append one entry with `prevHash`, `hash`, `evidenceKind` (Pack D 19+3 enum). Retention ≥ 7 years. EvidenceKind per card below.

### 2.4 SSE invalidation

Every state-changing write must publish on the matching channel (Pack D `ENTITY_TO_SSE_CHANNEL`) so FE `useLiveListV1` cache invalidates. Channel per card below.

### 2.5 Deprecated route policy

If a `410 Gone` is returned, `error.details.replacement` MUST contain the canonical replacement route, and the OpenAPI must document the replacement. **A 410 with no `details.replacement` is a P0 regression**. (P0-1/2/3 below violate this today.)

## 3. Repo map (where to land code)

| Layer | Path |
|---|---|
| BFF FastAPI app | `services/control-plane/bff/main.py` |
| Action allowlist | `services/control-plane/bff/action_catalog.py` |
| Command executor | `services/control-plane/bff/command_executor.py` |
| Command queue | `services/control-plane/bff/command_queue.py` |
| Models / schemas | `services/control-plane/bff/models.py` |
| Contract test (envelope) | `services/control-plane/bff/test_bff_error_envelope_shape.py` |
| Contract test (this sprint) | `services/control-plane/bff/test_bff_write_gap_2026_05_28.py` *(new)* |

### 3.1 Management AI multi-turn addendum (2026-06-03)

| Route | Requirement |
|---|---|
| `POST /bff/management/nl/ask` | Accept `conversation.recentTurns`, `conversation.summary`, `attachments`, and `ui.currentRoute/selectedEntity/visiblePanels/filters/availableUiActions`; create or validate a server-side Management AI session; persist the user turn before provider context construction; compose `backend.management_nl.data.conversation` from the server-side turn store, with FE turns retained only as `conversation.clientHint`; persist the assistant turn with `providerStatus` and `uiActions`; return canonical `data.sessionId`, `data.traceId`, `data.providerStatus`, `data.uiActions`, `data.actions`, `data.auditLog`, and `data.conversation.href`. |
| `GET /bff/management/ai/conversations` | Return visible persisted sessions for the current owner or tenant with `sessionId`, `title`, owner/tenant, created/updated timestamps, `turnCount`, and readback `href`; supports a bounded `limit` for frontend resync. |
| `GET /bff/management/ai/conversations/{sessionId}` | Return the server-side session from creation with ordered turns containing `id`, `role`, `text`, `createdAt`, `providerStatus`, and attachment proxy URLs; do not require or filter by `trace_id`; persisted sessions remain visible only to owner or same tenant. If the requested id exists only in browser-local conversation history, return a 200 local-only empty transcript with `localOnly`/`missingInStore` metadata so resync does not surface a raw BFF 404. |

Action suggestions returned by `POST /bff/management/nl/ask` must be constrained to the request's `ui.availableUiActions`. `runBffAction` and any write-style action must set `requiresConfirmation: true`. Management AI session idle TTL is documented as at least 7 days.

Inline attachment input uses `attachments[].dataBase64`. The BFF stores object
bytes under the Management AI attachment store and persists only metadata plus
`storageUrl` in the turn record. Conversation readback returns
`attachments[].url` as `/bff/management/ai/attachments/{attachmentId}` and never
returns base64 payloads. Idempotency replay returns the original response and
does not append duplicate user/assistant turns.

Storage defaults are BFF-owned: `PANTHEON_MANAGEMENT_AI_STORE_PATH` points to
the SQLite session/turn database and
`PANTHEON_MANAGEMENT_AI_ATTACHMENT_STORE_PATH` points to the attachment object
directory. These are the deploy-time swap points for Postgres/GCS/S3 adapters.
The dev compose deployment pins these paths under the mounted BFF data volume:
`/data/bff/management-ai-conversations.sqlite3`,
`/data/bff/management-ai-attachments`, and
`/data/bff/management-ai-audit.jsonl`, so a BFF container recreate does not drop
server-side conversation truth.

Already-existing generic action endpoints (workers extend these instead of creating new routes for P0-1/2/3 — register `AdvanceLifecycle` / `ApprovePool` / `StartRuntime` in the action catalog + handler):

- `POST /bff/personas/{persona_id}/actions/{action_id}` — main.py:34350
- `POST /bff/runtimes/{runtime_id}/actions/{action_id}` — main.py:37791
- `POST /bff/capital-pools/{pool_id}/actions/{action_id}` — main.py:20795
- `GET  /api/v1/operator/persona-management/{persona_id}` — main.py:17225 *(must add `data.health`)*

## 4. Endpoint requirement cards

Per-card format unchanged from FE spec; the BE-view additions are:

- **Land at**: file + approximate insertion line / pattern
- **Action catalog entry**: action_id (for `actions/{action_id}` routes only)
- **Reuses**: existing executor / helper if any

---

### Card P0-1 — `POST /bff/personas/{id}/actions/AdvanceLifecycle` *(register as action)*

| Field | Value |
|---|---|
| Probe | `410 OPERATION_NOT_ALLOWED route_deprecated`, `details.replacement` **missing** |
| Land at | `action_catalog.py` (register `AdvanceLifecycle`), `command_executor.py` (handler) |
| Action catalog | `action_id="AdvanceLifecycle"`, required_roles=`["persona_operator","live_owner_approver"]` (latter when target=live) |
| Body | `{ target_state: "paper_owner"\|"live_owner"\|"retired", confirm_token: string, memo?: string }` |
| Success | `202` `{ data: { status: "accepted", commandId, persona_id, from_state, to_state }, meta }` |
| Errors | 401 `UNAUTHENTICATED`, 403 `INSUFFICIENT_PERMISSIONS`, 409 `STATE_TRANSITION_INVALID`, 412 `CONFIRM_TOKEN_INVALID`, 422 `VALIDATION_FAILED` |
| State machine | `draft → paper_owner → live_owner → retired`; no skip; retire allowed from any non-retired |
| SSE channels | `personas:{id}`, `management.persona-fleet`, `audit:persona-{id}` |
| EvidenceKind | `persona.lifecycle.advance` |
| Acceptance | Probe stage 1 returns 202 + commandId; persona `lifecycle_state` reflects new state within 2s |

---

### Card P0-2 — `POST /bff/capital-pools/{id}/actions/ApprovePool` *(register as action)*

| Field | Value |
|---|---|
| Probe | `410 OPERATION_NOT_ALLOWED route_deprecated` no replacement |
| Land at | `action_catalog.py` (register `ApprovePool`), executor handler |
| Action catalog | `action_id="ApprovePool"`, required_roles=`["treasury_approver"]` |
| Body | `{ memo: string (≥8 chars), confirm_token?: string }` |
| Success | `202` `{ data: { status: "accepted", commandId, pool_id, state: "approved" }, meta }` |
| Errors | 403 `FORBIDDEN`, 409 `STATE_TRANSITION_INVALID` (already approved), 422 `MEMO_REQUIRED` |
| State machine | `draft → approved` (one-way; revoke is separate action) |
| SSE | `capital-pools:{id}`, `management.capital-pools` |
| EvidenceKind | `capital_pool.approve` |
| Acceptance | 202 + pool state visible as `approved` in `GET /bff/capital-pools/{id}` |

---

### Card P0-3 — `POST /bff/runtimes/{id}/actions/StartRuntime` *(register as action)*

| Field | Value |
|---|---|
| Probe | `410 OPERATION_NOT_ALLOWED route_deprecated` no replacement |
| Land at | `action_catalog.py` (register `StartRuntime`), executor handler |
| Action catalog | `action_id="StartRuntime"`, required_roles=`["runtime_operator","live_owner_approver"]` (latter + two-man for live) |
| Body | `{ confirm_token: string, two_man_token?: string }` |
| Success | `202` `{ data: { status: "accepted", commandId, runtime_id, state: "starting" }, meta }` |
| Errors | 403 `INSUFFICIENT_PERMISSIONS`, 403 `TWO_MAN_REQUIRED`, 409 `STATE_TRANSITION_INVALID`, 412 `CONFIRM_TOKEN_EXPIRED`, 423 `COOLDOWN_ACTIVE` |
| State machine | `stopped → starting → running` (BE drives `starting→running` via runtime daemon) |
| SSE | `runtimes:{id}`, `management.runtime-status` |
| EvidenceKind | `runtime.start` |
| Acceptance | 202 then SSE `runtime.status=running` within Pack D `uiBudgets.runtimeStart` (30s) |

---

### Card P0-4 — `POST /bff/command-confirmations/{token}/confirm` *(new route — top severity)*

| Field | Value |
|---|---|
| Probe | `404 RESOURCE_NOT_FOUND "Not Found"` — only GET `/bff/command-confirmations/{token}` exists |
| Land at | `main.py` near existing `GET /bff/command-confirmations/{token}` (grep `command-confirmations`) |
| Body | `{ confirm_token: string, command_id: string, memo?: string, two_man_token?: string }` |
| Success | `202` `{ data: { status: "accepted", commandId, confirmed_at }, meta }` |
| Errors | 404 (token unknown), 410 `CONFIRM_TOKEN_EXPIRED`, 412 `CONFIRM_TOKEN_INVALID`, 403 `TWO_MAN_REQUIRED` |
| State machine | Promotes `pending_confirmation` command to `accepted` and triggers underlying action |
| SSE | Depends on underlying command; minimum `audit:command-{commandId}` |
| EvidenceKind | `command.confirm` |
| Permission | Original action's permission + MFA when required |
| Acceptance | Probe `POST /bff/command-confirmations/token-dev/confirm` returns typed 4xx (NOT `RESOURCE_NOT_FOUND "Not Found"` and NOT `VALIDATION_FAILED "Method Not Allowed"`); valid live token returns 202 |
| **Severity rationale** | **Blocks every high-risk write in the system** (retire / promote_live / runtime start / break-glass / force-transition). |

---

### Card P0-5 — `POST /api/v1/bindings` *(method add — GET already exists)*

| Field | Value |
|---|---|
| Probe | `405 Method Not Allowed` (GET exists at main.py:12744; POST missing) |
| Body | `{ persona_id: string, capital_pool_id: string, role: "paper_owner"\|"live_owner", allowed_deployment_scope: "paper"\|"live", budget: number, expires_at?: string }` |
| Success | `201` `{ data: { id, persona_id, capital_pool_id, role, allowed_deployment_scope, budget, created_at }, meta }` |
| Errors | 403 `FORBIDDEN`, 409 `CONFLICT` (binding exists), 422 `VALIDATION_FAILED` (pool not approved, persona lifecycle mismatch) |
| State machine | Creates binding entity; persona readiness derives `binding=done` |
| SSE | `bindings:{persona_id}`, `personas:{persona_id}` |
| EvidenceKind | `binding.create` |
| Permission | `persona_operator` + binding role's implied permission |
| Acceptance | Probe stage 2c = 201 with binding id; included in `GET /api/v1/operator/persona-management/{id}.bindings[]` |

---

### Card P0-6 — `POST /api/v1/deployment-plans` *(method add)*

| Field | Value |
|---|---|
| Probe | `405` (GET exists at main.py:12774) |
| Body | `{ binding_id: string, artifact_id: string, deployment_mode: "paper"\|"live", capital_pool_id: string, params?: object, locked?: boolean }` |
| Success | `201` `{ data: { id, binding_id, artifact_id, deployment_mode, status: "pending_approval", capital_pool_id, locked, created_at }, meta }` |
| Errors | 403, 409 (artifact not approved), 422 |
| State machine | Plan created in `pending_approval`; persona readiness `plan=done` |
| SSE | `deployment-plans:{id}`, `personas:{persona_id}` |
| EvidenceKind | `deployment_plan.create` |
| Permission | `persona_operator` |
| Acceptance | Probe stage 3 = 201; plan appears in `persona-management/{id}.deploymentPlans[]` |

---

### Card P0-7 — `POST /api/v1/approval-decisions` *(method add)*

| Field | Value |
|---|---|
| Probe | `405` (GET exists at main.py:12800) |
| Body | `{ plan_id: string, decision: "approve"\|"reject", memo: string (≥8), two_man_token?: string }` |
| Success | `202` `{ data: { status: "accepted", commandId, plan_id, decision, approver_id, decided_at }, meta }` |
| Errors | 403 `INSUFFICIENT_PERMISSIONS` (not in quorum), 403 `TWO_MAN_REQUIRED`, 409 (already decided), 422 `MEMO_REQUIRED` |
| State machine | Plan: `pending_approval → approved\|rejected`; persona readiness `approval=done` on approve |
| SSE | `approvals:{plan_id}`, `deployment-plans:{plan_id}`, `personas:{persona_id}` |
| EvidenceKind | `approval.decide` |
| Permission | Pack D `reviewerQuorum`; live requires `live_owner_approver` + MFA |
| Acceptance | Probe stage 4 = 202; plan status updates within 2s |

---

### Card P0-8 — `GET /api/v1/operator/persona-management/{id}` + `data.health` *(field add)*

| Field | Value |
|---|---|
| Probe | `404 RESOURCE_NOT_FOUND` for dev id; `data.health` field absent on existing personas |
| Land at | `main.py:17225` — extend response envelope; reuse `persona-fleet[].health` derivation |
| Success | `200` `{ data: { persona, bindings[], deploymentPlans[], approvals[], runtimeBindings[], health: { status: "healthy"\|"degraded"\|"critical", score: 0..100, reasons: HealthReasonCode[] } }, meta }` |
| HealthReasonCode | `persona_lifecycle_not_active`, `no_runtime_binding`, `active_incident`, `drawdown_threshold`, `negative_pnl`, `runtime_status_attention` |
| Errors | 404 (real id miss), 403 |
| Permission | `persona_operator` or above |
| Acceptance | Probe F4 returns 200 for a valid persona id with all six top-level keys present; `data.health` parity with `persona-fleet[].health` for ≥1 persona |

---

### Card P1-9 — `POST /bff/runtimes` *(method add)*

| Field | Value |
|---|---|
| Probe | `405` (only GET implemented) |
| Body | `{ name: string, persona_id: string, binding_id: string, deployment_plan_id: string, runtime_kind: "paper"\|"live", params?: object }` |
| Success | `201` `{ data: { id, name, state: "stopped", persona_id, binding_id, deployment_plan_id, runtime_kind, created_at }, meta }` |
| Errors | 403, 409 (binding already has runtime), 422 |
| State machine | Creates runtime in `stopped`; persona readiness `runtime` derives |
| SSE | `runtimes:{id}`, `management.runtime-status` |
| EvidenceKind | `runtime.create` |
| Permission | `runtime_operator` |
| Acceptance | Probe row = 201; FE `supabase/functions/management-agent/index.ts` `create_runtime` tool re-enabled |

---

### Card P1-10 — `POST /bff/agora/signals` *(method add)*

| Field | Value |
|---|---|
| Probe | `405` (GET exists at main.py:19006) |
| Body | `{ title: string, body: string, market?: string, tags?: string[], linkedPersonaIds?: string[], linkedStrategyIds?: string[], severity?: "info"\|"warn"\|"alert" }` |
| Success | `201` `{ data: { id, title, body, status: "open", createdAt, … }, meta }` |
| Errors | 403, 422 |
| SSE | `agora.signals`, `agora.inbox` |
| EvidenceKind | `agora.signal.create` |
| Permission | `analyst` or above |
| Acceptance | Probe row = 201 |

---

### Card P1-11 — `POST /bff/agora/feedback` *(new route)*

| Field | Value |
|---|---|
| Probe | `404` |
| Body | `{ signal_id: string, verdict: "useful"\|"noise"\|"false_positive", memo?: string }` |
| Success | `201` `{ data: { id, signal_id, verdict, author_id, created_at }, meta }` |
| Errors | 403, 404 (signal id), 422 |
| SSE | `agora.signals:{signal_id}` |
| EvidenceKind | `agora.feedback.create` |
| Permission | `analyst` |
| Note | Distinct from existing `/bff/agora/signals/{signalId}/feedback` (main.py:19054) — that one is per-signal; this is the canonical bulk write. Worker decides: alias or new handler. |
| Acceptance | Probe row = 201 |

---

### Card P1-12 — `POST /bff/agora/inbox/{id}/triage` *(new route)*

| Field | Value |
|---|---|
| Probe | `404` |
| Body | `{ disposition: "ack"\|"snooze"\|"dismiss"\|"escalate", memo?: string, snooze_until?: string }` |
| Success | `202` `{ data: { status: "accepted", commandId, inbox_id, disposition }, meta }` |
| Errors | 403, 404, 422 |
| SSE | `agora.inbox` |
| EvidenceKind | `agora.inbox.triage` |
| Permission | `analyst` |
| Acceptance | Probe row = 202 |

---

### Card P1-13 — `POST /bff/agora/skill-coaching` *(new route)*

| Field | Value |
|---|---|
| Probe | `404` |
| Body | `{ skill_id: string, persona_id?: string, prompt: string, expected_behavior?: string, examples?: object[] }` |
| Success | `201` `{ data: { id, skill_id, status: "queued", … }, meta }` |
| Errors | 403, 422 |
| SSE | `agora.skill-coaching` |
| EvidenceKind | `agora.skill_coaching.create` |
| Permission | `coach` / `analyst` |
| Acceptance | Probe row = 201 |

---

### Card P1-14 — `POST /bff/agora/postmortems` *(method add)*

| Field | Value |
|---|---|
| Probe | `405` |
| Body | `{ incident_id?: string, title: string, body: string, root_cause: string, action_items: { owner, due, description }[] }` |
| Success | `201` `{ data: { id, title, status: "draft", created_at, … }, meta }` |
| Errors | 403, 422 |
| SSE | `agora.postmortems` |
| EvidenceKind | `agora.postmortem.create` |
| Permission | `analyst` |
| Acceptance | Probe row = 201 |

---

### Card P2-15 — `POST /bff/v5/interventions/batch-decide` *(new route)*

| Field | Value |
|---|---|
| Probe | `405` |
| Body | `{ items: { intervention_id: string, decision: "approve"\|"reject", memo: string }[], two_man_token?: string }` (max 50 / req) |
| Success | `202` `{ data: { status: "accepted", batchId, accepted: n, rejected: n, items: [{ intervention_id, commandId, status }] }, meta }` |
| Errors | 403 `INSUFFICIENT_PERMISSIONS`, 403 `TWO_MAN_REQUIRED`, 422 `VALIDATION_FAILED` (>50 items, memo <8 chars) |
| SSE | `v5.interventions` (one event per item) |
| EvidenceKind | `v5.intervention.batch_decide` |
| Permission | Same as single decide: `operator` / `approver` / `admin` |
| Acceptance | Probe row = 202 |

---

### Card P2-16 (informational) — Sentinel rule coverage for HealthReasonCode

**Observed**: 13 personas with `health.status=degraded`, `health.score=85`, `reasons=[persona_lifecycle_not_active, no_runtime_binding]` produce **zero** Sentinel findings.

**Expected** (Pack D §D-SentinelRules + v5 SA+SD): every persona health reason code below `healthy` SHOULD emit ≥1 matching Sentinel finding so it surfaces in the Sentinel timeline and triggers HIQ on severity threshold.

**Ask**: Sentinel rule engine adds coverage for the 6 `HealthReasonCode` values from Card P0-8. FE cannot patch — rule engine is BE-side.

**Land at**: Sentinel rule registration site (search for `sentinel_rules` / `register_rule` in `services/sentinel/`). One rule per HealthReasonCode → finding with matching severity.

**Acceptance**: After re-running Sentinel pass on the 13 degraded-personas fixture, ≥13 findings (one per persona) appear in `GET /bff/sentinel/findings?status=open`.

---

## 5. Acceptance / verification flow

Per-card acceptance above. End-of-sprint gate:

```bash
# From execute-plans (FE repo):
node scripts/probe-bff-write-paths.mjs
node scripts/probe-persona-onboarding-endpoints.mjs
```

A row flips to ✅ when:
- `2xx` for a success path, OR
- `4xx` with a Pack D 26-code envelope (NOT `RESOURCE_NOT_FOUND "Not Found"` and NOT `VALIDATION_FAILED "Method Not Allowed"`).

When all 15 rows green:
1. FE removes the corresponding `withWriteFallback` branches in `src/lib/bff-v1/writeFallback.ts` allow-list
2. FE removes `LiveStatusBanner` write-degraded entries
3. FE re-enables the `create_runtime` agent tool
4. Memory entry `mem://audits/bff-write-gap-2026-05-28` is closed

### CI gate matrix

| Route | Method | Expected (live) | Expected (`X-Dry-Run: 1`) |
|---|---|---|---|
| `/bff/personas/{id}/actions/AdvanceLifecycle` | POST | 202 | 200 + `dryRun:true` |
| `/bff/capital-pools/{id}/actions/ApprovePool` | POST | 202 | 200 + `dryRun:true` |
| `/bff/runtimes/{id}/actions/StartRuntime` | POST | 202 | 200 + `dryRun:true` |
| `/bff/command-confirmations/{token}/confirm` | POST | 202 | 200 + `dryRun:true` |
| `/api/v1/bindings` | POST | 201 | 200 + `dryRun:true` |
| `/api/v1/deployment-plans` | POST | 201 | 200 + `dryRun:true` |
| `/api/v1/approval-decisions` | POST | 202 | 200 + `dryRun:true` |
| `/api/v1/operator/persona-management/{id}` | GET | 200 (+`data.health`) | — |
| `/bff/runtimes` | POST | 201 | 200 + `dryRun:true` |
| `/bff/agora/signals` | POST | 201 | 200 + `dryRun:true` |
| `/bff/agora/feedback` | POST | 201 | 200 + `dryRun:true` |
| `/bff/agora/inbox/{id}/triage` | POST | 202 | 200 + `dryRun:true` |
| `/bff/agora/skill-coaching` | POST | 201 | 200 + `dryRun:true` |
| `/bff/agora/postmortems` | POST | 201 | 200 + `dryRun:true` |
| `/bff/v5/interventions/batch-decide` | POST | 202 | 200 + `dryRun:true` |

Recommend wiring this into `.github/workflows/pantheon-integration-gate.yml` as a release-gate step (separate `OPS-INTEGRATION-GATE-WRITE-2026-05-28` follow-up — not in this sprint).

## 6. Sprint structure → EPIC → ticket map

| EPIC | Tickets | Cards | Owner / Reviewer |
|---|---|---|---|
| `EPIC-WRITE-GAP-P0-LIFECYCLE` | 4 | P0-1, P0-2, P0-3, P0-4 | Codex / Claude |
| `EPIC-WRITE-GAP-P0-WIZARD` | 4 | P0-5, P0-6, P0-7, P0-8 | Codex / Claude |
| `EPIC-WRITE-GAP-P1-AGORA` | 6 | P1-9 … P1-14 | Codex2 / Claude2 |
| `EPIC-WRITE-GAP-P2-MISC` | 2 | P2-15, Sentinel rule coverage | Codex / Claude |
| `EPIC-WRITE-GAP-OPS` | 1 | Re-deploy + 15-curl live verify | Codex / Claude |

Owner / reviewer matches the 3-class pattern set by 2026-05-24 delta (P0 / Class C → Codex; P1 Agora batch → Codex2; ops → Codex). Babysit rule from `feedback_babysit_deploy_tasks`: do not declare done until live curl verified.

## 7. Change log

- **2026-06-03** v1.3 — adds Management AI conversation list resync and local-only empty readback metadata for browser-local orphan sessions, preventing stale local session ids from surfacing raw BFF 404 errors.
- **2026-06-03** v1.2 — makes Management AI conversation readback server-side source of truth: persisted sessions/turns, server-history context, attachment proxy URLs, nonexistent-session 404, and idempotency no-duplicate-turn acceptance.
- **2026-06-03** v1.1 — adds P0 Management AI multi-turn backend contract for `/bff/management/nl/ask` and `/bff/management/ai/conversations/{sessionId}`: conversation/UI context pack, session readback, action allowlist, providerStatus, 7-day idle TTL, and live probe coverage.
- **2026-05-28** v1.0 — initial BE-view rewrite of Lovable FE spec `BE_WRITE_GAP_SPEC_2026-05-28` v1.0. Adds repo-map, action-catalog hints for P0-1/2/3, and sprint-to-EPIC ticket map.
