# Memory Service Production Hardening

Last updated: 2026-04-30

This note records the production hardening scope for the memory service.

## Retrieval Authorization

`GET /api/memory/retrieve` is the read facade. It performs a governance AuthZ check before accessing the institutional memory store.

Configuration:

- `PANTHEON_GOVERNANCE_AUTHZ_URL`, `PANTHEON_GOVERNANCE_API_URL`, or `PANTHEON_GOVERNANCE_SERVICE_URL` points to governance.
- `PANTHEON_GOVERNANCE_AUTH_TOKEN` is optional and is sent as a bearer token when present.
- If no governance endpoint is configured, retrieval fails closed with `governance_authz_unconfigured`.

The local-only `PANTHEON_MEMORY_AUTHZ_MODE=local` path is for focused tests and single-process development. Production deployments should use the governance endpoint.

## Retention

Institutional entries support durable archival rather than deletion:

- `PANTHEON_MEMORY_RETENTION_DAYS` defaults to `365`.
- New entries without an explicit `expires_at` receive one based on `written_at`.
- `indefinite`, `never`, `none`, or an empty value disables create-time expiration.
- Expired active entries are marked with `archived_at` and `archived_reason`.
- Archived and superseded entries remain persisted for lineage and replay but are excluded from active list and retrieval calls.

## Replay Coverage

Focused replay coverage verifies that an institutional memory write persists, survives store reload, is retrievable through the governed facade, increments `reuse_count`, and excludes expired archived records from active retrieval while keeping them available through `active_only=false`.

Verification command:

```bash
python3 services/memory/smoke_test_institutional_memory.py
```
