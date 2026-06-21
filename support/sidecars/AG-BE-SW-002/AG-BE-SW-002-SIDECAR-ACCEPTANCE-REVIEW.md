# AG-BE-SW-002-SIDECAR-ACCEPTANCE — Review Notes

- Reviewer: Claude2
- Review date: 2026-06-21
- Task: AG-BE-SW-002-SIDECAR-ACCEPTANCE
- Owner: Claude
- Decision: **APPROVED**

## Review Verdict

Dependency map, architecture constraints, and acceptance checklist verified
against the codebase. The packet correctly reflects the AG-BE-SW-001 delivered
surface, the three `501` stubs, and the Registry ownership boundary. Approved
with two implementation annotations for the parent task owner (Claude2).

## Checklist Pass / Fail

| Reviewer question | Result | Notes |
|---|---|---|
| Support-only boundary preserved | PASS | Only `support/sidecars/AG-BE-SW-002/AG-BE-SW-002-SIDECAR-ACCEPTANCE.md` created. `git status --short` shows zero modified canonical files. |
| Dependency map correct | PASS | Linear chain `AG-BE-ID-001 → AG-BE-SW-001 → AG-BE-SW-002` with three downstream dependents (SW-003, SW-004, AG-FE-SW-002) matches `ai-status.json` task graph. |
| AG-BE-SW-001 delivered surface accurate | PASS | Router stubs and store tables listed in §Existing Stubs match the committed `router.py` and `store.py` in `task/AG-BE-SW-001`. |
| StrategySpec truth-in-Registry constraint correct | PASS | Constraint 1 prohibits a second StrategySpec store. `workshop_projection.py` is read-only on Registry. Aligns with `PERSONA_RUNTIME_MODEL.md` Registry ownership rules. |
| Patch-as-new-Registry-version sequence correct | PASS | Constraint 2's six-step sequence is consistent with the Registry draft-create path already used by other services; no novel write path invented. |
| No-order-route prohibition correct | PASS | Constraint 3 explicitly prohibits `RuntimeBinding` creation and live/canary broker orders from any of the three new routes. |
| Idempotency/ETag contract correct | PASS | Constraint 4 requires `Idempotency-Key` and `If-Match` on both mutating routes, matching the ETag pattern from AG-BE-SW-001 and SD §17.2. |
| Patch format matches VersionPatchProposal | PASS | Constraint 5 JSON example with `path`/`from`/`to`/`base_version_id`/`reason` matches the result-synthesis SPEC `proposedVersionPatches` shape. |
| Version-compare read-only | PASS | Constraint 6 explicitly prohibits state writes; diff keyed by JSON path with `before`/`after`/`type`. |
| Acceptance checklist complete | PASS | Sections A–E cover architecture, BFF routes, no-order-route, tests, and bundle immutability; all checkboxes are defined. |
| SD reference map accurate | PASS | Five SD references resolve to existing documents at the stated paths. |
| Bundle immutability items correct | PASS | Section E guards four bundle files and the v1.1 OpenAPI YAML; additive v1.2 path is the only allowed extension. |

## Implementation Annotations for Parent Owner (Claude2)

These two items are flagged as pre-implementation checkpoints before starting
AG-BE-SW-002. They do not block sidecar acceptance; they are advisory notes
for the parent task owner.

### Annotation 1 — Verify Registry draft-create path before wiring `POST /versions`

The packet (Constraint 2, step 4) requires that `POST /bff/agora/workshops/{id}/versions`
delegates to the **existing** Registry draft-create path in `services/research/`.

Before implementing the BFF route, confirm that:

- A usable draft-create function or endpoint exists in
  `services/research/strategy_spec/` (e.g. `registry.py`, `store.py`, or a
  helper exported from `models.py`).
- It accepts a StrategySpec dict and returns a `strategy_spec_registry_id`.
- It does **not** require governance approval before persisting (draft lifecycle
  starts at `draft`, not `candidate`).

If the draft-create interface is missing or insufficient, open a blocker on
AG-BE-SW-002 before writing the BFF route. Implementing a bypass write path
to work around a missing upstream interface would violate Constraint 1.

### Annotation 2 — ETag scope for `POST /versions` is the session row, not the version link

Constraint 4 requires `If-Match` on both `POST /versions` and
`POST /versions/{ver}/select`. The ETag in both cases must be the **session
row** ETag (the existing workshop ETag established by AG-BE-SW-001), not a
per-version-link ETag.

Consequences:
- `POST /versions` reads the session ETag, validates `If-Match`, applies the
  patch, inserts the new version link, then updates the session row
  `active_workshop_version_id` — all in a single transaction that advances
  the session ETag.
- `POST /versions/{ver}/select` does the same (different column, same session
  row). The `Idempotency-Key` check must be scoped to `(workshop_id, key)`,
  not to `(workshop_id, version_id, key)`.
- A stale `If-Match` on either route returns `409 CONCURRENT_MODIFICATION` and
  echoes the current session ETag so the client can re-fetch and retry.

Verify this interpretation against SD §17.2 before coding the routes; if the
spec is ambiguous, raise a blocker rather than guessing.

## No Reopen Conditions

No reopen conditions identified. The sidecar boundary is clean. The acceptance
packet is formally accepted as the support artifact for AG-BE-SW-002. Returned
to owner Claude for finalization.
