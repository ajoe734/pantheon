# MGMT-PERF-IA-001 Sidecar BFF / Frontend Handoff Follow-Up 2

Date: 2026-07-11  
Owner: Codex2  
Reviewer: Claude  
Parent task: `MGMT-PERF-IA-001`  
Helper kind: `bff_handoff_packet`  
Scope: support-only follow-up. This packet changes no canonical truth, BFF
contract/runtime, frontend implementation, route registry, or governance
behavior. The parent owner decides what to absorb.

## Purpose

The original sidecar packet is merged and the parent route/menu task is now
`review_approved`. This follow-up converts that packet into a closeout-time
absorption check so the parent can finish its route-manifest delivery without
claiming that the separate `MGMT-PERF-IA-002` read-model gaps are already
closed.

## State Observed

| Surface | Observed state | Consequence |
|---|---|---|
| `MGMT-PERF-IA-001` | `review_approved`; reviewer notes say execute-plans PR #250 is open and awaiting merge. | The parent owner must merge and perform normal closeout before `done`; this sidecar does not finalize it. |
| Original BFF handoff | Archived `done`; Pantheon PR #3096 merged to `dev`. | Its BFF route map and operator journey remain the base handoff. |
| This follow-up | `in_progress`, owned by Codex2 and reviewed by Claude. | Only this support artifact is delivered for review. |

## Parent Absorption Matrix

| Parent concern | Safe to absorb now | Must remain outside the parent claim |
|---|---|---|
| Canonical navigation ownership | One typed manifest owns sidebar, command palette, breadcrumbs, canonical centers, tabs, and compatibility redirects. | No statement that navigation ownership changes BFF truth or merges independent reads atomically. |
| Redirect context | Preserve only recognized typed entity/time/tab keys, replace browser history, and terminate at one canonical destination. | Do not forward opaque legacy query strings or infer IDs from display labels. |
| Performance center | Map overview to portfolio-book reads, attribution to performance-attribution reads, and exposure/holdings/positions to their existing separate reads. | Do not describe exposure, holdings, and positions as one atomic snapshot unless `MGMT-PERF-IA-002` supplies that contract. |
| Rankings center | Keep rolling persona league and quarterly ranking as distinct tabs/datasets. | Do not silently translate a quarter into a rolling period or treat both datasets as one ranking truth. |
| Governance center | Deep-link from immutable ranking evidence to recommendation/review/decision/apply records. | Do not collapse recommendation, Human Review, approval, and applied receipt into one mutable status. |
| Degraded behavior | Keep the canonical shell and navigation visible; show BFF-owned source/degradation metadata and disable dependent actions. | Empty rows are not proof of fresh zero results; fallback summaries are not formal attribution. |

## Residual BFF Query Gaps

These remain inputs for `MGMT-PERF-IA-002` or later center implementation and
must not block the route-manifest merge by themselves:

1. Common identity keys across persona, strategy, pool, asset, broker, runtime,
   and regime.
2. Shared `period`/quarter vocabulary and defaults.
3. Snapshot/as-of identity and source-confidence semantics for cross-center
   comparisons.
4. Supported-filter discovery instead of frontend inference from route names.
5. Typed cross-center link fields and governance evidence lineage identifiers.
6. Consistent partial/degraded/unavailable representation across independently
   composed reads.

The parent may reserve typed context slots for these concepts, but it should not
invent final field names or response semantics in frontend code.

## Operator Journey Closeout Check

The merged parent implementation should preserve this sequence:

```text
Persona Fleet / Cockpit
  -> Performance (entity + time context retained)
  -> Attribution or Exposure (source state visible)
  -> Rankings rolling or quarterly (dataset identity retained)
  -> Governance recommendation / Human Review
  -> decision and applied receipt
  -> return to originating ranking/performance context
```

For every legacy entry point, a test should prove that the redirect reaches one
canonical center, preserves only allowed context, uses history replacement, and
does not loop after refresh. For every degraded read, a test should prove that
navigation remains usable while unsupported conclusions/actions fail closed.

## Reviewer Checklist

Claude should verify:

- only this support artifact changed;
- the packet supplements rather than replaces the merged original sidecar;
- it does not claim execute-plans PR #250 is merged;
- it keeps `MGMT-PERF-IA-002` identity/time/snapshot/filter/link gaps visible;
- it makes no canonical, BFF runtime, schema, registry, governance, or frontend
  implementation change.

Recommended approval command:

```bash
AI_NAME=Claude \
REVIEW_FILE=support/sidecars/MGMT-PERF-IA-001/MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md \
REVIEW_NOTES_ZH="Follow-up packet approved: it accurately scopes parent closeout absorption, preserves MGMT-PERF-IA-002 residual query gaps, and changes support material only." \
./scripts/ai-status.sh approve MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 \
  "Support-only BFF/frontend closeout handoff approved for parent owner absorption."
```

## Validation

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2
AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-PERF-IA-001
AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF
git diff --check -- support/sidecars/MGMT-PERF-IA-001/MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md
```

No runtime tests are required because this follow-up changes only a support
artifact.
