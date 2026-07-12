# Agora gap closure — 2026-07-12

- [AG-GAP-001 durable workshop storage](AG-GAP-001-workshop-postgres-live.md):
  pins the dev workshop backend to Postgres and gates deployment on restart
  persistence.
- [AG-GAP-004 durable dashboard recipe storage](AG-GAP-004-dashboard-postgres.md):
  moves recipe identity/version/idempotency state behind a Postgres-capable
  store while retaining ETag CAS, append-only history, and rollback semantics.
- [AG-GAP-003 durable research storage](AG-GAP-003-research-postgres.md):
  persists research plans/runs and candidate collaboration aggregates in
  Postgres while preserving plan-first and tenant/user boundaries.
- [AG-GAP-002 durable Trading Room storage](AG-GAP-002-trading-room-postgres.md):
  adds the optional Postgres backend, pins it for dev, and defines restart
  persistence evidence without widening the order-routing boundary.
- [AG-GAP-005 contract honesty](AG-GAP-005-contract-honesty.md): formally
  defers six workshop 501 operations and refreshes dev compatibility tracking
  from the stale v1.1 snapshot to the v1.5 additive contract.
- [AG-GAP-006 main.py route migration](AG-GAP-006-mainpy-route-migration.md):
  documents the migration of identity, personalization, and conversation routes out
  of main.py to separate sub-routers while preserving compatibility/mocking seams.
- [AG-GAP-007 capabilities mismatch](AG-GAP-007-capabilities-mismatch.md): fixes the 
  BFF /bff/agora/capabilities endpoint silent projection loading failure and documents
  the dev journal dry-run residue cleanup procedure.
- [AG-GAP-008 Trading Room typed SSE](AG-GAP-008-trading-room-typed-sse.md):
  replaces the empty stream stub with immediate typed acknowledgement,
  user-scope isolation, bounded replay, and typed trader-decision delivery.
- [AG-GAP-010 design parity baseline](AG-GAP-010-design-parity-baseline.md):
  declares `AI Trading Desk Design.zip` formally lost after a final recorded
  search and replaces it with closure-pack specs plus TABS-GATE-011 hosted
  screenshots pinned to a deployed frontend SHA as the design-parity baseline.
- [AG-GAP-011 FE checkout hygiene](AG-GAP-011-fe-checkout-hygiene.md): details
  the audit and removal of stale frontend checkouts and establishes rules for
  execute-plans workspace integrity.
- [AG-GAP-012 twelve-block completeness](AG-GAP-012-twelve-block-completeness.md): defines the
  12-block completeness contract for the Winner Branch strategy family, its compatible 7-dimension
  mapping, and the BFF readiness projection implementation.
- [AG-GAP-013 market-data activation](AG-GAP-013-market-data-activation.md):
  projects real SRCLIVE source records into Agora market/watchlist and daily
  signal surfaces without inventing rows.

AG-GAP-001 completion requires a Pantheon task PR merged to `dev`, successful
focused validation, and live workflow evidence showing that a newly created
workshop survives an `operator-bff` restart.
