# MGMT-OPS-003-GAP-001-SIDECAR-BFF-HANDOFF Review — Claude

Task: MGMT-OPS-003-GAP-001-SIDECAR-BFF-HANDOFF — BFF/frontend handoff packet
for parent `MGMT-OPS-003-GAP-001` (Frontend Portfolio monitor closure)
Owner: Codex2
Reviewer: Claude
Review date: 2026-07-11
Disposition: **approved**

## 1. What Was Submitted For Review

`support/sidecars/MGMT-OPS-003-GAP-001/MGMT-OPS-003-GAP-001-SIDECAR-BFF-HANDOFF.md`,
added by commit `e8a091fd6` and merged to `dev` in PR #3217 (merge
`bff64d6b274cc66e65a118df012ae7f16edf9b05`). The packet inventories the
`/bff/management/portfolio-book/holdings` and `/positions` query contract, the
BFF response-to-UI mapping, fail-closed rules, an operator journey, and a
frontend acceptance-fixture checklist for the parent owner implementing
Portfolio Book in `ajoe734/execute-plans`. It is explicitly advisory support
material and does not touch canonical truth, BFF runtime, or `execute-plans`
source.

## 2. Verification Performed

Checked every factual claim in the packet against the live BFF source and its
contract test, not just the packet's own prose:

- `services/control-plane/bff/main.py:30734-31083`
  (`bff_management_portfolio_book_holdings`): confirmed all 6 documented
  operator-control query params (`deployment_stage`, `broker_id`,
  `runtime_id`, `source_status`, `stale_telemetry`, `risk_state`) plus the 6
  "additional context" params (`capital_pool_id`, `persona_id`, `status`, `q`,
  `page_token`, `page_size`) match the route signature exactly; comma-split
  filtering behavior, `meta.filters` echo, and pagination-before-slice order
  match the packet's claims.
- `services/control-plane/bff/main.py:31004-31011`
  (`summary["source_coverage"]`): keys (`source_row_count`, `runtime_count`,
  `telemetry_runtime_count`, `stale_row_count`, `missing_binding_count`,
  `degraded_source_count`) match the packet's coverage-card mapping exactly.
- `services/control-plane/bff/main.py:30992,31050` (`incident_count`,
  `meta.incidents`): both are computed from the full filtered
  `holding_contexts` set, not the paginated page — confirms the packet's
  "cross-check against the complete `meta.incidents` collection" guidance is
  correct, not aspirational.
- `services/control-plane/bff/main.py:31085-31131`
  (`bff_management_portfolio_book_positions`): confirmed it accepts the same
  query params and is literally composed by calling the holdings handler
  internally, exactly as the packet states.
- `services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py:513-605`
  (`test_portfolio_book_missing_focus_persona_holding_is_incident_not_formal_attribution`):
  confirmed the packet's reference-fixture claim line-for-line — a
  missing-telemetry holding stays in the table with `source_status=degraded`,
  `risk_state=degraded_source`, `capital_scope.scope_kind=paper_ledger`, a
  `MISSING_TELEMETRY` incident, and the related `by-persona` attribution row
  is `data_confidence=partial`/`source_status=partial` with
  `metrics.total_pnl is None`.
- Confirmed both cited parent docs exist:
  `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/MGMT-OPS-003-GAP-001-frontend-monitor.md`
  and
  `docs/04/pantheon_mgmt_ops_003_hosted_gap_2026-07-11/MGMT_OPS_003_HOSTED_GAP.md`.
- Confirmed `capital_scope` and `links` fields are present on holding entries
  (`main.py:27415`, `27474`, `27682`, `27688`), matching the response-to-UI
  mapping table.

No inaccurate claim, overstated confidence, or scope violation was found. The
packet does not propose or imply any BFF/runtime/canonical change; every
recommendation is framed as a choice for the `execute-plans` implementer.

## 3. Verdict

**APPROVED.** The packet is an accurate, narrowly-scoped support artifact.
Its query-parameter inventory, response-to-UI mapping, fail-closed rules, and
reference fixture description all match the live BFF contract and its
contract test exactly. This approval covers only this support artifact — it
does not approve, merge, or complete parent `MGMT-OPS-003-GAP-001`, which
still requires the actual `execute-plans` frontend implementation and hosted
evidence per its own `review_contract`.

## 4. Verification Commands

```bash
git show --stat e8a091fd6
git log --oneline --all | grep -i "MGMT-OPS-003-GAP-001-SIDECAR-BFF-HANDOFF"
git merge-base --is-ancestor e8a091fd6 bff64d6b2 && echo merged
grep -n "portfolio-book/holdings\|portfolio-book/positions" services/control-plane/bff/main.py
sed -n '30734,31083p' services/control-plane/bff/main.py
sed -n '513,606p' services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py
test -f docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/MGMT-OPS-003-GAP-001-frontend-monitor.md
test -f docs/04/pantheon_mgmt_ops_003_hosted_gap_2026-07-11/MGMT_OPS_003_HOSTED_GAP.md
```
