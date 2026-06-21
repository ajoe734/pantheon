# AG-BE-SW-002 Sidecar: Acceptance Packet and Dependency Map

| Field | Value |
|---|---|
| Task ID | `AG-BE-SW-002-SIDECAR-ACCEPTANCE` |
| Helper kind | `acceptance_packet` |
| Parent task | `AG-BE-SW-002` — StrategySpec draft patch/version linkage |
| Parent owner / reviewer | Claude2 / Claude |
| Sidecar owner / reviewer | Claude / Claude2 |
| Prepared by | Claude |
| Date | 2026-06-21 |
| Mutates canonical truth | false |
| Status | **Review Approved — Finalized** |
| Reviewer decision | APPROVED by Claude2 (2026-06-21) |
| Review file | `support/sidecars/AG-BE-SW-002/AG-BE-SW-002-SIDECAR-ACCEPTANCE-REVIEW.md` |

## Purpose

This support-only packet provides the acceptance checklist, dependency map, and
architecture constraint notes for `AG-BE-SW-002`. It is intended for use by:

- **Parent owner (Claude2)** — as a pre-implementation reference to understand
  what the reviewer will check and what architecture boundaries must be respected.
- **Reviewer (Claude)** — as a structured acceptance gate when the parent task
  reaches `review`.

This packet does not modify L1 canonical truth, existing BFF routes, Registry
models, OpenAPI bundles, schemas, or any runtime/governance implementation.

---

## Dependency Map

```
AG-BE-ID-001                  (Phase 1: Identity foundation)
  └─► AG-BE-SW-001            (Workshop session/event persistence — DONE)
        └─► AG-BE-SW-002      (StrategySpec draft patch/version linkage — this task)
              ├─► AG-BE-SW-003 (Completeness/conflict/next-best-question skill — HELD)
              ├─► AG-BE-SW-004 (Streaming workshop aggregate)
              └─► AG-FE-SW-002 (Version compare card + readiness gates)
```

**AG-BE-SW-001 delivered (per `task/AG-BE-SW-001` commit history):**
- `services/control-plane/bff/agora/strategy_workshop/router.py` —
  list/create/get workshop, append message, list events, completeness snapshot.
  Version/select routes registered as `501 NOT_IMPLEMENTED` stubs, pending SW-002.
- `services/control-plane/bff/agora/strategy_workshop/store.py` —
  `strategy_workshop_session`, `strategy_workshop_event`,
  `strategy_completeness_snapshot` persistence.
- `services/control-plane/bff/tests/test_agora_strategy_workshop.py` — SW-001 tests.

**AG-BE-SW-002 must deliver:**
- `services/research/strategy_spec/workshop_projection.py` — project existing
  Registry draft into workshop-visible summary without copying StrategySpec truth.
- `services/research/strategy_spec/patching.py` — apply JSON-path `from`/`to`
  patches to a StrategySpec, return a new patched dict, validate result against
  `strategy_spec.schema.json`.
- `services/research/strategy_spec/version_compare.py` — diff two StrategySpec
  dicts and return a structured diff keyed by JSON path.
- Wire `GET /bff/agora/workshops/{id}/versions` and
  `POST /bff/agora/workshops/{id}/versions` and
  `POST /bff/agora/workshops/{id}/versions/{ver}/select` in the existing
  router (replace the three `501` stubs).
- Unit tests for patching and version compare; integration tests for the
  new BFF routes.

---

## Architecture Constraints

These constraints are **non-negotiable** and must be satisfied for review to pass:

### 1. StrategySpec truth stays in the existing Registry

The workshop does **not** own or copy StrategySpec JSON. It owns only:

- `strategy_workshop_version_link` rows (already in the SW-001 DB schema,
  per `docs/04/pantheon_agora_cross_repo_2026-06-20/sw001-deep-closure/
  AG-BE-SW-001_deep_design_closure_2026-06-21.md §7.3`).
- Pointers (`strategy_spec_registry_id`, `selected_version_id`) inside the
  session row.

The `workshop_projection.py` module may read from the existing Registry but
must not create a second StrategySpec store or add Registry-write capability
to the workshop surface.

### 2. Patch produces a new Registry version, not a workshop copy

When the operator applies a patch, the implementation must:

1. Read the base StrategySpec from the existing Registry (via
   `services/research/strategy_spec/models.py` `StrategySpec.from_dict`).
2. Apply the JSON-path patch using `patching.py`.
3. Validate the result against `strategy_spec.schema.json`.
4. Write the patched spec as a new **Registry** version through the existing
   Registry draft-create path (not a custom endpoint or bypass).
5. Insert a new `strategy_workshop_version_link` row pointing to the new
   Registry version.
6. Update `active_workshop_version_id` on the session.

The BFF route (`POST /bff/agora/workshops/{id}/versions`) must delegate to
this sequence. It must not write raw JSON into the workshop event or session
tables as a StrategySpec copy.

### 3. No live-order route, no RuntimeBinding write

Agora evidence feeds Observe/Learn only. The patching, versioning, and
selection paths must not:

- Create or modify a `RuntimeBinding`.
- Trigger a live or canary broker order.
- Promote a candidate artifact to `approved` without a governance gate.
- Widen the tool allowlist from client input.

### 4. Idempotency and concurrency

Version-create and version-select routes are mutating and must:

- Require `Idempotency-Key` and `If-Match` headers (same pattern as other
  mutating workshop routes per SD §17.2 / AG-BE-SW-001 ETag contract).
- Return `409 CONCURRENT_MODIFICATION` with current ETag and snapshot link
  on stale `If-Match`.
- Repeating the same `Idempotency-Key` must not create a second version link.

### 5. Patch format is JSON-path `from`/`to`

Patch payloads use the shape established by `VersionPatchProposal` in the
result-synthesis SPEC (`docs/04/pantheon_agora_cross_repo_2026-06-20/
design-closure/skills/agora/result-synthesis/SPEC.md`):

```json
{
  "patches": [
    {
      "path": "/execution_profile/execution_mode_hint",
      "from": "research",
      "to": "paper"
    }
  ],
  "base_version_id": "<workshop_version_id>",
  "reason": "Promote to paper stage after OOS validation"
}
```

`patching.py` must reject:

- Patches that target fields outside the StrategySpec schema.
- `from` values that do not match the actual field value in the base spec
  (optimistic-lock semantic at field level).
- Results that fail schema validation.

### 6. Version compare is a read-only diff

`version_compare.py` must return a structured diff; it must not write any state.
The diff must be keyed by JSON path and include `before`, `after`, and `type`
(`added` / `removed` / `changed`).

---

## Acceptance Checklist

The reviewer (Claude) must verify all items below before approving.

### A. Architecture

- [ ] No new StrategySpec store — all StrategySpec JSON truth lives in
  the existing Registry.
- [ ] `workshop_projection.py` reads from Registry and returns a summary
  projection; it does not create or mutate Registry entries.
- [ ] `patching.py` produces a patched dict validated against
  `strategy_spec.schema.json`; invalid patches return descriptive errors.
- [ ] `version_compare.py` returns a structured diff (JSON-path keyed)
  and has no write side-effects.
- [ ] `POST /versions` writes through the existing Registry draft-create
  path; no second write endpoint is introduced.
- [ ] `selected_version_id` / `active_workshop_version_id` update is the
  only write that `POST /{ver}/select` makes.

### B. BFF Routes

- [ ] `GET /bff/agora/workshops/{id}/versions` lists workshop version links
  ordered by `sequence_no`; returns JSON with `strategy_spec_registry_id`
  and `parent_workshop_version_id` per entry.
- [ ] `POST /bff/agora/workshops/{id}/versions` accepts patch payload,
  applies patch, writes new Registry version, inserts
  `strategy_workshop_version_link`, returns new `workshop_version_id`.
- [ ] `POST /bff/agora/workshops/{id}/versions/{ver}/select` updates
  `selected_version_id` on the session; ETag/idempotency enforced.
- [ ] All three routes replace the existing `501` stubs (no route is left
  returning `NOT_IMPLEMENTED`).
- [ ] All three mutating routes require `If-Match` and `Idempotency-Key`.
- [ ] Stale `If-Match` returns `409 CONCURRENT_MODIFICATION`.
- [ ] Duplicate `Idempotency-Key` is idempotent (no second version/link).
- [ ] Routes reject with `409 WORKSHOP_ALREADY_CONCLUDED` or
  `409 WORKSHOP_ARCHIVED` when session is in terminal state.

### C. No-Order-Route

- [ ] No `RuntimeBinding` creation, modification, or approval path is
  reachable through any of the new routes.
- [ ] No live or canary broker order is triggered by patch, version-create,
  or version-select.
- [ ] `execution_mode_hint` may be `paper` or `research` in a patch target;
  the implementation does not auto-advance beyond draft lifecycle.

### D. Tests

- [ ] Unit test: `patching.py` — apply valid patch, reject wrong `from`,
  reject out-of-schema path, reject result that fails schema validation.
- [ ] Unit test: `version_compare.py` — diff identical specs (empty),
  diff added field, diff removed field, diff changed value.
- [ ] Integration test: `POST /bff/agora/workshops/{id}/versions` — happy
  path, stale ETag, duplicate idempotency key, patch validation failure.
- [ ] Integration test: `POST /bff/agora/workshops/{id}/versions/{ver}/select`
  — happy path, stale ETag, wrong version ID (404), concluded workshop (409).
- [ ] Integration test: `GET /bff/agora/workshops/{id}/versions` — empty,
  one entry, multiple entries ordered by `sequence_no`.

### E. Prior bundle immutability

- [ ] `services/control-plane/specs/agora/bundle_index.json` is not edited.
- [ ] `services/control-plane/specs/agora/bundle_index.v1_1.json` is not edited.
- [ ] `services/control-plane/openapi/agora_v1.openapi.yaml` is not edited.
- [ ] `services/control-plane/openapi/agora_v1_1.openapi.yaml` is not edited.
- [ ] If new OpenAPI or schema artifacts are needed, they are additive
  `v1.2` files; they do not overwrite prior bundles.

---

## Existing Stubs (Ready to Replace)

The three version routes were pre-registered in the existing router with explicit
`501` stubs by AG-BE-SW-001:

```text
services/control-plane/bff/agora/strategy_workshop/router.py:
  GET  /bff/agora/workshops/{workshop_id}/versions        → _not_implemented(...)
  POST /bff/agora/workshops/{workshop_id}/versions        → _not_implemented(...)
  POST /bff/agora/workshops/{workshop_id}/versions/{ver}/select → _not_implemented(...)
```

The parent owner (Claude2) should replace these three stubs in-place without
touching any other route handler.

---

## Current State of Relevant Artifacts

| Artifact | Status | Notes |
|---|---|---|
| `services/research/strategy_spec/models.py` | Exists | `StrategySpec`, `StrategyLifecycleState`, `from_dict`, `validate_strategy_spec` available |
| `services/research/strategy_spec/normalizer.py` | Exists | RS-002 normalize path; do not modify for SW-002 |
| `services/research/strategy_spec/workshop_projection.py` | Missing | Must be created by SW-002 |
| `services/research/strategy_spec/patching.py` | Missing | Must be created by SW-002 |
| `services/research/strategy_spec/version_compare.py` | Missing | Must be created by SW-002 |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Exists | Three version stubs present; all other SW-001 routes live |
| `services/control-plane/bff/agora/strategy_workshop/store.py` | Exists | `strategy_workshop_version_link` table defined in DB schema (§7.3 of SW-001 closure doc) |
| `services/control-plane/bff/tests/test_agora_strategy_workshop.py` | Exists | SW-001 tests; SW-002 should add to this file or a new sibling |

---

## SD Reference Map

The dispatch script references `SD §6/§7.4(VersionPatchProposal)`. The
concrete mapping to authoritative documents:

| SD reference | Document | Content |
|---|---|---|
| SD §6 | `docs/04/pantheon_agora_cross_repo_2026-06-20/sw001-deep-closure/AG-BE-SW-001_deep_design_closure_2026-06-21.md §6` | Canonical status model (`open`, `in_review`, `concluded`, `archived`) |
| SD §7.3 | Same doc §7.3 | `strategy_workshop_version_link` table definition |
| SD §7.4 | Same doc §7.4 | `strategy_completeness_snapshot` table definition |
| `VersionPatchProposal` | `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/skills/agora/result-synthesis/SPEC.md` | `proposedVersionPatches` shape with `path`, `from`, `to`, `reason` |
| SD §17.2 (versions / select-version) | `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/03_servant_and_workshop_contracts.md §B` | Route list including `GET|POST /bff/agora/workshops/{id}/versions` and `POST .../versions/{ver}/select` |

---

## Reviewer Implementation Annotations (Claude2 — for AG-BE-SW-002 owner)

These two items were flagged during review as pre-implementation checkpoints.
They are advisory only and do not reopen the sidecar. They must be resolved
before the parent owner begins coding the affected routes.

### Annotation 1 — Verify Registry draft-create path before wiring `POST /versions`

Before implementing `POST /bff/agora/workshops/{id}/versions`, confirm that a
usable draft-create function exists in `services/research/strategy_spec/`
(e.g. `registry.py`, `store.py`, or a helper from `models.py`) that:

- Accepts a StrategySpec dict and returns a `strategy_spec_registry_id`.
- Does **not** require governance approval before persisting (draft lifecycle
  starts at `draft`, not `candidate`).

If this interface is missing or insufficient, open a blocker on AG-BE-SW-002
rather than implementing a bypass write path (which would violate Constraint 1).

### Annotation 2 — ETag scope for `POST /versions` is the session row

The `If-Match` ETag on both `POST /versions` and `POST /versions/{ver}/select`
must be the **session row** ETag (established by AG-BE-SW-001), not a
per-version-link ETag. Consequences:

- `POST /versions` validates `If-Match`, applies patch, inserts the new version
  link, and updates `active_workshop_version_id` — all in one transaction that
  advances the session ETag.
- `POST /versions/{ver}/select` does the same (different column, same session row).
- `Idempotency-Key` check must be scoped to `(workshop_id, key)`, not
  `(workshop_id, version_id, key)`.
- Stale `If-Match` on either route returns `409 CONCURRENT_MODIFICATION` with the
  current session ETag so the client can re-fetch and retry.

Verify against SD §17.2 before coding; raise a blocker if the spec is ambiguous.

---

## Handoff Note to Parent Owner (Claude2)

Before starting implementation, verify:

1. `AG-BE-SW-001` PR is merged into `dev` and the workshop session/event tables
   and the three version stubs are confirmed present in the router.
2. The existing Registry has a `draft-create` path available in
   `services/research/` (used by `POST /versions` to persist the patched spec).
3. No design question about the Registry write interface is outstanding; if
   unclear, open a blocker rather than implementing a workaround.

Start with `services/research/strategy_spec/patching.py` and its unit tests,
since it is a pure function with no dependencies. Then `version_compare.py`.
Then `workshop_projection.py`. Wire the BFF routes last.

Do not widen the acceptance criteria or invent routes beyond those listed in
§B above without raising a blocker to Claude first.
