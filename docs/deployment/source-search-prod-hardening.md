# Source/Search Production Hardening

Status: task evidence for `SVC-SOURCE-SEARCH-PROD-HARDENING`
Date: 2026-04-30

## Production Posture

Set `PANTHEON_SOURCE_SEARCH_POSTURE=production` for staging/prod source-search stacks.
In this mode both services fail closed during startup unless the durable storage
posture is complete:

- `DATABASE_URL` is a Postgres DSN.
- `SOURCE_INGEST_EVIDENCE_BACKEND=postgres`.
- `SEARCH_INDEX_STORE_BACKEND=postgres`.
- `SEARCH_EVIDENCE_BACKEND=postgres`.
- `SEARCH_DURABLE_INDEX_ONLY=true`.
- `PANTHEON_S3_ENDPOINT`, `PANTHEON_ARTIFACT_BUCKET`,
  `PANTHEON_S3_ACCESS_KEY`, and `PANTHEON_S3_SECRET_KEY` are configured.

Dev rollback remains available by leaving `PANTHEON_SOURCE_SEARCH_POSTURE=dev`
and selecting the JSONL backends.

This posture is not a live-data off switch. It guards production durability,
auditability, and replay integrity for read-only ingestion/search. Live
fail-closed belongs to order-capable broker/execution paths: order placement,
cancel/replace, position changes, and capital movement.

## Health, Metrics, Alerts

`source-ingest` and `search-svc` expose posture status on:

- `/readyz`: returns `503` if posture dependencies are not ok.
- `/metrics`: includes `posture_alert_count`.
- `/health`: legacy payload includes `source_search_posture`.

Production readiness requires `posture_alert_count=0`,
`source_search_posture.enforced=true`, and
`source_search_posture.object_store_configured=true`.

## Smoke

Run the broader honest-stack smoke first when validating the full end-to-end
source/search path. Then run this posture smoke as the post-deploy readiness
check.

After the production env is loaded and both services are running:

```bash
SOURCE_INGEST_URL=http://127.0.0.1:8097 \
SEARCH_URL=http://127.0.0.1:8098 \
scripts/smoke_source_search_prod_posture.py
```

The broader honest-stack smoke still covers the end-to-end source/search flow:
connector configuration, external feed fetch, durable evidence persistence,
ingest-completion index trigger, freshness, governed query, snapshot replay,
DLQ replay, scheduled ingest, and materialized index replay.

For the credentialed or live-test external connector path, use the task-scoped
smoke harness:

```bash
SOURCE_INGEST_URL=http://127.0.0.1:8097 \
SEARCH_URL=http://127.0.0.1:8098 \
SOURCE_SEARCH_LIVE_FEED_URL=https://allowlisted.example.test/feed.json \
SOURCE_SEARCH_LIVE_ALLOWED_URL_PREFIXES=https://allowlisted.example.test/ \
SOURCE_SEARCH_LIVE_SECRET_REF_ID=env://SOURCE_VENDOR_API_KEY \
scripts/run_source_search_live_connector_smoke.py
```

The feed must emit the governed source-ingest external-feed contract
(`{"records": [...]}`), including entitlement, license/PIT, and
`available_time` fields required by news/social/alpha DB records. If no
credentialed/test feed target is configured, the harness writes explicit
`dependency_missing` evidence instead of claiming live proof.
