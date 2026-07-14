# AG-GAP-002: Durable Postgres store for trading_room

## Scope

`services/control-plane/bff/agora/trading_room/store.py` is an in-memory
singleton ("Not durable — each restart starts empty"). Workspaces, proposals,
widget revisions, version history, decision events, and governed intent
handoffs all vanish on restart, which contradicts the production definition
already met by the FE workflow gate.

## Work

1. Implement a Postgres-backed trading_room store modeled on
   `PostgresWorkshopStore` (schema DDL, tenant/user scoping columns, indexes).
2. Keep the memory store as default; select backend via
   `AGORA_TRADING_ROOM_STORE_BACKEND=postgres` + DSN (same convention as
   AG-GAP-001).
3. Preserve every existing invariant: `no_order_route_proof` enforcement,
   ETag/If-Match versioning, tenant/user isolation (403 on cross-user).
4. Enable on dev after merge.

## Acceptance

- All routes in `trading_room/router.py` behave identically on both backends;
  `test_trading_room.py` runs against both (parametrized or duplicated suite).
- Live restart-persistence proof: accept a workspace proposal, restart BFF,
  workspace + version history read back intact with the same ETag lineage.
- Cross-user isolation proof repeated against the Postgres backend.
- Post-deploy live curl proof recorded under `docs/deployment/evidence/ag-gap-002/`.

## References

- `services/control-plane/bff/agora/trading_room/store.py:1-30`
- `services/control-plane/bff/agora/trading_room/router.py`
- `services/control-plane/specs/agora/v6/` (trading_room_workspace contracts)
