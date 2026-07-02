# PPLG-006 - Automatic Risk Guardrails And Incident Review Evidence

Priority: P0

Area: Runtime risk, capital protection, incident review

Depends on: `PPLG-001`

## Goal

Implement automatic protective actions for loss, drawdown, exposure, slippage,
order, data, runtime, policy, and correlation triggers. These actions may pause,
reduce, risk-off, or freeze immediately and must create review evidence.

## Required Work

- Implement `RiskGuardrailEvent`.
- Wire triggers to runtime/pool/persona telemetry.
- Implement allowed automatic actions:
  - `pause_new_orders`
  - `reduce_exposure`
  - `risk_off`
  - `frozen`
- Create or link incident review records for every automatic action.
- Enforce that automatic guardrails cannot promote or increase allocation.
- Add resume requirements through PPLG-005 human review.

## Acceptance Criteria

- Daily loss breach pauses new orders.
- Max drawdown breach risk-offs the persona/pool scope.
- Critical policy violation freezes.
- Runtime/data/broker health failures pause dependent trading.
- Each automatic action records observed value, threshold, action, incident ID,
  and trace ID.
- Tests prove human review is required to resume from `risk_off` and `frozen`.

## Artifacts

- `services/capital/risk_policy.py`
- `services/runtime-manager/*`
- `services/incident/*`
- `services/control-plane/bff/tests/*risk_guardrail*`
- `tests/e2e/*risk_off*`
