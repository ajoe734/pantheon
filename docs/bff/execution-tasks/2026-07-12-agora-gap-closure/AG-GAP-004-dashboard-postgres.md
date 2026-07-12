# AG-GAP-004 — durable dashboard recipe storage

Dashboard recipe identity, append-only versions, idempotency keys, and recipe
feedback now use an injectable store instead of router-owned process memory.
`MemoryDashboardRecipeStore` remains the isolated test/default backend;
`PostgresDashboardRecipeStore` owns the `agora.dashboard_recipe_*` tables.

Dev Compose selects Postgres with `AGORA_DASHBOARD_STORE_BACKEND=postgres`,
`AGORA_DASHBOARD_STORE_DSN`, and `AGORA_DASHBOARD_STORE_SCHEMA=agora`. Startup
logging reports backend/store type without rendering the DSN.

Version mutation uses a compare-and-swap update of `active_version` in the same
transaction that appends the immutable version row. Existing ETag computation,
version history, and rollback-as-new-version behavior are unchanged; a raced
writer fails closed with HTTP 409.

Recipe creation reserves an `Idempotency-Key` in the same Postgres transaction
that inserts identity and version 1. Concurrent requests using the same key
serialize on the unique key and both resolve to the recorded recipe; they cannot
create orphan duplicate identities before the idempotency mapping is visible.

Focused validation:

```text
pytest -q services/control-plane/bff/tests/test_agora_dashboard_store.py
AGORA_DASHBOARD_TEST_POSTGRES_DSN=postgresql://... pytest -q services/control-plane/bff/tests/test_agora_dashboard_store.py
pytest -q services/control-plane/tests/agora/test_cross_user_isolation.py
```

Deployment acceptance still requires creating/editing a recipe, restarting the
BFF container, reading it back with the same ETag/version, and executing a
rollback that appends (rather than overwrites) the next version.
