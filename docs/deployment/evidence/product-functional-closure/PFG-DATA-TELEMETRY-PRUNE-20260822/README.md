# PFG-DATA-TELEMETRY-PRUNE-20260822: dev telemetry prune scope fix

## Defect

`scripts/deploy_nonprod_vm.sh`'s `prune_dev_management_ai_telemetry_for_disk`
truncated every `telemetry_events` table whose schema was either the derived
Management AI schema (`MANAGEMENT_AI_STORE_SCHEMA`, default `management_ai`)
**or** `public`. Every dev root deploy that reached the disk-pruning step
therefore truncated `public.telemetry_events`, the canonical telemetry table,
even though the intent (and the surrounding gating/env flag naming) was only
to bound the size of the derived Management AI store.

## Fix

- The `TRUNCATE` loop now matches `n.nspname = target_schema` only, dropping
  the `'public'` alternative entirely.
- Added an explicit guard: if `MANAGEMENT_AI_STORE_SCHEMA` ever resolves to
  `public` (misconfiguration), the function logs a refusal and returns without
  truncating anything, so the canonical table cannot be pruned by this path
  under any schema configuration.
- No other gating (`PANTHEON_DEPLOY_ENV`, `PANTHEON_DEPLOY_COMPONENT`,
  `MANAGEMENT_AI_STORE_BACKEND`, `PANTHEON_DEV_POSTGRES_TELEMETRY_PRUNE`) or
  the diagnostic before/after size listing was changed.

## Verification

```bash
bash -n scripts/deploy_nonprod_vm.sh
/home/lupin/pantheon/.venv/bin/python -m pytest -q \
  scripts/test_deploy_nonprod_telemetry_prune.py \
  scripts/test_management_ai_postgres_bootstrap_contract.py
```

Both commands passed (10 tests). The new
`scripts/test_deploy_nonprod_telemetry_prune.py` asserts the truncate
predicate no longer includes `'public'`, that the public-schema refusal guard
exists ahead of the truncate loop, and that the existing dev/postgres/env-flag
gating around the function is unchanged.
