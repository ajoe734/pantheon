# Source Connector Framework

Status: implemented for `SVC-SOURCE-CONNECTOR-FRAMEWORK`
Last updated: 2026-04-30

## Runtime Contract

`source-ingest` owns the governed connector registry for the source/search plane.
Connectors are configured through:

- `POST /api/source-ingest/connectors`
- `GET /api/source-ingest/registry`
- `POST /api/source-ingest/jobs`
- `PUT /api/source-ingest/connectors/{connector_id}/schedule`
- `POST /api/source-ingest/run-scheduled`

The framework contract is `SourceConnector` plus the SDK-style provider
surface in `services/source_ingestion/connectors`. A provider exposes:

- governed connector metadata: source type, provider, status, modes
- auth policy with secret refs only, no inline secrets
- rate-limit policy and optional policy ref
- license policy and allowed-use tags
- source metadata for operator/search surfaces
- fetch config using the same `static_records` or `external_feed` schema

## Safety Rules

External feed config is bounded and allowlisted:

- URL scheme must be `http`, `https`, or `file`.
- URL user/password credentials are rejected.
- sensitive query parameters such as `token`, `api_key`, `password`, and
  `client_secret` are rejected.
- inline secret headers/config keys are rejected; use `secret_ref_id`.
- `max_bytes`, `max_records`, and service-level `SOURCE_INGEST_MAX_RECORDS`
  enforce bounded fetches.
- redirects must remain within `allowed_url_prefixes`.

Auth-bearing connectors must use a secret ref such as
`env://OPENALEX_API_KEY`, `vault://...`, or another non-secret reference id.

## BFF Read Path

The operator BFF reads connector metadata through the source-ingest service
client using one of:

- `PANTHEON_SOURCE_INGEST_API_URL`
- `PANTHEON_SOURCE_INGEST_URL`
- `SOURCE_INGEST_URL`

The BFF endpoint is:

- `GET /api/v1/research/source-connectors`

It returns the registry entries and provider examples without exposing raw
secret material.
