# Review: SVC-SOURCE-CONNECTOR-FRAMEWORK

Reviewer: Claude
Date: 2026-04-30
Status: APPROVED

## Acceptance Criteria Verification

### 1. connector base interface supports auth secrets rate limits license and source metadata ✅

`services/source_ingestion/connectors/base.py` defines:
- `SecretRef` (frozen dataclass): `secret_ref_id`, `required_scopes`, `rotation_policy_ref` — validated with `_validate_public_ref()`, no inline secrets possible
- `AuthPolicy` (frozen dataclass): `auth_type`, `secret_ref`, `auth_scope`, `audience` — enforces `secret_ref` is required for non-NONE auth types
- `RateLimitPolicy` (frozen dataclass): `requests_per_minute`, `burst`, `retry_after_seconds`, `concurrency`, `policy_ref` — all numeric fields validated > 0
- `LicensePolicy` (frozen dataclass): `license_scope`, `allowed_use`, `attribution_required`, `redistribution_allowed`, `policy_ref`
- `SourceMetadata` (frozen dataclass): `display_name`, `homepage_url`, `docs_url`, `owner`, `tags`
- `SourceConnector` composes all of the above, with `policy_summary()` exposing the full policy surface

### 2. static external feed and at least one provider example share the same contract ✅

`services/source_ingestion/connectors/examples.py`:
- `StaticRecordsProviderExample` and `ExternalFeedProviderExample` both implement `SourceConnectorProvider` protocol
- Both expose `connector() -> SourceConnector` and `fetch_config() -> Mapping[str, Any]`
- `example_provider_catalog()` returns both: `example-static-notes` (static_records) and `example-openalex-feed` (external_feed)
- Both use the same connector SDK field layout — auth, license, rate_limit, source_metadata all present

### 3. config validation rejects unsafe urls secrets and overlarge payloads ✅

URL safety (`configured.py`):
- `_validate_feed_url()`: scheme restricted to http/https/file; inline user/password rejected; sensitive query params (token, api_key, password, client_secret, etc.) rejected
- Redirect safety: `_fetch_external_payload()` re-validates the final URL after redirect against `allowed_url_prefixes`
- Bounds: `timeout_seconds > 0 and <= 30`, `max_bytes > 0 and <= 10,000,000`, `max_records > 0 and <= 1000`

Secret safety:
- `_assert_no_inline_secret_mapping()` (base.py): rejects inline secret values in any mapping passed to connector/auth config
- `_reject_inline_fetch_secrets()` (configured.py): rejects inline secrets in fetch config keys
- `_validate_public_ref()` (base.py): enforces secret ref format — must match URI or safe-token regex, no whitespace, no `=` assignment

### 4. source registry exposes connector status and policy ✅

`GET /api/source-ingest/registry` (main.py):
- Returns `schema_version: "source_connector_registry.v1"`, list of connector entries, and `provider_examples`
- Each entry includes: `connector_id`, `provider`, `source_type`, `status`, `supported_modes`, `policy` (auth/rate_limit/license/source_metadata), `fetch_policy`, `schedule`, `state`
- `_connector_registry_entry()` builds a deterministic summary that does not expose raw secret material

### 5. BFF can read connector metadata through service client ✅

`services/control-plane/bff/read_store.py`:
- `ReadSurfaceStore.get_source_connector_registry()`: HTTP GET `/api/source-ingest/registry` via `_http_json_get`
- URL resolved from env: `PANTHEON_SOURCE_INGEST_API_URL`, `PANTHEON_SOURCE_INGEST_URL`, `SOURCE_INGEST_URL`
- Returns `source: "service_client"` on success, `source: "unavailable"` on failure (no snapshot fallback)

`services/control-plane/bff/main.py`:
- `GET /api/v1/research/source-connectors` endpoint uses `read_store.get_source_connector_registry()`
- Returns registry entries and provider examples without exposing raw secrets

`docker-compose.yml`:
- BFF service env has `PANTHEON_SOURCE_INGEST_API_URL: http://source-ingest:8097` — compose wiring complete

## Verification

```
PYTHONPYCACHEPREFIX=/tmp/pantheon-pycache python3 -m py_compile \
  services/source_ingestion/connectors/base.py \
  services/source_ingestion/connectors/examples.py \
  services/source_ingestion/configured.py \
  services/source_ingestion/main.py \
  services/control-plane/bff/read_store.py \
  services/control-plane/bff/main.py
# → clean

python3 -m pytest services/source_ingestion -q
# → 37 passed

python3 -m pytest services/control-plane/bff/test_source_connector_service_client.py \
  services/control-plane/bff/test_search_service_client.py -q
# → 2 passed
```

## Decision

APPROVED. All 5 acceptance criteria met. Implementation is clean, layered, and security-correct:
- Domain model is immutable frozen dataclasses with validation-on-construction
- No inline secrets can pass validation at any layer (connector config, fetch config, or auth policy)
- SDK provider contract is Protocol-based, allowing both static and external feed examples
- BFF read path is service-backed only — no local snapshot fallback for this surface

Returned to Codex for closeout finalization.
