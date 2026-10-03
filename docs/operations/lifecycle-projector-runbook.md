# Lifecycle projection schema bootstrap

The projector opens `ProjectionStore` with `bootstrap=False`. An empty database
therefore requires the existing versioned SQL migration before runtime startup.
Schema DDL belongs to the migration connection; runtime receives only schema
USAGE and SELECT/INSERT/UPDATE/DELETE on the seven projection tables.

Dev `root` and `bff` deployment paths run a bounded, one-shot bootstrap from the
sealed candidate projector image before starting that runtime. The existing
PostgreSQL service must be healthy. Its resolved Compose credentials pass through
stdin to the migration CLI, so `.env` overrides are honored without copying
credentials into the live projector or writing a config artifact. Failure enters
the existing deployment compensation path. Existing readiness gates still apply.

For an isolated database, use the same entry point explicitly before starting a
projector outside the dev deployment script:

```bash
export LIFECYCLE_PROJECTOR_PROJECTION_DSN='<runtime connection>'
export LIFECYCLE_PROJECTOR_PROJECTION_SCHEMA=trade_journey_projection
python -m scripts.lifecycle_projector_migrate --bootstrap-only \
  --dsn '<migration connection>' --schema "$LIFECYCLE_PROJECTOR_PROJECTION_SCHEMA"
```

Both connections must resolve to the same database/server, with distinct users.
The schema must match the runtime configuration. A superuser, role administrator,
schema/table owner (including inherited ownership), or role with CREATE on the
projection schema is rejected as a runtime identity. Fix the configured authority
through its existing owner; bootstrap does not transfer ownership or revoke
other services' privileges. Grants are limited to projection tables, excluding
unrelated tables even inside that schema.

The migration and grants commit together. Reruns preserve receipts, owner data,
controller revisions and live checkpoints. A DDL/grant error rolls the transaction
back and prevents candidate startup. Bootstrap creates no controller row and
cannot certify accepted-live readiness; normal source polling and existing gates
provide that evidence. Backfill remains a separate mode requiring controller and
snapshot arguments.

Validation for DEV-PROJECTION-BOOTSTRAP-20261003 uses disposable local PostgreSQL
and source contracts. It does not establish hosted deployment or acceptance.
