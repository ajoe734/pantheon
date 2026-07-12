# AG-GAP-004: Durable Postgres store for dashboard recipes

## Scope

`services/control-plane/bff/agora/dashboard/router.py:68-79` keeps recipe
identity, versions, layouts, feedback, and rollback state in module-level
in-memory dicts. Accepted dashboard recipes and their version history are lost
on every BFF restart.

## Work

1. Extract the module-level dicts into a store interface with memory and
   Postgres implementations (`AGORA_DASHBOARD_STORE_BACKEND=postgres`),
   following the AG-GAP-001/002 convention.
2. Preserve ETag/If-Match semantics, version lineage, widget registry
   validation, and tenant/user scoping.
3. Enable on dev after merge.

## Acceptance

- All 11 dashboard-recipe routes behave identically on both backends.
- Live restart-persistence proof: accept a recipe, add a layout change and a
  rollback, restart BFF, version history reads back intact.
- Post-deploy live curl proof recorded under `docs/deployment/evidence/ag-gap-004/`.

## References

- `services/control-plane/bff/agora/dashboard/router.py:68-79`
- `services/control-plane/specs/agora/v2/` (dashboard_recipe_v2, widget_spec_v2)
