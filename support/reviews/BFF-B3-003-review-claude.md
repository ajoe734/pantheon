# Review: BFF-B3-003 — GET /bff/management/human-inbox aggregate and detail

Reviewer: Claude
Date: 2026-05-23
Status: APPROVED

## Scope

Reviewed PR #448 (commit 31e66ca2) against:
- `docs/04/pantheon_bff_api_gap_2026-05-23/BFF_API_GAP_final_integration_spec.md#b34-get-bffmanagementhuman-inbox`
- Acceptance criteria 1–5 in §B3.4

## Artifacts Verified

| File | Result |
|---|---|
| `services/control-plane/bff/main.py` | ✅ Routes correct, auth gate, composition logic |
| `services/control-plane/bff/tests/test_bff_b3_human_inbox.py` | ✅ 4/4 tests pass |
| `execute-plans/src/lib/bff-v1/paths.ts` | ✅ `managementHumanInbox()` and `managementHumanInboxItem(id)` added |
| `execute-plans/src/lib/bff/client.ts` | ✅ `humanInbox.list()` and `humanInbox.get()` wired via `withStrictLiveOrMock` |
| `execute-plans/src/lib/bff-v1/management.ts` | ✅ Types updated for cockpit sections |

## Acceptance Criteria Assessment

| # | Criterion | Verdict |
|---|---|---|
| 1 | `GET /bff/management/human-inbox` composes approval queue and v5 interventions | PASS — `_human_inbox_all_items()` merges both sources |
| 2 | Response: `data`, `items`, `summary`, `page_info`, `meta.surfaces.human_inbox` | PASS — all fields present in route handler |
| 3 | `source_type`, `status`, pagination filters and detail lookup | PASS — `_human_inbox_filter_items()`, `_page_slice()`, `_human_inbox_detail_match()` |
| 4 | Anonymous returns HTTP 401 typed BFF error envelope | PASS — `_require_read_role` gate confirmed by test |
| 5 | Frontend path/client contract without seed-list fanout | PASS — `withStrictLiveOrMock` with empty aggregate fallback, no mock seed list |

## Backend Review Notes

- Priority normalization handles severity aliases (`sev1→critical`, `sev2→high`) — correct.
- Items sorted by descending priority rank, then `created_at`, then `id` — reasonable UX.
- Detail match uses multi-key lookup (`id`, `inbox_id`, `source_id`, `approval_decision_id`, `intervention_id`) — future-proofs both prefixed and bare IDs.
- `_human_inbox_surfaces()` correctly upgrades intervention surface to `bff_local_registry` when records exist.
- `_build_management_cockpit_payload` correctly embeds the human_inbox aggregate and links to `/bff/management/human-inbox`.

## Frontend Review Notes

- `HumanInboxItem`, `HumanInboxAggregate`, `HumanInboxQuery` types are well-defined in client.ts.
- `adaptHumanInboxAggregate` and `adaptHumanInboxDetail` provide safe unknown→typed projection.
- `emptyHumanInboxAggregate()` fallback preserves the strict no-seed-fanout contract.
- Frontend environment lacks installed node_modules in this worktree; tests are structurally correct and match the backend contract.

## Verdict

All 5 acceptance criteria satisfied. Backend tests pass. Implementation is clean and follows the established pattern from BFF-B3-001/002. Approved for owner finalization.
