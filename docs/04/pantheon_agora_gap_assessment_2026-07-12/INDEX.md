# Pantheon Agora gap assessment — 2026-07-12

## Durable workshop storage

AG-GAP-001 owns durable Strategy Workshop storage on the Pantheon dev BFF.
Dev deployment must select the Postgres workshop store, report that selection
at startup without logging credentials, and prove that a workshop remains
readable after the BFF container restarts. This does not change workshop API
semantics, database schema, or staging/live deployment policy.

## Contract honesty

AG-GAP-005 confirms that six v1.1 workshop operations remain registered
fail-closed 501 routes rather than implemented capabilities. Their formal
disposition and implementation boundaries are recorded in
`docs/bff/execution-tasks/2026-07-12-agora-gap-closure/AG-GAP-005-contract-honesty.md`.

The development compatibility record now targets the v1.5 additive bundle.
It must remain `pending` until frontend v1.5 generated-contract evidence and a
real frontend runtime commit are available.

## Design parity baseline

AG-GAP-010 ran a final recorded search for `AI Trading Desk Design.zip`
(missing since at least 2026-07-03), found no copy anywhere on the machine,
and formally declared it lost. Design parity is no longer gated on an
unrecoverable file; it is now checked against closure-pack written specs plus
TABS-GATE-011 hosted screenshots pinned to deployed frontend commit
`9d60297e5c200d05214df7f758ee0c20c224db02`. See
[`docs/04/agora_design_pack_dynui_2026-06-28/design-parity-baseline-declaration.md`](../agora_design_pack_dynui_2026-06-28/design-parity-baseline-declaration.md)
and the execution packet at
[AG-GAP-010-design-parity-baseline.md](../../bff/execution-tasks/2026-07-12-agora-gap-closure/AG-GAP-010-design-parity-baseline.md).

## Market-data activation

SRCLIVE-001 activation evidence was merged in PR #3047. AG-GAP-013 owns the
remaining read-model projection and live Agora readback proof; see the Agora
gap-closure execution packet.

## Checkout hygiene

AG-GAP-011 establishes frontend checkout hygiene rules, audits and removes
stale nested `.fe-ep`, `.fe-human-inbox-persona-focus`, and `.fe-worktrees`
checkouts inside the backend repository, and mandates that all frontend work
is routed canonically through `ajoe734/execute-plans@dev`. The details are
documented in [AG-GAP-011-fe-checkout-hygiene.md](../../bff/execution-tasks/2026-07-12-agora-gap-closure/AG-GAP-011-fe-checkout-hygiene.md).
