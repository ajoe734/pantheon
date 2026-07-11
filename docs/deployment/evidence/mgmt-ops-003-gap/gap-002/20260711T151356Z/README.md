# MGMT-OPS-003-GAP-002 Hosted Evidence

Captured: 2026-07-11T15:13:56Z

Task: `MGMT-OPS-003-GAP-002`

Verdict: `APPROVE`

## Delivery identity

- **Implementation head**: `3373bc3d2ee3d2548b16122e342eeee7d41b961d` (PR #3208 implementation source).
- **Deployed dev merge commit**: `e3d3d88487c15e989ebfb48b6b8783552b5d12ab` (the merge commit of `3373bc3d2` into `dev` running on the dev VM BFF).
- **Evidence branch HEAD**: `bf3362dcb3a06534a36f31757a704ae366a79025` (the release bot's evidence capture commit on the task branch).
- **Git lineage**: `git merge-base --is-ancestor 3373bc3d2 e3d3d88487` is verified to be successful. The evidence files are collected from the dev VM running the merge commit `e3d3d88487`.

## Authenticated BFF capture

The captures use the dev stub operator identity and the allowed `pantheon-dev` tenant. The bearer credential is not stored in these artifacts.

| Measure | Prior hosted baseline (11:48) | Current hosted capture (15:13) |
|---|---:|---:|
| Runtime count | 10 | 19 |
| Telemetry runtime count | 5 | 14 |
| Holdings | 18 | 27 |
| Missing-binding holdings | 10 | 19 |
| Degraded holdings | 18 | 27 |
| Holding incidents | 18 | 27 |

The prior values are the baseline recorded in the task handoff and reviewer packet. The current values come from `portfolio-book.json` and `portfolio-book-holdings.json` in this directory.

The core portfolio summary reports zero missing bindings for its pool/runtime join. That does not erase the holding-level truth: all 19 unresolved holdings remain present, degraded, and incident-backed in the holdings response. Formal attribution values remain unavailable for those degraded rows. This is a quarantine/isolation outcome, not a claim that the authoritative source data has been repaired.

## Hosted browser capture

Playwright loaded the current Pantheon-owned frontend in strict live mode with the same authenticated operator context at desktop (1440x1000) and mobile (390x844) viewports.

- Browser console errors: 0 desktop, 0 mobile.
- Failed BFF requests: 0 desktop, 0 mobile.
- Required holdings response: HTTP 200 on both viewports.
- Seed/mock fallback text: absent on both viewports.
- Raw `undefined`, `NaN`, or `Invalid Date`: absent on both viewports.
- Screenshots: `portfolio-book-desktop.png` and `portfolio-book-mobile.png`.

## Resolved UI-to-API differences

Under the new frontend deployment (`execute-plans` commit `e23aba15bf530a617135441602fcee86dec149df`), the hosted UI accurately represents the captured BFF truth:

- The summary cards display the correct counts (`Telemetry Runtime 14 / 19`).
- The desktop pool table correctly reflects telemetry status under strict live constraints.
- The browser evidence required-status arrays are fully populated for `/bff/management/portfolio-book`, `/bff/management/portfolio-book/holdings`, and `/bff/management/portfolio-book/positions` with `200` status codes.

All hosted verification criteria in this task's `review_contract` have been fully satisfied.

## Auditable Reconciliation Verification

To satisfy the reviewer's request for verification of the data quality reconciler under the same live constraints, the following rerunnable assets are captured and attached:

1. **Reconciliation snapshot**: `reconciliation-snapshot.json` contains raw, authenticated samples of runtime bindings, deployment plans, persona capital bindings, capital pools, and telemetry summaries extracted from the running services.
2. **Reconciliation execution report**: `reconciliation-report.json` records the output of the local reconciler, classifying each driving runtime binding's disposition (e.g. `repair_proposed` for unresolved missing bindings).
3. **Idempotent audit trail**: `reconciliation-audit.jsonl` contains the append-only logs of the reconciler. Running the reconciler twice against the same snapshot is verified to be fully idempotent, producing no duplicate records in the audit file.

All 19 unresolved holdings, their quarantined states, and incident-backed reasons are strictly preserved in the report.

## Files

- `portfolio-book.json`: authenticated portfolio summary response.
- `portfolio-book-holdings.json`: authenticated holdings and incidents.
- `portfolio-book-positions.json`: authenticated positions response.
- `performance-attribution.json`: authenticated attribution response.
- `hosted-summary.json`: compact deployment identity and summary extract.
- `hosted-browser-evidence.json`: viewport, console, network, and fallback counters.
- `portfolio-book-desktop.png`: desktop hosted capture.
- `portfolio-book-mobile.png`: mobile hosted capture.
- `reconciliation-snapshot.json`: raw inputs/samples for the reconciler.
- `reconciliation-report.json`: reconciler execution report.
- `reconciliation-audit.jsonl`: idempotent append-only audit trail.
