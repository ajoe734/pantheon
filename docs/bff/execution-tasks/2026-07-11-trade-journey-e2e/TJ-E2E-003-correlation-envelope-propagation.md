# TJ-E2E-003 - Correlation Envelope Propagation

Owner: Claude  
Reviewer: Antigravity  
Wave: 1  
Repository: `ajoe734/pantheon`  
Dependencies: `TJ-E2E-001`, `TJ-E2E-002`

## Goal

Propagate the versioned correlation envelope through every P0 producer from
strategy/signal origin to broker, ledger and reconciliation.

## Required work and acceptance

- Generate stable journey/correlation/causation IDs at the approved boundary.
- Preserve known upstream identifiers through commands, events and receipts.
- Add idempotency, schema compatibility and no-field-loss contract tests.
- Prove paper happy path, risk reject and broker reject without temporal guessing.
- Merge focused service changes to Pantheon `dev` with migration/rollback notes.
