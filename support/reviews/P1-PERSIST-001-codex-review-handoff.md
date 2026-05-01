# P1-PERSIST-001 Review Handoff

Owner: Codex
Reviewer: Claude
Status: ready for review

## Scope Delivered

- Added `services.foundation.persistence_posture` as the shared staging/prod
  persistence posture guard.
- Enforced Postgres backend declarations, Postgres `DATABASE_URL`, and shared
  object-store env vars in `PANTHEON_PERSISTENCE_POSTURE` / `PANTHEON_ENV`
  staging-prod modes.
- Kept JSON/JSONL fallback allowed only outside enforced posture; health payload
  reports `dev_fallback_allowed=true` only in non-enforced modes.
- Wired posture into `/healthz` dependencies and details for:
  `consultation`, `training-session`, `policy-learning`,
  `research-orchestrator`, `research-worker-gateway`, `governance`, `capital`,
  `incidents`, `postmortems`, `promotion`, `memory`, and
  `reconciliation-drift`.
- Preserved existing source-ingest/search-specific posture guard and added a
  platform script that checks both posture families.
- Updated root/control compose and `env/prod-control.env.example` so the posture
  tier and object-store env reach the guarded services.
- Updated SA task artifacts with the P1-PERSIST-001 disposition.

## Verification

```text
python3 -m pytest services/foundation/tests/test_persistence_posture.py services/test_source_search_posture.py services/source_ingestion/test_compose_activation.py services/search/tests/test_service_activation_contract.py services/consultation/test_compose_activation.py services/research/tests/test_research_orchestrator_http_service.py services/policy-learning/tests/test_policy_learning_http_service.py services/research-worker-gateway/tests/test_research_worker_gateway_http_service.py services/reconciliation-drift/tests/test_reconciliation_drift_http_service.py services/promotion/test_service.py services/memory/test_main.py services/postmortems/test_main_routes.py services/incidents/test_main_routes.py services/training-session/tests/test_http_service.py services/training-session/tests/test_postgres_event_store.py services/training-session/tests/test_compose_activation.py -q
=> 95 passed

python3 -m py_compile services/foundation/persistence_posture.py scripts/check_platform_persistence_posture.py services/governance/main.py services/capital/main.py services/incidents/main.py services/promotion/main.py services/consultation/main.py services/training-session/main.py services/research/main.py services/research-worker-gateway/main.py services/policy-learning/main.py services/reconciliation-drift/main.py services/memory/main.py services/postmortems/main.py
=> passed

python3 scripts/check_platform_persistence_posture.py --env-file env/prod-control.env.example
=> all 14 posture checks ok

docker compose config --quiet
=> passed

docker compose -f docker-compose.control.yml config --quiet
=> passed

git diff --check
=> passed
```

Additional note: `services/capital/test_service.py` still has three unrelated
failures around pre-existing unhandled domain exceptions on binding write paths.
`services/capital/test_service.py::test_health` passes, and the posture changes
do not alter those binding paths.

## Dirty Worktree Note

The worktree already contained unrelated bracket-order changes in
`services/execution/lean_runtime/*`. `SA-20_v2_risk_register_corrected.md` also
contains bracket-order hunks owned by that adjacent work. They were not reverted;
the P1-PERSIST scope is the persistence posture hunks and files listed above.
