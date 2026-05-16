# MGMT-BROKER-006 Review - Codex

Reviewer: Codex
Owner: Codex2
Reviewed at: 2026-05-15T17:46:01Z
Task: Shioaji canary readiness packet integration

## Decision

Changes requested. The happy-path packet and focused tests pass, but the Shioaji evidence validator does not yet enforce two acceptance conditions claimed by the handoff.

## Blocking Findings

1. `scripts/run_ep5_canary_readiness.py:360` checks only that `ooda_packet_validation_errors` is empty. It projects `ooda_packet.status` at line 389, but never rejects a non-closed OODA packet. A packet copied from `support/evidence/MGMT-BROKER-004/shioaji-sandbox-evidence-packet.json` with only `ooda_packet.status` changed to `open` is still accepted as ready evidence. MGMT-BROKER-006 claims the readiness packet validates a closed OODA packet, so this must fail closed and needs a regression test.

2. `scripts/run_ep5_canary_readiness.py:335` normalizes missing or non-list `acceptance_checks` to `[]`, and line 358 uses `any(...)` only to reject explicit failing entries. That means an evidence packet with `acceptance_checks=[]` is accepted. Since this gate is supposed to validate passed Shioaji evidence acceptance checks, the validator should require a non-empty list, and preferably the expected MGMT-BROKER-004 check names, with every status equal to `pass`.

## Verification Run

- `PYTHONPATH=scripts PYTHONDONTWRITEBYTECODE=1 python3 -m pytest scripts/test_run_ep5_canary_readiness.py scripts/test_run_canary_human_gate_smoke.py -q` -> 11 passed
- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/run_ep5_canary_readiness.py scripts/run_canary_human_gate_smoke.py scripts/test_run_ep5_canary_readiness.py scripts/test_run_canary_human_gate_smoke.py` -> passed
- `PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/broker/shioaji/test_evidence_packet.py -q` -> 4 passed
- `git diff --check -- scripts/run_ep5_canary_readiness.py scripts/test_run_ep5_canary_readiness.py scripts/run_canary_human_gate_smoke.py scripts/test_run_canary_human_gate_smoke.py docs/deployment/ep5-canary-ready/README.md docs/deployment/ep5-canary-ready/operator-approval-checklist.md support/evidence/MGMT-BROKER-006 support/evidence/MGMT-SAFE-004/README.md support/evidence/MGMT-SAFE-004/canary-human-gate-smoke.json` -> passed

## Reproduction Notes

- Mutating only `ooda_packet.status` to `open` returned `accepted=true` and projected `ooda_packet_status=open`.
- Mutating only `acceptance_checks` to `[]` returned `accepted=true`.
