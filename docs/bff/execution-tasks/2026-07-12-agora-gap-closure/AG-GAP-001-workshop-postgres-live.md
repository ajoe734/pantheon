# AG-GAP-001: Enable and prove durable workshop Postgres backend on dev

## Scope

`services/control-plane/bff/agora/strategy_workshop/store.py` ships a complete
`PostgresWorkshopStore` (line ~380) but the factory defaults to
`MemoryWorkshopStore` unless `AGORA_WORKSHOP_STORE_BACKEND=postgres` plus a DSN
is set. PR #3021 (2026-07-05) claimed the dev cutover; this task verifies and
enforces it end to end so workshop state survives BFF restarts on dev.

## Work

1. Inspect the dev BFF deployment (compose/env on `pantheon-lupin-dev`) and
   record whether `AGORA_WORKSHOP_STORE_BACKEND=postgres` and the DSN are set.
2. If not set, wire them into the deploy configuration (versioned, not
   hand-edited on the VM) and redeploy.
3. Add a startup log line stating which workshop store backend is active so
   future audits do not need VM access.
4. If the backend was already on, record that as the finding; do not rebuild.

## Acceptance

- Deploy config in git shows the Postgres backend and DSN wiring for dev.
- BFF startup log states the active workshop store backend.
- Live proof: create a workshop via `/bff/agora/workshops`, restart the BFF
  container, read the workshop back with the same scoped identity.
- Existing workshop tests stay green (memory and postgres paths).
- Post-deploy live curl proof recorded under `docs/deployment/evidence/ag-gap-001/`.

## References

- `services/control-plane/bff/agora/strategy_workshop/store.py:380,1115`
- `docs/bff/execution-tasks/2026-07-05-agora-dynui-full-production-recovery/INDEX.md` (PR #3021)
- Assessment: `docs/04/pantheon_agora_gap_assessment_2026-07-12/INDEX.md`
