# Review: SVC-MEMORY-AUTHZ-RETENTION-REPLAY-HARDENING

**Reviewer:** Claude  
**Date:** 2026-04-30  
**Decision:** APPROVED

---

## Scope Reviewed

- `services/memory/institutional_memory_store.py` — `InstitutionalRetentionPolicy`, `archive_expired`, `expires_at`/`archived_at`/`archived_reason` fields on `InstitutionalMemoryEntry`
- `services/memory/main.py` — `_authorize_memory_retrieve`, `_governance_authz_url`, `GET /api/memory/retrieve` fail-closed facade with `mark_reused` writeback
- `services/governance/authz.py` — `evaluate_authz_request` with persona-isolation and role-based deny rules
- `services/governance/main.py` — `POST /api/governance/authz/check` endpoint
- `services/governance/models.py` — `AuthzCheckRequest` / `AuthzCheckResponse` Pydantic models
- `services/memory/test_institutional_memory_store.py` — retention, archive-on-read, persistence, retrieval ranking
- `services/memory/test_main.py` — authz fail-closed, cross-persona reject, governance URL integration, reuse_count increment
- `services/governance/test_governance_api.py` — governance authz endpoint, full lifecycle, write-authority enforcement
- `services/memory/smoke_test_institutional_memory.py` — S7 governed retrieval replay end-to-end
- `services/memory/MEMORY_LAYER_DESIGN_NOTE.md` — retention section, production hardening note in §5.1, resolved open items in §9

---

## Acceptance Criteria Verification

| # | Criterion | Result |
|---|---|---|
| 1 | memory retrieval enforces governance authz or explicit deny boundary | ✅ PASS — `_authorize_memory_retrieve` in main.py calls governance HTTP endpoint or local `evaluate_authz_request`; when neither configured, returns `governance_authz_unconfigured` deny; `test_retrieve_fails_closed_when_governance_authz_unconfigured` verifies HTTP 403 |
| 2 | retention TTL or archive policy is implemented and documented | ✅ PASS — `InstitutionalRetentionPolicy.from_env()` assigns `expires_at` on create; `archive_expired()` archives expired entries with `archived_at`+`archived_reason`; `list(active_only=True)` calls `archive_expired()` on read; env var `PANTHEON_MEMORY_RETENTION_DAYS` documented in MEMORY_LAYER_DESIGN_NOTE.md §7 Retention subsection |
| 3 | golden replay includes memory writeback and retrieval path | ✅ PASS — smoke S7 writes entry via `/api/memory/entries`, reloads store, calls `/api/memory/retrieve` with local authz, verifies `reuse_count=1` durably persisted; all 17 smoke checks pass |
| 4 | unauthorized persona session access is rejected | ✅ PASS — `evaluate_authz_request` returns `persona_scope_mismatch` when session_persona_id ≠ persona_id; `test_retrieve_rejects_cross_persona_session_access` verifies HTTP 403 with correct reason; governance API test `test_authz_rejects_cross_persona_session_memory_retrieval` independently confirms |
| 5 | focused memory and replay tests pass | ✅ PASS — 57 tests pass across `test_institutional_memory_store.py`, `test_main.py`, `test_governance_api.py`; 11 BFF contract tests pass; 17 smoke checks pass |
| 6 | docs no longer list authz retention replay as unowned open items | ✅ PASS — MEMORY_LAYER_DESIGN_NOTE.md §9 marks all three items (Retention/TTL, Cross-plane replay, Auth/RBAC) as "Resolved by production-hardening slice"; §5.1 updated with production hardening note |

---

## Verification Commands Run

```
python3 services/memory/smoke_test_institutional_memory.py
  → Summary: 17 passed, 0 failed

python3 -m pytest services/memory/test_institutional_memory_store.py services/memory/test_main.py services/governance/test_governance_api.py -q
  → 57 passed in 1.85s

python3 -m pytest services/control-plane/bff/test_kw01_institutional_memory_contract.py services/control-plane/bff/test_read_store_service_clients.py -q
  → 11 passed in 2.67s
```

---

## Implementation Quality Notes

**Positive observations:**

- The fail-closed pattern is correct: when `PANTHEON_MEMORY_AUTHZ_MODE != "local"` and no governance URL is configured, retrieval returns HTTP 403 immediately — no implicit allow fallback.
- The `PANTHEON_MEMORY_AUTHZ_MODE=local` path calls the same `evaluate_authz_request` logic used by the governance HTTP endpoint, ensuring local and remote evaluation are consistent.
- `archive_expired()` is called lazily inside `list(active_only=True)`, which means the retrieve path also archives expired entries on read — appropriate archive-on-read behavior with no background job required.
- `InstitutionalRetentionPolicy.from_env()` is called fresh on each `create()` invocation, picking up policy changes without a service restart for new entries.
- `mark_reused()` is called inside the retrieve endpoint after auth is confirmed, correctly updating `reuse_count` before projecting the hit into the response — callers see the already-incremented count.
- Persona-isolation in `evaluate_authz_request` is strict: consultation_session roles must also satisfy a `relevance_scope=persona_and_committee` resource field — the secondary gate is in place.
- `PostgresInstitutionalMemoryStore` mirrors the same retention and archive behavior through the base class, so Postgres backend is consistent.

**No blocking issues found.**

---

## Open Items (Non-Blocking)

- Persona memory retrieval path (`scope=persona`) is not yet wired to an actual PersonaMemory store; the facade responds with an empty `hits` list for persona scope. This is pre-existing and correctly noted as a future merge-in in MEMORY_LAYER_DESIGN_NOTE.md §5.1.
- The HTTP-mode `_authorize_memory_retrieve` makes a synchronous blocking HTTP call with a 2-second timeout. At high retrieve volume this may become a latency concern; current posture is correct for the production-hardening phase.

---

## Decision

All six acceptance criteria are met. The fail-closed authz boundary, retention TTL with archive-on-read, governed retrieval replay, and cross-persona isolation are correctly implemented and tested. Docs reflect resolved state.

**APPROVED** — return to Codex2 for closeout finalization.
