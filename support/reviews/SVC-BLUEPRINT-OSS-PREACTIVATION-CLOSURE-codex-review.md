# Review: SVC-BLUEPRINT-OSS-PREACTIVATION-CLOSURE

Reviewer: Codex
Owner: Claude
Date: 2026-05-04
Disposition: approved

## Scope Reviewed

- `services/research`
- `services/policy-learning`
- `services/research-worker-gateway`
- `services/control-plane/bff`
- `services/learning/trl/worker.py`
- `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md`
- `RESEARCH_BACKEND_MATURITY_MATRIX.md`
- `scripts/smoke_oss_activation_ready_matrix.py`
- `scripts/test_smoke_oss_activation_ready_matrix.py`
- `scripts/test_smoke_openclaw_activation_ready_e2e.py`
- Current task closeout commit `68fa21f6`

## Findings

No blocking findings.

The reviewed implementation keeps OSS research and learning backends activation-ready without enabling production paths:

- Default Qlib, TRL, FinRL, RLlib, Ray Tune, W&B, and OpenClaw paths remain fail-closed for production, paper, canary, live, registry write, governance write, broker execution, and capital-bound routes.
- The explicit offline gate surfaces activation-ready metadata and bounded offline dispatch only; production activation remains reported as disabled.
- BFF exposes read-only `oss-preactivation` / `oss-activation-ready` aggregate state with inventory, run history, artifact refs, logs, and error summaries, but no activation command surface.
- The TRL worker subprocess import fix in `68fa21f6` is valid for gateway dispatch: the repo root is on `sys.path`, so `services.evaluation` imports resolve while the local `adapter` package remains importable.
- Documentation preserves the distinction between smoke-tested / activation-ready baselines and actual production activation.

## Verification

```bash
/tmp/pantheon-test-venv/bin/python -m pytest \
  scripts/test_smoke_oss_activation_ready_matrix.py \
  scripts/test_smoke_openclaw_activation_ready_e2e.py \
  services/control-plane/bff/test_research_oss_preactivation_contract.py \
  services/research/tests/test_research_orchestrator_http_service.py \
  services/policy-learning/tests/test_policy_learning_http_service.py \
  services/policy-learning/tests/test_policy_learning_gateway_routing.py \
  services/research-worker-gateway/tests/ -v
# 51 passed in 22.88s
```

## Closeout Note

Owner closeout should preserve the task-scoped commit metadata and then run the required `review_approved -> done` finalization workflow. No additional implementation changes are required by review.
