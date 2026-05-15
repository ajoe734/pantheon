# SVC-OSS-RESEARCH-LEARNING-ACTIVATION-READY-E2E Evidence

Task: `SVC-OSS-RESEARCH-LEARNING-ACTIVATION-READY-E2E`
Owner: `Codex2`
Reviewer: `Codex`
Date: `2026-05-04`

## Scope

This task validates the end-to-end activation-ready boundary across the research orchestrator,
policy-learning, research-worker-gateway, BFF operator view, and the Qlib/TRL/RL/W&B scaffolds.
No runtime production activation, broker route, registry-promotion path, governance write path, or
capital-binding path was opened.

## Acceptance Mapping

| Acceptance item | Evidence |
|---|---|
| BFF and gateway expose one read-only operator view for research learning and OSS capabilities | `GET /api/v1/operator/research/oss-activation-ready` and `/oss-preactivation` contract tests passed; gateway capability and job tests passed |
| Offline/test smoke covers qlib trl rl wandb scaffolds | `scripts/smoke_oss_activation_ready_matrix.py --keep-output` passed 16/16 rows |
| Production paper canary live and online sdk paths remain rejected by default | Matrix default and enabled `paper`, `canary`, `live` rows rejected with `production_adapter_disabled`; W&B online remains explicit-gated and local smoke uses offline store |
| No broker registry governance or capital writes occur during default smoke | Matrix summary reported `registry_write=false`, `governance_write=false`, `broker_write=false`, `live_write=false` |

## Verification

Run from `/home/lupin/code/pantheon` with temporary test venv `/tmp/pantheon-oss-e2e-venv`:

```bash
/tmp/pantheon-oss-e2e-venv/bin/python scripts/smoke_oss_activation_ready_matrix.py --keep-output
# 16/16 passed; forbidden_writes={'registry_write': False, 'governance_write': False, 'broker_write': False, 'live_write': False}
```

```bash
/tmp/pantheon-oss-e2e-venv/bin/python -m pytest -q \
  scripts/test_smoke_oss_activation_ready_matrix.py \
  scripts/test_smoke_openclaw_activation_ready_e2e.py \
  services/control-plane/bff/test_research_oss_preactivation_contract.py \
  services/research/tests/test_research_orchestrator_http_service.py \
  services/policy-learning/tests/test_policy_learning_http_service.py \
  services/policy-learning/tests/test_policy_learning_gateway_routing.py \
  services/research-worker-gateway/tests/
# 51 passed in 30.47s
```

The venv was created under `/tmp` because the system Python is externally managed and lacked
`fastapi` for the focused service tests.

## Owner Closeout Verification

Finalization rerun from `/home/lupin/code/pantheon` on 2026-05-04:

```bash
/tmp/pantheon-oss-e2e-venv/bin/python scripts/smoke_oss_activation_ready_matrix.py --keep-output
# 16/16 passed; forbidden_writes={'registry_write': False, 'governance_write': False, 'broker_write': False, 'live_write': False}
# output_dir=/tmp/pantheon-oss-matrix-trcwqgfq
```

```bash
/tmp/pantheon-oss-e2e-venv/bin/python -m pytest -q \
  scripts/test_smoke_oss_activation_ready_matrix.py \
  scripts/test_smoke_openclaw_activation_ready_e2e.py \
  services/control-plane/bff/test_research_oss_preactivation_contract.py \
  services/research/tests/test_research_orchestrator_http_service.py \
  services/policy-learning/tests/test_policy_learning_http_service.py \
  services/policy-learning/tests/test_policy_learning_gateway_routing.py \
  services/research-worker-gateway/tests/
# 51 passed in 27.64s
```

## Notes

- `DEFERRED_OSS_ACTIVATION_MAP.md` is the task artifact named in status, but the repo-local file
  lives at `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md`.
- The task patch updates only documentation/evidence records. Existing runtime code already met
  the E2E acceptance after `SVC-BLUEPRINT-OSS-PREACTIVATION-CLOSURE`.
