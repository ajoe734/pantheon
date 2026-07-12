# AG-GAP-003: Durable Postgres store for research

## Scope

The Agora BFF research facade previously kept research plans, runs, candidate
pools, scores, member reviews, discussions, monitoring, and metrics only in
process memory. They were lost whenever `operator-bff` restarted.

## Delivery

- `PostgresResearchPlanStore` persists every route-shaped aggregate as JSONB,
  with aggregate-kind namespacing and parent/subject indexes.
- A database primary key provides atomic, durable idempotency-key admission.
- `AGORA_RESEARCH_STORE_BACKEND=postgres` selects the durable backend and
  fails closed if no DSN is configured; memory remains the test-safe default.
- Root and control compose definitions plus both non-production BFF deploy
  paths pin the durable backend.
- Plan-first approval and tenant/user filtering remain in the existing router;
  the store does not create an alternate dispatch path.

## Verification

```text
python3 -m py_compile services/control-plane/bff/agora/research/store.py
python3 -m pytest -q \
  services/control-plane/bff/tests/test_agora_research_run_projection.py \
  services/control-plane/bff/tests/test_agora_candidate_pool.py \
  services/control-plane/bff/tests/test_agora_research_store_backend.py \
  services/control-plane/bff/tests/test_agora_workshop_dev_deploy_config.py
# 9 passed
git diff --check
```

## Remaining acceptance evidence

The hosted restart proof is intentionally post-merge. After the task PR is
merged and deployed, record a draft → approve → run plan and a candidate pool
with score and member review under `docs/deployment/evidence/ag-gap-003/`,
restart `operator-bff`, and prove both aggregates read back unchanged before
moving this task to `done`.
