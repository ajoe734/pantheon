# MGMT-BROKER-003 Review

Reviewer: `Codex`
Owner: `Gemini2`
Date: `2026-05-15`
Disposition: `reopen`

## Findings

1. `support/evidence/MGMT-BROKER-003/summary.json` is stored under the MGMT-BROKER-003 evidence path, but the machine-readable `task_id` is `EP5-BROKER-TW-002-RERUN-REAL-FIX`. The harness hard-codes the same older id in `services/broker/shioaji/sandbox_smoke.py`, and the regression test currently asserts that older id. This makes the MGMT-BROKER-003 evidence packet non-task-scoped and will mislead later evidence packet, archive, and closeout readers.

## Verification

Passed:

```bash
python3 -m pytest services/broker/shioaji/test_adapter.py services/broker/shioaji/test_sandbox_smoke.py -q
```

Result: `44 passed in 19.70s`

Passed:

```bash
rm -rf /tmp/pantheon-mgmt-broker-003-review && BROKER_SHIOAJI_SANDBOX_ENABLED=1 python3 services/broker/shioaji/sandbox_smoke.py --mock-api --symbol 2890 --qty 1 --side buy --order-type limit --limit-price 18 --account-kind stock --submit-spacing-seconds 0 --cancel-delay-seconds 0 --output-dir /tmp/pantheon-mgmt-broker-003-review
```

Result: `status=passed`, `run_mode=mock_api_replay`, `reconciliation.status=passed`, `live_gate.response.error_code=SHIOAJI_LIVE_DISABLED`, `no_real_capital.status=passed`.

## Required Fix

Regenerate the MGMT-BROKER-003 evidence with a task-scoped id, preferably by making the smoke harness accept an explicit `--task-id` or equivalent metadata override and updating the regression coverage. Keep the existing proof boundary explicit: this review verified only repo-safe mock replay evidence, not a real external Shioaji simulation-account proof.
