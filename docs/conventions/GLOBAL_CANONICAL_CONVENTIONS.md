# Global Canonical Conventions

Status: draft-canonical
Last updated: 2026-04-22
Source of truth inputs:
- `docs/reviews/Pantheon_Response_to_System_Design_Open_Questions.md`
- `docs/reviews/2026-04-20-system-design-open-questions-for-architecture-team.md`
- `docs/reviews/Pantheon_Response_to_System_Design_Followup_Questions.md`
- `docs/reviews/Pantheon_Response_to_Architecture_Blockers_Decision_Package.md`
Tier: L1 Platform Architecture & Policy
Scope: cross-module contract conventions, freshness/degradation semantics, and readiness framing
Conflict rule: a narrower canonical decision or module contract may add domain-specific detail, but it must not contradict these global conventions without an explicit override decision

## Purpose

This document defines the cross-module rules that every Pantheon BFF contract,
screen packet, handoff bundle, and implementation lane should share.

Until superseded by a later architecture decision, these rules are the working
canonical conventions for new contracts, truth-rebaseline work, and readiness
classification.

## Front-page principles

1. Module-level canonical contract != new deployable service.
   Contract documents define BFF truth, packet truth, UI truth, and execution
   truth. They do not require a new standalone runtime service.
2. Frontend and BFF must not synthesize domain truth.
   CTA authority, lifecycle truth, degradation truth, freshness truth, and
   readiness truth must come from canonical contracts or canonical readiness
   records, not UI heuristics.
3. Shared envelope is a minimum wrapper, not a replacement for domain identity.
   Domain-specific primary keys remain domain-shaped even when the BFF adds
   common operator-facing metadata.
4. Freshness and availability are different dimensions.
   `meta.staleness` describes freshness; `meta.surfaces.*.state` describes
   availability or degradation of a specific surface.
5. Canonical readiness truth outranks derived docs.
   If a dedicated ratification or decision file exists, it outranks SA, packet
   family, and other derivative summaries.

## Required global rules

### 1. `allowedActions`

- `allowedActions` is a backend-owned object of named boolean authority flags.
- Frontend must not derive CTA availability from actor role, object state, or
  route presence.
- New contracts must not replace object-shaped `allowedActions` with a string
  list or array.

Examples:
- `allowedActions.canCancel`
- `allowedActions.canApproveMutation`
- `allowedActions.canCommit`

### 2. Snapshot and staleness

- Canonical snapshot timestamp lives at `meta.snapshot_at`.
- Freshness lives at `meta.staleness`.
- New contracts must not use `stale` as the primary `meta.surfaces.*.state`
  value.
- Existing legacy contracts may keep `stale` in surface state only as a
  deprecated migration alias.

### 3. Surface truth

- Surface health must be exposed under `meta.surfaces.*`.
- Canonical surface state values are defined in
  `docs/conventions/DEGRADATION_DICTIONARY.md`.
- `partial` is valid only for non-authoritative read surfaces where enrichment
  or auxiliary data is incomplete.
- Command, approval, runtime, deployment, and safety-critical surfaces must not
  use `partial`.

### 4. Response envelope

- Shared BFF envelope rules live in
  `docs/conventions/BFF_RESPONSE_ENVELOPE.md`.
- New detail contracts should expose a minimum `object_ref` wrapper plus
  common operator-facing metadata.
- Domain-specific identity stays domain-shaped inside the domain payload and is
  not replaced by a generic `id` / `title` requirement.

### 5. Readiness ladder

- Canonical readiness states live in
  `docs/conventions/MODULE_READINESS_LADDER.md`.
- Derived labels such as `pending-bff`, `route-live`, `ready`, `implemented`,
  and `shell-only` must map back to the ladder; they are not standalone
  canonical states.
- `contract_ready` and `implementation_ready` are distinct readiness levels and
  must not be collapsed.

### 6. Partial activation

- `partial activation` is a module-specific promotion modifier, not a separate
  global readiness rung.
- The current explicit case is `CW-03`: it may partial-activate before
  `CW-02` is fully live, but transcript-dependent surfaces remain gated.

## Ownership and boundary decisions referenced by this document

- `docs/decisions/LIN-002-lineage-ownership.md`
- `docs/decisions/control-plane-persona-boundary.md`
- `docs/decisions/control-plane-router-enforcement-ownership.md`

## Follow-up harmonization notes

The `2026-04-22` integration of the follow-up architecture response applies two
explicit harmonizations so the canonical docs stay internally consistent:

1. `allowedActions` remains object-shaped even where a follow-up example
   accidentally rendered it as an array.
2. `CW-03` partial activation is integrated as a promotion modifier / gate,
   not as a seventh global readiness rung, because Pantheon now uses one shared
   readiness ladder.

## Remaining rebaseline work

1. Rebaseline legacy docs that still encode `stale` as a primary surface state.
2. Rebaseline packet family, SA, and backlog docs so they use the canonical
   readiness ladder directly.
3. Rebaseline legacy module contracts that still use array-shaped actions or
   omit `meta.staleness`.
