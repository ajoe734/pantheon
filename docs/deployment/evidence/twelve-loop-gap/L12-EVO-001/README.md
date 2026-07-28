# L12-EVO-001 durable Evolution dispatch evidence

Status: merged owner closeout evidence ready for independent `Codex2` review.

Owner evidence cut v1.0.0 uses canonical task-state journal sequence 3158,
committed at `2026-07-28T11:29:57Z`, as its point-in-time snapshot. The
canonical snapshot scan boundary is journal sequence 3158: owner `Codex`,
reviewer `Codex2`, status `in_progress`, and no canonical `review_file` yet.
The reviewer must bind this manifest through the governed approval command.

This packet proves the repository implementation for complete Evolution input
coverage, tenant-scoped decision authority, durable approved-action dispatch,
real Research Orchestrator terminal receipts, restart recovery, replay
cooldown, compensation, and worker health. Delivery PR
[`#4267`](https://github.com/ajoe734/pantheon/pull/4267) merged exact head
`fbf01b97ba6f27efeb213aff45316cb7e76c112f` to `dev` as
`64e7c1fbb586bf1f3b3ca624c1e5290dfa0144e0`. It does not claim
replacement-dev activation and does not predeclare the reviewer decision.

The schema-valid machine receipt is
[`evidence.json`](evidence.json). Its companion digest is
[`evidence.sha256`](evidence.sha256). The manifest binds the final source and
this README through an acyclic content digest and passes all ten fail-closed
evidence rules.

## Delivered behavior

- Every monitored runtime summary is checked for a numeric metric and an
  approved positive artifact baseline. Missing inputs produce explicit
  incomplete coverage and no fabricated breach candidate.
- Empty, missing, malformed, or all-disabled threshold configuration produces
  an explicit fail-closed diagnostic; otherwise-eligible artifacts remain
  incomplete in direct assessment, the sweep tick, and the coverage API.
- Decision, active-target uniqueness, dispatch identity, reads, replay, and
  compensation are tenant-scoped.
- Approval creates a durable intent before downstream work. Duplicate triggers
  reuse it, and a crash between approval and activation is reconciled.
- Research actions create a real Research Orchestrator run. Queued work leaves
  the decision `approved`; only terminal readback can set `executed`.
- Terminal downstream failure leaves the decision approved, dead-letters the
  intent, and records one durable compensation obligation whether the receipt
  arrives through the worker or the direct execute API. Duplicate direct
  failure calls converge on the same DLQ attempt and compensation record.
- Unsupported governance/deployment/runtime planes remain explicit DLQ records;
  they are never converted into synthetic executed state.
- The Evolution API and dispatch worker share one configured backend. Production
  posture refuses JSON before boot, while separate local API/worker processes
  prove the shared JSON development authority and restart semantics.
- All four Evolution Compose processes have readiness/health coverage and a
  30-second stop grace period. Scheduler and threshold workers publish atomic
  heartbeat files and fail health on startup, error, or stale state.
- Root Compose defaults the Evolution API, dispatcher, and scheduler to shared
  token authentication and the same tenant authority; an unauthenticated caller
  cannot inherit the default tenant merely by reaching the service.

## Validation

The post-merge current-dev regression used the checkout-scoped interpreter:

```text
PANTHEON_RUNTIME_MANAGER_URL=http://runtime-manager:8081 \
.venv-pantheon/bin/python3 -m pytest -q \
  services/evolution \
  services/incidents \
  services/postmortems \
  services/control-plane/governance/test_evolution_decision.py \
  services/control-plane/governance/test_evolution_controller.py \
  services/control-plane/governance/test_evolution_dispatcher_invariants.py \
  services/control-plane/governance/smoke_test_evolution_decision.py \
  services/control-plane/governance/smoke_test_evolution_controller.py \
  scripts/test_evolution_daily_sweep_deploy_contract.py

446 passed, 5 warnings in 89.75s
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

Owner `Codex` and reviewer `Codex2` remain distinct. The manifest has owner
records and deliberately no formal reviewer verdict. Governed approval must
bind this exact repository-relative manifest before owner closeout.
