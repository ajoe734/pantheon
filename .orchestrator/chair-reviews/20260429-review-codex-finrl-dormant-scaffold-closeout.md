# Review: SVC-FINRL-DORMANT-SCAFFOLD-CLOSEOUT

**Reviewer:** Codex
**Task Owner:** Codex2
**Date:** 2026-04-29
**Decision:** APPROVED

## Scope

Reviewed the FinRL dormant scaffold closeout for:

- fail-closed CLI/env gates
- draft-only `rl_policy` output with `deployment_stage=none`
- no registry, governance, paper, canary, live, or capital-bound write path
- activation and maturity docs accurately acknowledging the FinRL dormant scaffold without reopening RL activation

Shared docs also contain adjacent RLlib/Ray Tune/W&B edits from other active tasks. This review approval is scoped to the FinRL statements and FinRL artifact behavior.

## Acceptance Check

| Criterion | Status | Notes |
|---|---|---|
| FinRL worker and smoke require explicit deferred-prep gate | PASS | `smoke_test.py` exits 2 without `--enable-deferred-prep`; `worker.py` exits 2 without `PANTHEON_FINRL_PREP_ENABLED=1`. |
| Outputs remain draft and deployment stage none | PASS | Smoke and worker output `artifact_state=draft`; `deployment_summary.current_stage` / `deployment_stage` remains `none`. |
| No registry/governance/paper/canary/live writes are possible | PASS | Adapter builds in-memory artifact bundle, registry projection, and candidate packet only; no external client or file write path exists. |
| Activation and maturity docs no longer understate the dormant scaffold | PASS | FinRL rows now describe the prep-only adapter/worker/smoke path while preserving the closed RL activation gate. |

## Verification

```bash
python3 -m pytest services/research/finrl/test_adapter.py -q
# 14 passed in 0.15s

python3 services/research/finrl/smoke_test.py
# exit 2, deferred-prep gate required

python3 services/research/finrl/smoke_test.py --enable-deferred-prep
# assertions: OK; artifact_state=draft; deployment_stage=none; gate_state=closed

python3 services/research/finrl/worker.py
# exit 2, PANTHEON_FINRL_PREP_ENABLED=1 required

PANTHEON_FINRL_PREP_ENABLED=1 python3 services/research/finrl/worker.py
# artifact_state=draft; deployment_stage=none; backend=stub_finrl
```

## Decision

APPROVED. The FinRL scaffold is fail-closed, offline-only, non-writing, and documented as dormant prep rather than RL activation. Returned to Codex2 for owner finalization.
