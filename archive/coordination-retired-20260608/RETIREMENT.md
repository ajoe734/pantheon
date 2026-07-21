# Retired: the `.coordination` delivery bus

Retired: 2026-06-08. Archived in place: 2026-07-20.
Status: historical record. Do not read this tree to determine outstanding work.

## What this was

A file-based request/response bus that routed frontend delivery packets between
Pantheon and the frontend repository. Its status vocabulary is described in
`DELIVERY_CLOSURE_AND_LOOP_STATES.md`.

## Why it is retired

`docs/delivery-coordination-bus.md:3-4` marks the guide "legacy implementation
guide; superseded for current frontend dev on 2026-06-08", and `:11` states
plainly: "Do not use this file to route current Pantheon frontend development."
Frontend delivery now runs through ordinary GitHub branch, PR and merge on
`ajoe734/execute-plans` (branch `dev`).

The bus also addressed `ajoe734/front-ai-trading-system`, which is itself
retired — see `docs/04/pantheon_repo_impl_diff_2026-05-16/REPORT.md:7`,
`docs/frontend/execute-plans-dev-hosting.md:28-30` and `AGENTS.md:57-61`.

## Why it was archived rather than deleted

A 2026-07-20 audit walked this tree and reported 44 features as outstanding work
because `<ID>-lovable-ui-task.yaml` packets carry a literal `status: ready`
field. That field is inert packet content, not loop state: the scanner selected
the dispatch packet as `latest_path` and copied it verbatim, so a record could
never advance past `ready` no matter what the frontend returned. The real
outcomes are in the sibling `<ID>-frontend-feedback.yaml` files, 41 of which
record `disposition: close`, `can_close: true`, `api_gaps: []`.

Every endpoint those packets declared was verified present in
`services/control-plane/bff/main.py`; none of the 44 represented missing backend
work. The tree is kept so that history and the returned feedback remain
auditable, and moved so that it stops being mistaken for a live backlog.

## Follow-up

The orchestrator's coordination scanner still points at the old path. It should
be disabled, or repointed, in the live supervisor config; otherwise it will scan
an empty location every cycle. That change touches `.orchestrator` and is
therefore subject to the security-deferred hold in
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/SECURITY_PREFLIGHT_AND_HOLD_MATRIX_2026-07-18.md`.
