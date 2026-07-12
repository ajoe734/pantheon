# AG-GAP-006: Migrate identity/personalization/shadow routes out of main.py

## Scope

`bff/agora/identity/router.py`, `personalization/router.py`, and
`shadow/router.py` are empty shells (`return APIRouter()` with zero routes).
The real handlers for sessions/ask/inbox/handoffs (identity family) and
memory/insights (personalization family) live in the 58k-line
`services/control-plane/bff/main.py`, registered first so they win route
resolution. `create_agora_router()` is mounted last to avoid conflicts
(main.py:58704-58715). This dual-track architecture is the largest ongoing
maintenance cost in the Agora backend.

## Work

1. Move the Agora-scoped handlers from main.py into their sub-routers,
   domain by domain (identity first, then personalization, then shadow),
   behavior-preserving — same paths, same envelopes, same store access.
2. Remove the migrated registrations from main.py in the same PR that adds
   them to the sub-router, so no path is double-registered.
3. Shadow has schema but no routes anywhere; migration for shadow means
   documenting that state in the sub-router docstring, not inventing routes.

## Acceptance

- `identity/router.py` and `personalization/router.py` own their documented
  route families; the corresponding main.py blocks are gone.
- `test_bff_agora_core_contract.py`, `test_bff_agora_extended_contract.py`,
  `test_agora_journal_merge_patch.py`, `test_bff_b2_005_agora_canonical_aliases.py`
  pass unchanged (contract stability is the gate).
- Live smoke on dev after deploy: `/bff/agora/sessions`, `/bff/agora/inbox`,
  `/bff/agora/memory`, `/bff/agora/insights` return the same shapes as before.
- No behavior changes; this is a pure move. Any bug found en route becomes a
  separate task, not a drive-by fix.

## References

- `services/control-plane/bff/agora/router.py:5-24,169-182`
- `services/control-plane/bff/main.py:58704-58715`
- `services/control-plane/bff/agora/identity/router.py:7-41`
