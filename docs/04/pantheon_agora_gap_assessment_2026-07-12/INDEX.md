# Pantheon Agora gap assessment — 2026-07-12

## Contract honesty

AG-GAP-005 confirms that six v1.1 workshop operations remain registered
fail-closed 501 routes rather than implemented capabilities. Their formal
disposition and implementation boundaries are recorded in
`docs/bff/execution-tasks/2026-07-12-agora-gap-closure/AG-GAP-005-contract-honesty.md`.

The development compatibility record now targets the v1.5 additive bundle.
It must remain `pending` until frontend v1.5 generated-contract evidence and a
real frontend runtime commit are available.

AG-GAP-008 replaces the Trading Room's empty SSE stub with an authenticated,
typed, replayable stream. The stream is isolated by tenant and user scope and
immediately emits `trading_room.connected`; trader decisions emit
`trading_room.decision.recorded` without expanding the decision-support-only
capital boundary.
