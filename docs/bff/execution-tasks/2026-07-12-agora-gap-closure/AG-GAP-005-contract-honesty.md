# AG-GAP-005 — Workshop contract honesty

Status: implementation deferred; runtime remains fail-closed

The six workshop operations below were published in the additive v1.1
OpenAPI before their backing version, research, consultation, and conclusion
services existed. The BFF registers each route but returns the standard
non-retryable `501 NOT_IMPLEMENTED` envelope. They are not production-ready
capabilities and clients must not interpret their presence in OpenAPI as an
availability promise.

| Operation | Disposition | Required implementation boundary |
|---|---|---|
| `GET /bff/agora/workshops/{workshop_id}/versions` | deferred | durable StrategySpec version projection and tenant-scoped reads |
| `POST /bff/agora/workshops/{workshop_id}/versions` | deferred | version creation, ETag CAS, idempotency, and registry linkage |
| `POST /bff/agora/workshops/{workshop_id}/versions/{version_id}/select` | deferred | version ownership validation and atomic selected-version update |
| `POST /bff/agora/workshops/{workshop_id}/research-runs` | deferred | governed research dispatcher and workshop event projection |
| `POST /bff/agora/workshops/{workshop_id}/consultations` | deferred | consultation service linkage and lifecycle projection |
| `POST /bff/agora/workshops/{workshop_id}/conclude` | deferred | final-version validation and atomic terminal transition |

The frozen v1.1 artifact is retained byte-for-byte for its published bundle
hash. This disposition supersedes the successful-response implication for
runtime readiness until a follow-up implementation task supplies tests for
authorization, cross-tenant isolation, ETag/idempotency behavior, persistence,
and non-501 success responses.

The dev compatibility manifest is refreshed against the v1.5 additive bundle,
OpenAPI, and capability manifests. `pending` remains the honest state until an
`execute-plans` v1.5 generated-type snapshot and concrete frontend runtime
commit are supplied; deployment gating continues to fail closed.
