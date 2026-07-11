# MGMT-OPS-003-GAP-001 BFF Handoff Follow-up 2

Status: support packet for parent owner review

Parent task: `MGMT-OPS-003-GAP-001`

Sidecar task: `MGMT-OPS-003-GAP-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`

Owned layer: BFF/frontend handoff delta check and parent composition checklist

Not changing: L1 canonical truth, BFF runtime or contract, governance logic, or
the `execute-plans` implementation

Intended consumer: the parent owner implementing Portfolio Book in
`ajoe734/execute-plans`

## Outcome

No new BFF contract delta is required for the parent frontend closure. Reuse
the previously reviewed packet:

`support/sidecars/MGMT-OPS-003-GAP-001/MGMT-OPS-003-GAP-001-SIDECAR-BFF-HANDOFF.md`

That packet was approved by Claude and merged to `dev` in PR #3217 (merge
commit `bff64d6b274cc66e65a118df012ae7f16edf9b05`). Its inventory remains the
handoff source for the six Portfolio Book query dimensions, response-to-UI
mapping, fail-closed behavior, operator journey, and frontend fixture.

This follow-up deliberately does not duplicate that contract inventory. It
records the narrow composition steps the parent owner should carry into the
`execute-plans` delivery and the conditions that would require a refreshed BFF
handoff.

## Parent Composition Checklist

- Use one URL-owned filter state for `deployment_stage`, `broker_id`,
  `runtime_id`, `source_status`, `stale_telemetry`, and `risk_state`; clear
  `page_token` when any filter changes.
- Compare the request state with `meta.filters` during component and hosted
  verification so refresh and browser navigation cannot silently diverge.
- Render `meta.incidents` independently of the current row page. Do not infer
  incident completeness from `data.items` or `page_info`.
- Keep degraded, stale, unavailable, missing-binding, and unknown states
  visible. Missing counters are unavailable rather than zero, and missing
  identity must not be synthesized.
- Render `capital_scope` with accessible text for paper ledger, canary sleeve,
  live capital pool, and unknown. Unknown must not inherit a healthy or
  paper/live presentation.
- Preserve BFF-provided links and their target context for Persona Fleet,
  Performance Attribution, and Human Review. A missing individual link removes
  only that action, not its row or incident.
- Keep the 14-degraded-holding / 10-missing-binding fixture and all six URL
  filters in automated coverage, then collect the parent task's required
  authenticated desktop/mobile hosted evidence against the deployed SHA.

## Delta Triggers

Refresh this handoff before parent integration only if one of these becomes
true:

1. The holdings or positions route signature changes.
2. `meta.filters`, `meta.incidents`, `data.summary.source_coverage`,
   `capital_scope`, source diagnostics, or governed links change shape or
   semantics.
3. The parent implementation needs a write action not already represented by
   a BFF-provided governed link.
4. Hosted evidence disagrees with the checked-in BFF contract test.

If none applies, the discrepancy belongs to frontend consumption, deployment,
or hosted evidence and should not be papered over by inventing a new BFF
contract in this sidecar.

## Verification

The follow-up was checked against:

- `services/control-plane/bff/main.py`, including the Portfolio Book holdings
  and positions routes;
- `services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py`;
- `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/MGMT-OPS-003-GAP-001-frontend-monitor.md`;
- the approved original handoff and
  `support/reviews/MGMT-OPS-003-GAP-001-SIDECAR-BFF-HANDOFF-review-claude.md`.

This is advisory support material only. Claude reviews this follow-up; the
parent owner decides whether to compose it into the canonical frontend task.

## Review Record

Claude reviewed this follow-up against the live BFF contract
(`services/control-plane/bff/main.py`), its contract test
(`services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py`), and
the original approved packet, and approved it: the "no new delta" outcome is
correct because the Portfolio Book route signatures, `source_coverage`
fields, and `capital_scope`/`links` fields are unchanged since the original
handoff merged, and the parent composition checklist is fully backed by the
live source. Full verification is in
`support/reviews/MGMT-OPS-003-GAP-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-review-claude.md`.
This approval covers only this support artifact, not the parent task's own
implementation or hosted-evidence requirements.
