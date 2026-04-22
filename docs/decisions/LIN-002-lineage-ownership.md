# LIN-002 Lineage Ownership

Status: draft-canonical
Last updated: 2026-04-22
Source of truth inputs:
- `docs/reviews/Pantheon_Response_to_System_Design_Open_Questions.md`
- `docs/reviews/Pantheon_Response_to_System_Design_Followup_Questions.md`
Tier: L1 Platform Architecture & Policy
Scope: UI-facing lineage read ownership and migration boundary
Conflict rule: this decision governs operator-facing lineage read ownership until superseded by a newer explicit lineage decision

## Decision

`services/lineage-read/` is the UI-facing canonical lineage read owner.

## Why

Pantheon currently has lineage-related logic across:

- `services/telemetry/lineage_read/`
- `services/lineage-read/`
- `services/control-plane/bff/`

Without an explicit read owner, the BFF can drift into consuming multiple truth
paths and the frontend can inherit inconsistent lineage semantics.

## Working rules

1. Domain services own normalized lineage write edges.
2. `services/lineage-read/` owns UI-facing lineage read truth.
3. BFF lineage and evolution surfaces consume `lineage-read` only.
4. Telemetry lineage implementation may remain as an internal substrate but
   must not become a second UI-facing truth path.

## Migration phases

### Phase 0: transitional coexistence

Multiple lineage paths may still exist in the repo. This is tolerated only as a
transition state.

### Phase 1: facade consolidation

`services/lineage-read/` wraps or consumes:

- telemetry lineage substrate
- registry lineage edges
- domain normalized lineage edges

### Phase 2: BFF cutover

BFF stops directly consuming telemetry lineage read paths for operator-facing
surfaces and resolves lineage through `lineage-read` only.

### Phase 3: UI-facing deprecation

Any UI-facing lineage endpoint outside `lineage-read` becomes `internal_only`
or is deprecated.

## Allowed internal telemetry lineage usage

Telemetry lineage substrate may still be used for:

- incident reconstruction
- telemetry correlation
- background projection build
- lineage repair jobs

It must not be consumed directly by:

- Lovable
- frontend
- BFF UI surfaces
- operator UI

## Consequences

1. Frontend and BFF surfaces should stop describing telemetry lineage paths as
   parallel read owners.
2. Future rebaseline work should collapse wording like "BFF may use telemetry
   lineage directly" back to `lineage-read`.
3. Performance or implementation convenience does not justify a second
   operator-facing lineage truth path.
