# AG-UIPOL-003: Real per-widget data availability; collapse badge spam

## Scope

The trading-room proposal page renders twelve identical
"部分可用 + generated from ready StrategySpec version; live projection may
lag research" caption blocks — one per widget — because the BFF proposal
generator defaults every view/widget to `dataAvailability: "partial"`
(`services/control-plane/bff/agora/trading_room/router.py:860,899`) instead
of checking anything. The badges carry zero information and dominate the page.

## Work

1. pantheon: compute `dataAvailability` per widget from its actual data
   source (widget `dataSource`/query target): `full` when the backing surface
   returns rows for the scoped strategy, `missing` when the surface is not
   wired, `partial` only for genuinely degraded sources. Reuse the source
   health checks the performance tab already queries where possible. No
   blanket defaults.
2. execute-plans: information design — per-widget captions collapse into one
   availability summary per view ("9 full / 2 partial / 1 missing", expandable
   detail); only degraded widgets show an inline badge. Remove the repeated
   caption sentence from every card.

## Acceptance

- BFF: unit tests cover full/partial/missing derivation per widget; grep gate
  proves no unconditional `"partial"` default remains.
- Hosted screenshot: proposal page shows a single availability summary per
  view; no repeated identical caption blocks.
- A widget backed by a live surface (e.g. markets after SRCLIVE activation)
  reports `full` on dev.
