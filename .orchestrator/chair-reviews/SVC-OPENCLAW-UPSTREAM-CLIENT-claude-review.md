# Review: SVC-OPENCLAW-UPSTREAM-CLIENT

Reviewer: Claude  
Date: 2026-04-30  
Status: **APPROVED**

## Acceptance Criteria Verification

### 1. Typed client covers health / capabilities / sessions list / get / create / cancel ✅

`OpenClawUpstreamClient` provides:
- `get_capabilities()` → `GET /api/capabilities`
- `list_sessions()` → `GET /api/sessions` + normalization
- `get_session(session_id)` → `GET /api/sessions/{id}`
- `create_session(req)` → `POST /api/sessions`
- `cancel_session(session_id)` → `POST /api/sessions/{id}/cancel`

Health probing via `_probe_upstream()` and `_upstream_health_dep()`.

### 2. Timeouts, retries, and upstream error mapping are explicit ✅

- `OPENCLAW_UPSTREAM_TIMEOUT` and `OPENCLAW_UPSTREAM_RETRIES` wired from env vars.
- `httpx.TimeoutException` → `UPSTREAM_TIMEOUT` (504, retryable=True).
- `httpx.ConnectError / NetworkError` → `UPSTREAM_UNAVAILABLE` (503, retryable=True).
- `_map_http_status` covers 401/403, 404, 409, 429, and >=500 explicitly; 5xx are retryable, 4xx are not (except 409/429).
- Retry loop: `attempts = retries + 1` with per-attempt re-raise on last attempt.
- JSON decode failure → `UPSTREAM_INVALID_JSON` (502, non-retryable).
- Schema mismatch → `UPSTREAM_SCHEMA_ERROR` (502, non-retryable).

### 3. Missing or unhealthy upstream degrades cleanly ✅

- Empty `OPENCLAW_GATEWAY_URL` raises `UPSTREAM_NOT_CONFIGURED` (503).
- `/api/openclaw-adapter/capabilities` always returns the static snapshot with `activation_state: "upstream_client_degraded"` and degrades the upstream key; never throws 5xx.
- `/api/openclaw-adapter/sessions` returns `{"status": "upstream_unavailable", "sessions": [], "note": "..."}` on degraded upstream.
- Session normalization via `_normalize_session` ensures consistent schema regardless of upstream field naming variations.

### 4. No broker / paper / live execution is enabled ✅

- `_PRODUCTION_BROKER_ENABLED`, `_PAPER_ADAPTER_ENABLED`, `_LIVE_ADAPTER_ENABLED`, `_CAPITAL_BINDING_ENABLED` all default to `False`.
- Docker compose explicitly sets all four gates to `"false"` (verified by `test_compose_activation.py`).
- `TestProductionGuard` asserts all four are disabled in the default module scope.
- `TestCapabilityFenceCompleteness` asserts no execution paths are `"enabled"` in the capabilities payload.

### 5. Unit tests use fake upstream ✅

- Most route tests use `MagicMock` injected via `patch.object(adapter_main, "_client", ...)`.
- Low-level HTTP tests use `httpx.MockTransport` — no real network calls.
- Timeout and retry tests simulate transport-level failures without a real server.

## Test Run

```
pytest -q services/openclaw-gateway-adapter/test_main.py \
       services/openclaw-gateway-adapter/test_compose_activation.py
30 passed in 1.46s
```

`py_compile` clean on `main.py`, `test_main.py`, `test_compose_activation.py`.

## OPENCLAW_RUNTIME_CONTRACT.md

Section §2.2 updated correctly: upstream client surface, degraded mode semantics, and timeout/retry env vars are all documented. Repo-truth note (2026-04-30) in the doc preamble accurately describes the delivered state.

## Summary

All five acceptance criteria are met. Implementation is clean, fail-closed by default, and the typed client surface matches the contract spec. Returned to Codex2 for task-scoped commit and `done` closeout.
