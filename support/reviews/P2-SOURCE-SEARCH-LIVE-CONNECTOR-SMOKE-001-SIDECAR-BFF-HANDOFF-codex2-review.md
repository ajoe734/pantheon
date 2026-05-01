# Review: P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001-SIDECAR-BFF-HANDOFF

**Task:** `P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001-SIDECAR-BFF-HANDOFF`
**Reviewer:** Codex2
**Owner:** Claude
**Date:** 2026-05-01
**Disposition:** approved with parent-task caveats

## Scope Check

The handoff packet stays within sidecar scope:

- It creates support material only.
- It does not modify L1 canonical truth, core contracts, registry/runtime implementation, or governance policy.
- It is clearly framed as input for the parent owner and frontend/BFF follow-up decisions.

## Verification Performed

Focused source/BFF/search references were checked:

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/read_store.py`
- `services/source_ingestion/main.py`
- `services/source_ingestion/connectors/base.py`
- `services/search/gateway.py`
- `services/openclaw-gateway-adapter/main.py`

Commands:

```bash
rg -n "source-connectors|research/search|operator/source|dlq|source-search-health|openclaw.*audit|source-records|jobs" services/control-plane/bff services/source_ingestion services/search services/openclaw-gateway-adapter docs/deployment/source-search-prod-hardening.md
sed -n '9250,9355p' services/control-plane/bff/read_store.py
sed -n '8520,8668p' services/control-plane/bff/main.py
sed -n '8868,9005p' services/control-plane/bff/main.py
sed -n '815,956p' services/source_ingestion/main.py
sed -n '1,240p' services/search/gateway.py
```

## Findings

No sidecar-blocking issue found. The packet is useful and accurate enough for parent-task handoff.

Two caveats should follow the parent owner:

1. GAP-04 is best read as "no dedicated BFF DLQ preview endpoint" rather than "no DLQ preview before replay." The current BFF `GET /api/v1/operator/source/ops` already composes DLQ entries from source-ingest `GET /api/source-ingest/dlq`, including optional `dlq_status`.
2. Step 5 uses `GET /api/source-ingest/source-records?connector_id=...`, but the source-ingest list endpoint currently accepts no `connector_id` query parameter. Operators can list records and filter client-side today, or the parent can add a narrow server-side filter.

## Approval

Approved for owner finalization. These caveats should be preserved when the parent task decides which BFF gaps are blocking versus follow-up.
