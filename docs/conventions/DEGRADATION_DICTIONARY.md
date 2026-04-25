# Degradation Dictionary

Status: draft-canonical
Last updated: 2026-04-22
Source of truth inputs:
- `docs/reviews/Pantheon_Response_to_System_Design_Open_Questions.md`
- `docs/reviews/Pantheon_Response_to_System_Design_Followup_Questions.md`
- `docs/reviews/Pantheon_Response_to_Architecture_Blockers_Decision_Package.md`
Tier: L1 Platform Architecture & Policy
Scope: canonical surface-state and freshness vocabulary for BFF-facing operator surfaces
Conflict rule: module contracts may define domain-local banners or derived UI treatments, but they must not redefine the canonical surface-state or freshness vocabulary in this document

## Purpose

Pantheon currently has more than one surface-health vocabulary in the repo.
This document defines the canonical target model so new contracts stop
introducing additional drift.

## Canonical model

Pantheon separates:

- surface availability or degradation
- data freshness

Use:

```json
meta.surfaces.<surface>.state
```

for surface availability, and:

```json
meta.staleness.status
```

for freshness.

## Canonical surface state values

The canonical `meta.surfaces.<surface>.state` values are:

- `ok`
- `partial`
- `degraded`
- `unavailable`

### `ok`

- surface is available
- no verified degradation is present

### `partial`

- only valid for non-authoritative read surfaces
- canonical data is still readable
- some enrichment, auxiliary metadata, or secondary refs are incomplete

Examples where `partial` is allowed:

- lineage summary with unresolved evidence refs
- transcript enrichment with missing display labels or missing resolved links
- evidence rail with some unresolved external refs
- search enrichment with optional metadata missing
- insight aggregation with delayed secondary sources

### `degraded`

- some verifiable content may still exist
- the UI must not treat missing rows, empty arrays, or absent fields as
  authoritative success
- use when the surface is available but cannot be trusted as complete or fully
  authoritative

### `unavailable`

- no verifiable surface truth is available for that surface

## Surfaces that must never use `partial`

The following surfaces are authoritative and must use only:

- `ok`
- `degraded`
- `unavailable`

They must not use `partial`:

- `allowedActions`
- approval authority surfaces
- deployment authority surfaces
- runtime truth surfaces
- kill-switch or rollback surfaces
- persona lifecycle mutation surfaces
- capital binding authority surfaces

## Canonical freshness values

Freshness must live under `meta.staleness.status`.

Canonical values:

- `fresh`
- `stale`
- `unknown`
- `not_applicable`

Example:

```json
{
  "meta": {
    "snapshot_at": "2026-04-22T00:00:00Z",
    "staleness": {
      "status": "stale",
      "as_of": "2026-04-21T23:58:00Z",
      "max_age_seconds": 30
    },
    "surfaces": {
      "lineage": {
        "state": "ok"
      }
    }
  }
}
```

## Migration rules

### Deprecated legacy shape

If an existing contract currently uses:

```json
meta.surfaces.<surface>.state = "stale"
```

it may remain temporarily as a deprecated alias during migration.

### Canonical target shape

New or rebaselined contracts must use:

- `meta.staleness.status` for freshness
- `meta.surfaces.<surface>.state` for availability

### Legacy shorthand warning

Legacy string-shorthand surface shapes may still exist in older docs. They
should be rebaselined toward object-wrapped surface state plus `meta.staleness`.

## Working interpretation rules

1. `stale` means freshness degradation, not primary surface unavailability.
2. `partial` means enrichment incompleteness, not missing command authority.
3. If event ordering, integrity, or canonical authority is broken, use
   `degraded`, not `partial`.
4. Frontend must not enable authority-dependent actions from a surface that is
   `partial`, `degraded`, or `unavailable`.
