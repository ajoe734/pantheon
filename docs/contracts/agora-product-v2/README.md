# Agora Product v2 Authoritative Contracts

Program ID: `agora-product-correction-20260813`
Task ID: `AGORA-BE-INTEGRATION-20260813`
Status: Canonical product v2 contract baseline

## 1. Overview and Authority Boundaries

Agora Product v2 governs the user journey from Strategy Workshop hypothesis creation through Strategy Reconstruction, immutable StrategySpec draft/versioning, governed Research, real Candidate Pool generation, Trading Room Workspace compilation, Decision Event projections, request-only Trading Intent creation, owner-scoped Strategy Performance indexing, eligible Dataset extraction, Policy-learning candidate admission, and independent Consultation review.

### Absolute Negative Boundaries:
1. **No Broker Order Authority**: Agora components never place live broker orders or mutate broker execution state directly.
2. **No Live Capital Binding**: Agora actions create request-only handoffs; no `RuntimeBinding` or `CapitalBinding` is established without separate control-plane governance approval.
3. **Strict Multi-Tenant and User Isolation**: Every aggregate, receipt, event, and query is partitioned by `(tenant_id, user_id)`. Cross-tenant requests fail closed with 404 (`OWNER_SCOPE_NOT_FOUND`) to prevent resource enumeration.
4. **Command Receipt Model**: All mutations require idempotency keys and return standard command receipts with CAS expected-revision tracking. Replays with identical request hashes return existing receipts; reused keys with different payloads return `IDEMPOTENCY_KEY_REUSED`.
5. **No Client-Derived Truth or Synthetic Completeness**: Completeness and readiness are calculated deterministically by server workers; client attempts to write completeness are rejected.
6. **No Production Fixture Fallbacks**: Candidate pools without eligible completed research return empty pools with explicit exclusion reasons, never hardcoded dummy candidates.
7. **Independent Consultation Review**: Policy learning candidate producers cannot self-attest or auto-approve Consultation memos. The evaluator identity must be independent from the candidate author.

## 2. Contract Files

- `agora_v2_capability_manifest.json`: Audience-filtered capability registrations across 8 subsystems.
- `agora_v2_bundle_index.json`: Schema definitions for scope envelopes, command envelopes, receipts, reconstructions, decision events, and intents.
- `agora_product_v2.openapi.yaml`: Complete OpenAPI 3.1.0 specification for BFF and downstream domain endpoints.
