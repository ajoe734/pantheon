# AG-UIPOL-010: Performance cockpit parity

Status: draft follow-up from AG-UIPOL-005. Not yet dispatched.

Priority: 5 — closes the outcome/intervention/execution feedback loop.

## Matrix coverage

`parity-matrix.md` rows PF-01–PF-06.

## Design authority

- V6 §§15.1–15.5
- V5 §§6.1–6.7
- `Agora.dc.html` lines 1051–1185
- screenshots `01-v5-exec.png` and `02-v5-exec.png`

## Scope

Recompose the current KPI/source-health table into the designed three-column
Performance cockpit with overview, strategy detail, intervention, execution
history, and governed adjustment suggestions. Preserve the truthful zero vs
not-reported behavior already visible on the audited deploy.

Primary repo: `ajoe734/execute-plans@dev`; additive Pantheon attribution,
intervention, or history projections are in scope when current contracts do not
expose the required live data.

## Work

1. Build the 300px strategy list, central mode/detail board, and 320px
   assistant/action rail. Keep source-health diagnostics accessible but
   subordinate to operator outcome judgment.
2. Add Overview, Intervention, and Execution History modes with clear selected
   strategy/time-window context.
3. Render multi-strategy return/drawdown/contribution comparison and selected
   strategy returns/risk, attribution, trade quality, thesis/evidence, and
   monitoring state.
4. Add an intervention timeline with reason, owner/status, before/after,
   measured effect, evidence, and unresolved follow-up.
5. Add execution-history chronology with intended versus actual outcome,
   slippage/exception, and review entry. Do not imply broker confirmation when
   only Pantheon decision state exists.
6. Add Servant adjustment suggestions as reviewed proposals with evidence,
   risk, and apply/reject boundaries; never silently change strategy or route
   an order.

## Non-goals

- Regressing AG-UIPOL-004's measured-zero/not-reported and Unassigned semantics.
- Duplicating the canonical Performance Center; Agora should link to formal
  accounting while retaining decision context.
- Direct trading/capital/broker writes.

## Acceptance

- Hosted desktop evidence shows the three-column cockpit and all three modes
  for live data; empty/unavailable fields remain explicit.
- A named strategy can be selected without leaving Agora and its attribution,
  interventions, and execution history share the same time/scope context.
- Unassigned remains explained and after named strategies; measured zero is
  never rendered as missing, and missing is never rendered as zero.
- An adjustment suggestion has evidence, risk, proposal state, and reject/apply
  behavior with no direct order/broker/capital request.
- Component/contract/Playwright tests cover selected strategy, period change,
  no data, partial data, intervention status, and execution-history exceptions.
