# Review: EXEC-BFF-KW03-001 — KW-03 Evidence Refs BFF Routes

**Reviewer**: Claude
**Date**: 2026-04-21
**Task**: EXEC-BFF-KW03-001 — Implement KW-03 evidence refs BFF routes from the ratified contract
**Decision**: APPROVED

---

## Evidence Examined

1. `docs/bff/KW-03-evidence-refs.md` — canonical contract
2. `docs/examples/KW-03-evidence-refs.json` — example payloads
3. `services/control-plane/bff/main.py:7403-7552` — list and detail routes
4. `services/control-plane/bff/read_store.py:4612-4840` — projection helpers and store methods
5. `services/control-plane/bff/test_kw03_evidence_refs_contract.py:238-358` — contract tests
6. `support/sidecars/EXEC-BFF-KW03-001/EXEC-BFF-KW03-001-SIDECAR-BFF-HANDOFF.md` — BFF handoff packet

---

## Route Coverage

| Route | Location | Status |
|---|---|---|
| `GET /api/v1/knowledge/evidence` | `main.py:7403-7507` | live |
| `GET /api/v1/knowledge/evidence/{ref_id}` | `main.py:7510-7552` | live |

Both routes enforce auth (`_require_read_role`) before serving any data.

---

## Contract Compliance

### List route
- All contract query params present: `linked_entity_type`, `linked_entity_ref`, `link_type`, `credibility_tier`, `verified`, `page_token`, `page_size` (default 20, max 100)
- `linked_entity_ref` without `linked_entity_type` correctly returns 400 with `precondition_failed` detail
- Response shape matches the contract: `evidence_refs[]`, `pagination`, `meta.surfaces.evidence_refs_list`
- List items carry all required fields: `ref_id`, `source_document`, `link_type`, `credibility` (list-level), `linked_object_summary`, `resolved_link`, `route_href`
- Surface state is derived from dataset availability, not from empty arrays — correct

### Detail route
- Returns 404 `OBJECT_NOT_FOUND` for unknown `ref_id`
- Full `source_document` detail including `excerpt`, `storage_preview`, `captured_by`
- Full `credibility` with `last_verified_at` and `verification_method`
- `linked_decisions[]` resolved BFF-side with `display_label`, `route_href`, `link_type`, `relationship_note`
- `source_note_context` and `source_memory_context` both nullable, typed correctly
- Per-panel surface state: `evidence_ref_detail`, `resolved_link`, `linked_decisions`

### BFF-owned link resolution (`read_store.py:4672-4689`)
- `availability` normalized to `available | unavailable | external`; unknown values default to `unavailable`
- `unavailable` correctly forces `route_href = null`
- `external` correctly enforces `open_in_new_tab = true`
- Frontend receives only resolved values; no raw `ref_id`, `source_ref`, or storage prefix is exposed for URL construction

### Credibility semantics
- List projection: `tier` + `verified` only
- Detail projection: adds `last_verified_at` + `verification_method`
- `tier` defaults to `unverified` when absent; `verified` coerced to bool

### Service-backed fallback (`read_store.py:4807-4838`)
- Both list and detail use `_service.record("evidence_refs", ...)` with `_local_fallback` on unavailability
- Sorted by `captured_at` descending with `ref_id` as tiebreak

---

## Test Verification

The sidecar confirms `10 passed` on:
```
pytest -q services/control-plane/bff/test_kw03_evidence_refs_contract.py \
       services/control-plane/bff/test_kw02_research_notes_contract.py \
       services/control-plane/bff/test_pkt016_knowledge_workbench_contract.py
```

Tests cover:
- Degraded-fallback list/detail behavior with correct surface state
- Service-backed filters (`linked_entity_type`, `verified`)
- External-link semantics (`availability=external`, `open_in_new_tab=true`)
- Empty filter result with `ok` surface state (not `degraded`)
- Invalid `linked_entity_ref` rejection (400)

---

## Acceptance Criteria Verdict

| Criterion | Result |
|---|---|
| KW-03 list / detail routes with BFF-owned `resolved_link` / `linked_decisions` | ✅ live at canonical paths |
| Frontend cannot construct URLs; `availability` / `credibility` semantics preserved | ✅ all resolution is BFF-owned |
| Contract verification and next-action truth | ✅ 10 tests pass; sidecar documents residual drift |

---

## Residual Items (do not block approval)

The sidecar correctly identifies three non-blocking follow-up items:

- **DRIFT-KW03-001**: `KW-006` packet family still marks KW-03 BFF as pending — narrative sync needed
- **DRIFT-KW03-002**: `MODULE_READINESS_RATIFICATION_2026-04-20.md`, overview route, and overview examples still say `contract_ready` — narrative sync needed
- **GAP-KW03-003**: No frontend handoff bundle at `docs/pantheon-handoffs/KW-03-evidence-refs/` — frontend activation not started

None of these reopen the BFF implementation. They belong to a readiness/overview sync slice or a separate frontend activation task.

---

## Decision

The KW-03 route family is implemented and contract-compliant. All acceptance criteria are met. The residual items are correctly classified as non-blocking. Task is approved and returned to the owner for finalization.
