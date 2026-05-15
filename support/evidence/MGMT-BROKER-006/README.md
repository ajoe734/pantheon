# MGMT-BROKER-006 Shioaji Canary Readiness Packet Integration

Scope:

- Integrates the `MGMT-BROKER-004` Shioaji sandbox evidence packet into the EP5 canary human-gate readiness packet.
- Consumes `support/evidence/MGMT-BROKER-003/summary.json` as the Shioaji sandbox smoke summary.
- Requires both broker smoke and `shioaji_sandbox_evidence_packet.v1` before the human-gate packet can be `ready_for_review`.
- Requires the Shioaji evidence packet to carry all MGMT-BROKER-004 acceptance checks with `status=pass`, an empty `ooda_packet_validation_errors` list, and `ooda_packet.status=closed`.
- Production live broker remains disabled.
- Capital binding remains disabled.
- Canary progression still requires risk-owner and operator approval.

Generated evidence:

- `support/evidence/MGMT-BROKER-006/checklist/operator-checklist.json`
- `support/evidence/MGMT-BROKER-006/datasource-smoke/summary.json`
- `support/evidence/MGMT-BROKER-006/datasource-smoke/datasource-smoke.json`
- `support/evidence/MGMT-BROKER-006/plan/canary-deployment-plan.json`
- `support/evidence/MGMT-BROKER-006/plan/canary-execution-projection.json`
- `support/evidence/MGMT-BROKER-006/plan/summary.json`
- `support/evidence/MGMT-BROKER-006/human-gate/human-gate-packet.json`
- `support/evidence/MGMT-BROKER-006/human-gate/summary.json`

Generation commands:

```bash
CANARY_BROKER_SANDBOX_SMOKE_REF=support/evidence/MGMT-BROKER-003/summary.json \
CANARY_HUMAN_GATE_PACKET_REF=support/evidence/MGMT-BROKER-006/human-gate/human-gate-packet.json \
PYTHONDONTWRITEBYTECODE=1 python3 scripts/run_ep5_canary_readiness.py run-operator-checklist \
  --env-file env/canary-exec.env.example \
  --allow-empty-secrets \
  --output-dir support/evidence/MGMT-BROKER-006/checklist

CANARY_BROKER_SANDBOX_SMOKE_REF=support/evidence/MGMT-BROKER-003/summary.json \
CANARY_HUMAN_GATE_PACKET_REF=support/evidence/MGMT-BROKER-006/human-gate/human-gate-packet.json \
PYTHONDONTWRITEBYTECODE=1 python3 scripts/run_ep5_canary_readiness.py run-datasource-smoke \
  --env-file env/canary-exec.env.example \
  --output-dir support/evidence/MGMT-BROKER-006/datasource-smoke

CANARY_BROKER_SANDBOX_SMOKE_REF=support/evidence/MGMT-BROKER-003/summary.json \
CANARY_HUMAN_GATE_PACKET_REF=support/evidence/MGMT-BROKER-006/human-gate/human-gate-packet.json \
PYTHONDONTWRITEBYTECODE=1 python3 scripts/run_ep5_canary_readiness.py emit-canary-plan \
  --env-file env/canary-exec.env.example \
  --output-dir support/evidence/MGMT-BROKER-006/plan

PYTHONDONTWRITEBYTECODE=1 python3 scripts/run_ep5_canary_readiness.py emit-human-gate-packet \
  --task-id MGMT-BROKER-006 \
  --checklist-json support/evidence/MGMT-BROKER-006/checklist/operator-checklist.json \
  --datasource-summary-json support/evidence/MGMT-BROKER-006/datasource-smoke/summary.json \
  --plan-json support/evidence/MGMT-BROKER-006/plan/canary-deployment-plan.json \
  --drill-summary-json docs/deployment/evidence/ep5-dual-vm-local/20260424T143020Z/rollback-drill-summary.json \
  --broker-smoke-summary-json support/evidence/MGMT-BROKER-003/summary.json \
  --shioaji-evidence-packet-json support/evidence/MGMT-BROKER-004/shioaji-sandbox-evidence-packet.json \
  --dual-vm-evidence-dir docs/deployment/evidence/ep5-dual-vm-local/20260424T143020Z \
  --event-trace-status packetized \
  --event-trace-note "Replay-clean event-trace projection evidence remains packetized from the archived EP5 closeout bundle; MGMT-BROKER-006 adds the MGMT-BROKER-004 Shioaji sandbox evidence packet to the canary human-gate readiness inputs." \
  --output-dir support/evidence/MGMT-BROKER-006/human-gate
```

Result:

- `human-gate/summary.json` reports `status=ready_for_review`.
- `broker_sandbox_smoke_status=passed`.
- `shioaji_sandbox_evidence_packet_status=passed`.
- `human-gate/human-gate-packet.json` projects `ooda_packet_status=closed`.

Focused verification:

```bash
PYTHONPATH=scripts PYTHONDONTWRITEBYTECODE=1 python3 -m pytest scripts/test_run_ep5_canary_readiness.py scripts/test_run_canary_human_gate_smoke.py -q
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/run_ep5_canary_readiness.py scripts/run_canary_human_gate_smoke.py scripts/test_run_ep5_canary_readiness.py scripts/test_run_canary_human_gate_smoke.py
PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/broker/shioaji/test_evidence_packet.py -q
git diff --check -- scripts/run_ep5_canary_readiness.py scripts/test_run_ep5_canary_readiness.py scripts/run_canary_human_gate_smoke.py scripts/test_run_canary_human_gate_smoke.py docs/deployment/ep5-canary-ready/README.md docs/deployment/ep5-canary-ready/operator-approval-checklist.md support/evidence/MGMT-SAFE-004/README.md support/evidence/MGMT-SAFE-004/canary-human-gate-smoke.json
jq empty support/evidence/MGMT-BROKER-006/checklist/operator-checklist.json support/evidence/MGMT-BROKER-006/datasource-smoke/summary.json support/evidence/MGMT-BROKER-006/datasource-smoke/datasource-smoke.json support/evidence/MGMT-BROKER-006/plan/canary-deployment-plan.json support/evidence/MGMT-BROKER-006/plan/canary-execution-projection.json support/evidence/MGMT-BROKER-006/plan/summary.json support/evidence/MGMT-BROKER-006/human-gate/human-gate-packet.json support/evidence/MGMT-BROKER-006/human-gate/summary.json
```

Observed results on 2026-05-15:

- `scripts/test_run_ep5_canary_readiness.py scripts/test_run_canary_human_gate_smoke.py`: 14 passed.
- `services/broker/shioaji/test_evidence_packet.py`: 4 passed.
- `py_compile`, `git diff --check`, and `jq empty` passed.
