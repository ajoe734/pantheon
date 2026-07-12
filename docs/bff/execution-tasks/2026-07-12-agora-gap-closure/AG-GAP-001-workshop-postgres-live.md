# AG-GAP-001 — Durable Workshop Postgres Backend on Dev

Status: implementation complete; live proof pending merged dev deployment.

Owner: Codex
Reviewer: Codex2

## Scope

- Pin `AGORA_WORKSHOP_STORE_BACKEND=postgres`, its internal Compose DSN, and
  the `agora` schema in both root and BFF-only dev deployment paths.
- Log the selected workshop backend, store class, and schema at startup without
  exposing the DSN or credentials.
- Make the dev deployment workflow create a workshop, confirm the running BFF
  selected Postgres, restart `operator-bff`, and read the same workshop back.

Not in scope: workshop route semantics, Postgres table design, frontend
behavior, staging/live deployment policy, or broker authority.

## Acceptance

- [x] Versioned dev deploy configuration selects the Postgres workshop store.
- [x] Root and BFF-only deployment paths use the same durable configuration.
- [x] Startup logs identify `backend=postgres` and omit DSN credentials.
- [x] A focused unit/config gate covers the settings and safe log output.
- [x] The deployment workflow contains a restart-persistence smoke for dev.
- [ ] A merged dev deployment records the workflow run and surviving
  `workshop_id` as live evidence.

## Validation

Passed locally on 2026-07-12:

```text
python3 -m pytest -q services/control-plane/bff/tests/test_agora_workshop_dev_deploy_config.py services/control-plane/bff/tests/test_agora_strategy_workshop.py -k 'WorkshopStoreFactory or dev_root_and_bff_deploys_pin_durable_workshop_store'
# 7 passed, 62 deselected

bash -n scripts/deploy_nonprod_vm.sh
git diff --check origin/dev...HEAD
```

## Live Evidence

Pending. The workflow gate runs only after the task implementation is merged
and deployed to dev. Record the GitHub Actions run URL, deployed merge SHA,
and surviving workshop ID here before final owner closeout.
