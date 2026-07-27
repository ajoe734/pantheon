# L12-EVO-001 durable Evolution dispatch evidence

Status: owner evidence ready for independent `Claude` review.

This packet proves the repository implementation for complete Evolution input
coverage, tenant-scoped decision authority, durable approved-action dispatch,
real Research Orchestrator terminal receipts, restart recovery, replay
cooldown, compensation, and worker health. It does not claim replacement-dev
activation and does not predeclare the reviewer decision.

The schema-valid machine receipt is
[`evidence.json`](evidence.json). Its companion digest is
[`evidence.sha256`](evidence.sha256).

## Delivered behavior

- Every monitored runtime summary is checked for a numeric metric and an
  approved positive artifact baseline. Missing inputs produce explicit
  incomplete coverage and no fabricated breach candidate.
- Decision, active-target uniqueness, dispatch identity, reads, replay, and
  compensation are tenant-scoped.
- Approval creates a durable intent before downstream work. Duplicate triggers
  reuse it, and a crash between approval and activation is reconciled.
- Research actions create a real Research Orchestrator run. Queued work leaves
  the decision `approved`; only terminal readback can set `executed`.
- Terminal downstream failure leaves the decision approved, dead-letters the
  intent, and records one durable compensation obligation.
- Unsupported governance/deployment/runtime planes remain explicit DLQ records;
  they are never converted into synthetic executed state.
- The Evolution API and dispatch worker share one configured backend. Production
  posture refuses JSON before boot, while separate local API/worker processes
  prove the shared JSON development authority and restart semantics.
- All four Evolution Compose processes have readiness/health coverage and a
  30-second stop grace period. Scheduler and threshold workers publish atomic
  heartbeat files and fail health on startup, error, or stale state.

## Validation

The final merged-current-dev regression used the checkout-scoped interpreter:

```text
PANTHEON_RUNTIME_MANAGER_URL=http://runtime-manager:8081 \
.venv-pantheon/bin/python -m pytest -q \
  services/evolution \
  services/incidents \
  services/postmortems \
  services/control-plane/governance/test_evolution_decision.py \
  services/control-plane/governance/test_evolution_controller.py \
  services/control-plane/governance/test_evolution_dispatcher_invariants.py \
  services/control-plane/governance/smoke_test_evolution_decision.py \
  services/control-plane/governance/smoke_test_evolution_controller.py \
  scripts/test_evolution_daily_sweep_deploy_contract.py

431 passed, 5 warnings in 112.60s
```

The five warnings are existing FastAPI/Starlette deprecation notices.
`compileall`, `docker compose -f docker-compose.yml config -q`, and
`git diff --check` also passed. `ruff` is not installed in the dependency
environment or on `PATH`, so that optional lint command was not run and is
recorded as such in the manifest.

## Boundaries

This task does not write `RuntimeBinding`, deployment, governance-target,
broker, or capital state. Global Compose admission and hosted identity remain
owned by `L12-MANIFEST-001`, `L12-VERIFY-OBS-001`, and `L12-HOSTED-001`.

Owner `Codex2` and reviewer `Claude` remain distinct. The manifest has one owner
record and deliberately no formal reviewer verdict. Governed approval must bind
this exact repository-relative manifest before owner closeout.
