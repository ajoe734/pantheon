# PPL-GOV-006 - Policy And Emergency Risk Actions

Owner: Gemini
Reviewer: Claude2
Depends on: PPL-GOV-001
Type: policy and risk-control implementation task

## Purpose

Keep quarterly promotion governance separate from immediate risk containment.
Large losses or hard risk failures must be handled immediately, without waiting
for quarterly ranking approval.

## Scope

- Encode emergency conditions from `PAPER_CANARY_LIVE_POLICY.md` and current
  runtime risk surfaces.
- Define containment actions for paper, canary, and live stages.
- Ensure containment can freeze, suspend, reduce capital, risk-off, liquidate,
  or rollback.
- Ensure containment cannot promote or increase live capital.
- Surface emergency action evidence in BFF and frontend management views where
  existing risk controls live.

## Acceptance

- S1/S2 incident, forced kill, hard risk breach, loader mismatch, binding
  mismatch, reconciliation anomaly, and drawdown breach are explicit triggers.
- Emergency actions are role-gated and audited.
- Emergency actions do not wait for quarterly review.
- Emergency actions never increase stage or capital.
- Tests cover at least one immediate containment path and one blocked promotion
  side effect.

## Validation

```sh
python3 -m pytest services/control-plane/bff/tests -k "risk or promotion or approval"
npm test -- --runInBand
git diff --check
```
