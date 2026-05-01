# P2-BROKER-SANDBOX-ORDER-001 Review

Reviewer: Codex
Date: 2026-05-01

## Outcome

Approved after reviewer hardening. The broker sandbox/test-key smoke path is fail-closed for production-live modes, writes structured evidence for IBKR validate-only, Shioaji simulation, and Kraken validate-only lanes, and keeps archived evidence free of raw broker secret material.

## Review Notes

- Verified `scripts/run_broker_sandbox_order_smoke.py` rejects production live modes before payload generation.
- Verified the packet writes auth, account readiness, place, cancel/replace, readback, execution/no-fill, telemetry, and reconciliation JSON artifacts.
- Verified archived evidence under `docs/deployment/evidence/broker-sandbox-order-smoke/20260501T154654Z` records repo-safe `secret://...` references only.
- Reviewer applied a narrow hardening patch so `credential_ref` must use an explicit secret-reference scheme, rather than accepting arbitrary short credential text.

## Verification

```bash
python3 -m pytest scripts/test_run_broker_sandbox_order_smoke.py services/execution/test_ibkr_adapter.py services/execution/test_shioaji_adapter.py services/execution/test_kraken_adapter.py
git diff --check -- scripts/run_broker_sandbox_order_smoke.py scripts/test_run_broker_sandbox_order_smoke.py docs/04/CANARY_LIVE_ACTIVATION_CRITERIA_AND_RUNBOOK.md docs/deployment/ep5-canary-ready/broker-venue-config-boundary.md docs/deployment/ep5-canary-ready/README.md
rg -n "(AKIA|ASIA|api[_-]?key\\s*[:=]|secret\\s*[:=]|private[_-]?key|-----BEGIN|password\\s*[:=]|token\\s*[:=])" docs/deployment/evidence/broker-sandbox-order-smoke/20260501T154654Z
python3 scripts/run_broker_sandbox_order_smoke.py --provider ibkr --mode live --symbol AAPL.US --side buy --quantity 1 --limit-price 120 --output-dir /tmp/pantheon-live-reject-check
python3 scripts/run_broker_sandbox_order_smoke.py --provider ibkr --mode validate_only --symbol AAPL.US --side buy --quantity 1 --limit-price 120 --credential-ref ibkr-paper-secret-name --output-dir /tmp/pantheon-secret-ref-reject-check
```

Result:

- 29 tests passed.
- `git diff --check` passed for the reviewed task files.
- Secret-pattern scan found only the intended `credential_ref` fields containing `secret://...` references.
- Negative CLI checks returned exit code 2 for production-live mode and arbitrary credential text, as expected.

## Acceptance Mapping

| Acceptance | Review disposition |
|---|---|
| Broker order API smoke runs with paper/sandbox/test credentials, simulation mode, or validate-only mode before any production-live side effect | Covered by non-production provider modes and production-live rejection. |
| Smoke captures auth, account readiness, place, cancel/replace, status/readback, execution/no-fill or fill disposition, telemetry, and reconciliation evidence | Covered by runner packet writer and archived IBKR/Shioaji/Kraken evidence. |
| Production-live order/cancel/position/capital routes remain disabled unless explicit activation gate passes and no raw broker secrets enter repo artifacts | Covered by fail-closed mode gate, `production_live.enabled=false`, and credential-reference whitelist hardening. |

## Residual Scope Boundary

This review approves the bounded validate-only/simulation smoke path. It does not claim real-money broker acknowledgement, fills, production live order routing, position mutation, or capital movement.
