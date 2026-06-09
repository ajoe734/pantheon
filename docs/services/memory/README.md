# Memory Service Production Hardening

Last updated: 2026-04-30

This note records the production hardening scope for the memory service.

## Retrieval Authorization

`GET /api/memory/retrieve` is the read facade. It performs a governance AuthZ check before accessing the institutional and persona memory stores.

Configuration:

- `PANTHEON_GOVERNANCE_AUTHZ_URL`, `PANTHEON_GOVERNANCE_API_URL`, or `PANTHEON_GOVERNANCE_SERVICE_URL` points to governance.
- `PANTHEON_GOVERNANCE_AUTH_TOKEN` is optional and is sent as a bearer token when present.
- If no governance endpoint is configured, retrieval fails closed with `governance_authz_unconfigured`.

The local-only `PANTHEON_MEMORY_AUTHZ_MODE=local` path is for focused tests and single-process development. Production deployments should use the governance endpoint.

Persona memory retrieval is scoped by `persona_id`. Persona-session style actors must provide matching `session_persona_id`; operator/admin/reviewer/auditor reads are authorized for a specified `persona_id` through governance. Consultation sessions only receive persona entries marked `relevance_scope=persona_and_committee`.

## Persona Memory

Persona memory is first-class in the memory service:

- `POST /api/memory/persona-entries` stores a canonical `PersonaMemory` entry.
- `POST /api/memory/writebacks/persona` is the writeback trigger entrypoint for lifecycle events.
- `GET /api/memory/retrieve?scope=persona|both` returns persona-scoped hits and increments `reuse_count` for returned entries.

JSON mode uses `PANTHEON_PERSONA_MEMORY_STORE` when set, otherwise `PANTHEON_MEMORY_DATA_DIR/persona_memory_entries.json`. `PANTHEON_PERSONA_MEMORY_STORE_BACKEND` defaults to `PANTHEON_MEMORY_STORE_BACKEND`; Postgres mode defaults to table `memory.persona_memory_entries`.

## Retention

Institutional entries support durable archival rather than deletion:

- `PANTHEON_MEMORY_RETENTION_DAYS` defaults to `365`.
- New entries without an explicit `expires_at` receive one based on `written_at`.
- `indefinite`, `never`, `none`, or an empty value disables create-time expiration.
- Expired active entries are marked with `archived_at` and `archived_reason`.
- Archived and superseded entries remain persisted for lineage and replay but are excluded from active list and retrieval calls.

## Replay Coverage

Focused replay coverage verifies that institutional and persona memory writes persist, survive store reload, are retrievable through the governed facade, increment `reuse_count`, and exclude expired archived institutional records from active retrieval while keeping them available through `active_only=false`.

Verification command:

```bash
python3 services/memory/smoke_test_institutional_memory.py
```
