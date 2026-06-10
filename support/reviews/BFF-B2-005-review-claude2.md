# Review: BFF-B2-005 — Agora canonical aliases (B7 6 endpoints)

Reviewer: Claude2
Date: 2026-05-23
Task: BFF-B2-005
PR: #428 (merged to dev at 74de84d8a21f372020a86ec4c07a2c647b8443f7)

## Review Outcome: APPROVED

## Scope Verified

Checked all six B7 Agora compatibility endpoints from the spec
(`docs/04/pantheon_bff_api_gap_2026-05-23/BFF_API_GAP_final_integration_spec.md#b7--agora-compatibility-apis`):

| Endpoint | Line in main.py | Status |
|---|---|---|
| `GET /bff/agora/ask/sessions` | 25082 | ✅ |
| `GET /bff/agora/ask/sessions/{sessionId}` | 25138 | ✅ |
| `GET /bff/agora/signals` | 15355 | ✅ |
| `GET /bff/agora/journal` | 15861 | ✅ |
| `GET /bff/agora/postmortems` | 25655 | ✅ |
| `GET /bff/agora/inbox` | 25076 | ✅ |

## Acceptance Criteria Verified

1. ✅ `/bff/agora/ask/sessions` uses `_sem_list_payload` with `filter_mode="quick_ask"`, correctly scoping to `mode=quick_ask` sessions. Detail route at `/{sessionId}` returns 404 for non-ask sessions.
2. ✅ `/bff/agora/signals`, `/bff/agora/journal`, `/bff/agora/postmortems` all return BFF read envelopes with `data`/`items` equality, `page_info.next_page_token=null`, and surface metadata.
3. ✅ `/bff/agora/inbox` composes `insight_cards` + `agora_signals` + `research_tickets` with per-source `inboxType` tags, surface metadata for each dataset, and `meta.composition.itemCounts`.
4. ✅ Historical alias routes (`/bff/agora/markets`, `/bff/agora/committee-sessions`, `/bff/agora/market-notes`, `/bff/agora/decision-journal`, `/bff/agora/research-tasks`, `/bff/agora/incoming`) share canonical handler outputs and surface metadata with their counterparts.

## Test Evidence

Ran focused test suite:

```
pytest services/control-plane/bff/test_bff_b2_005_agora_canonical_aliases.py -v
2 passed in 2.14s
```

- `test_bff_b2_005_agora_aliases_share_canonical_read_surfaces` — PASSED
- `test_bff_b2_005_agora_core_routes_return_envelopes_and_composite_inbox` — PASSED

## Notes

- Implementation is clean: `_sem_list_payload` and `_sem_agora_inbox_payload` helpers are reused without duplication.
- The inbox sort key (`_sem_inbox_sort_value`) handles both camelCase and snake_case date fields correctly.
- No regressions observed in surface metadata shape or alias route parity.

LLM-Agent: Claude2
Task-ID: BFF-B2-005
