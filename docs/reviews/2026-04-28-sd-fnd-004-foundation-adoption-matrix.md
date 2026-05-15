# SD-FND-004 Foundation Adoption Matrix

Date: 2026-04-28
Owner: Codex2
Reviewer: Claude

## Scope

This matrix inventories command, governance, promotion, deployment, evidence,
and consultation paths against the shared foundation primitives from
`services/foundation`: `TraceContext`, `CommandEnvelope`, `IdempotencyRecord`,
`PolicyDecision`, `AuditAction`, `ErrorEnvelope`, outbox, DLQ, and schema
registry.

## Adoption Status

| Path | Current status | Foundation evidence | Owner | Risk | Required follow-up evidence |
|---|---|---|---|---|---|
| BFF operator command admission (`POST /api/v1/operator/commands`) | Pilot-complete | Builds `TraceContext`, `CommandEnvelope`, `IdempotencyRecord`, `PolicyDecision`, `AuditAction`, and returns `ErrorEnvelope` on validation / policy / idempotency errors. | Codex | Medium: broad operator write surface | Keep command-specific validator tests paired with foundation error-envelope assertions. |
| Runtime-manager kill-switch dispatch | Pilot-complete | Preserves upstream foundation trace, records command/idempotency/policy/audit context, and returns replay evidence. | Claude | High: execution control path and crash windows | SD-FND-005 owns durable recovery / corrupt-state evidence. |
| Deployment plan dispatch (`POST /api/deployment/plans/{plan_id}/dispatch`) | Newly adopted in SD-FND-004 | Builds shared foundation command context, persists serialized foundation evidence in deployment saga metadata, uses foundation trace on saga outbox events, replays matching idempotency keys, rejects idempotency conflicts with `ErrorEnvelope`, and rejects unapproved dispatch through policy-denial envelope. | Codex2 | Medium: deployment handoff path, but response shape is preserved and evidence is metadata-only | Maintain `services/deployment/test_service.py` coverage for success, replay/idempotency conflict, and policy-denial envelope. |
| Deployment saga progress (`binding-created`, `runtime-active`, `failure`, compensation finalize) | Deferred | Local DEP-002 outbox/inbox exists, but progress commands do not yet build shared `CommandEnvelope` / `AuditAction`. | Claude / Codex | Medium: cross-service write ordering | Add foundation context per progress command and prove duplicate/out-of-order receipts still hold. |
| Governance approval-decision module | Deferred | Domain-level validation and tests exist, but command admission currently relies on BFF pilot rather than module-owned foundation context. | Claude | Medium: approval write semantics | Add module-level command envelope or explicitly document BFF as the only write admission owner. |
| Governance deployment-plan creation / status update | Deferred | Deployment service validates status transitions locally; no shared envelope or error envelope on create/status write paths. | Codex2 | Medium: promotion/deployment plan lifecycle | Add trace/idempotency/audit to plan creation and status update with conflict tests. |
| Promotion registry / artifact promotion | Deferred | Registry promotion contracts and tests exist; foundation primitives are not yet applied to promotion writes. | Qwen / Codex | High: promotion gates feed deployment | Add promotion command envelope, schema registry validation, and audited rejection tests. |
| Capital write authority / binding changes | Deferred | Capital service has local audit/write-authority logic; no shared foundation context. | Claude | High: write-owner and capital boundaries | Adopt foundation `AuthorityScope` and `AuditAction`; prove denied writes return stable envelope. |
| Source/evidence/search first-slice writes | Deferred | SD-SRC-EVIDENCE-001 introduced governed evidence metadata; durable store work is dependency-gated on foundation primitives. | Copilot | Medium: evidence refs must stay stable | SD-SRC-EVIDENCE-002 should use shared audit/outbox/replay where service-owned persistence fits. |
| Source ingestion scheduler / watermarks | Deferred | Not yet materialized; packet requires retry/DLQ behavior. | Gemini | Medium: ingestion replay and poison records | SD-SRC-EVIDENCE-003 should use shared DLQ/audit paths for failed ingestion records. |
| Consultation service lifecycle writes | Deferred | Service owns consultation records but still loads lifecycle tables in memory and emits synthetic actors in some audit events. | Claude2 | Medium: actor fidelity and immutable memo publication | SD-CONSULT-002 should persist lifecycle with foundation audit/outbox/replay where applicable. |
| BFF/runtime consultation workflows | Deferred | BFF/runtime surfaces are not yet wired to consultation service as the authoritative boundary. | Gemini | Medium: shadow lifecycle assumptions | SD-CONSULT-003 should preserve response shape while pointing handoffs to service-owned ids/evidence/audit refs. |
| Telemetry ingest DLQ | Intentionally excluded from this rollout | Existing telemetry DLQ is service-owned per SD-FND-003 review; foundation docs explicitly avoid weakening telemetry storage semantics. | Claude | High: telemetry shock absorption already has specialized semantics | Bridge only through explicit adapter task if canonical telemetry policy requires it. |

## SD-FND-004 Evidence

- Newly adopted path: deployment plan dispatch.
- Code paths:
  - `services/deployment/service.py`
  - `services/deployment/models.py`
  - `services/deployment/test_service.py`
- Targeted tests:
  - `test_dispatch_records_foundation_context_and_replays_idempotently`
  - `test_dispatch_rejects_idempotency_key_reuse_with_foundation_error`
  - `test_dispatch_unapproved_plan_returns_foundation_policy_error`
