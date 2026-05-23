# BFF API GAP — Final Integration Spec

Status: active
Date: 2026-05-23
Sprint: Sprint BFF-1 / EPIC-BFF-GAP-P0
Owner: Claude

This document records the BFF API integration gaps identified as of 2026-05-23 and the
resolution specification for each. Gaps are numbered by section. Each section records
the gap, the canonical fix, and the acceptance criteria.

---

## §11 Session Bootstrap

### Gap

The execute-plans strict-mode bootstrap requires a canonical current-session endpoint
that can replace seed/mock session state. The existing BFF surface exposed `/bff/me`,
but the payload did not make the frontend bootstrap fields explicit at `data.*`, and
the response did not echo the caller correlation ID in both metadata and the
`X-Correlation-Id` response header.

### Fix

**File: `services/control-plane/bff/main.py`**

- Keep `/bff/me` as the canonical session bootstrap path.
- Preserve existing nested compatibility objects (`data.user`, `data.currentUser`,
  `data.tenant`, `data.session`, `data.feature_flags`).
- Add explicit bootstrap aliases at `data.*`: `operatorId`, `operator_id`, `roles`,
  `tenantId`, `tenant_id`, `allowedTenants`, `allowed_tenants`, `locale`,
  `sessionKind`, `session_kind`, `capabilities`, `featureFlags`, and `feature_flags`.
- Echo `X-Correlation-Id` into the response header and `meta.correlationId`; generate
  a route-scoped correlation ID when the request omits one.
- Attach the same correlation ID to route-scoped typed auth errors so anonymous
  bootstrap failures remain machine-readable.

**File: `execute-plans/src/lib/bff-v1/paths.ts`**

- No path change required. `paths.me()` already resolves to `/bff/me`; deprecated
  session aliases continue to target the canonical path.

### Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | Authenticated `GET /bff/me` returns `operatorId`, `roles`, `tenantId`, `allowedTenants`, `locale`, `sessionKind`, `capabilities`, and `featureFlags` under `data` | Implemented in BFF-B1-003 |
| 2 | Anonymous `GET /bff/me` returns HTTP 401 with the typed BFF error envelope | Implemented in BFF-B1-003 |
| 3 | Request `X-Correlation-Id` is echoed as `X-Correlation-Id` and `meta.correlationId` | Implemented in BFF-B1-003 |
| 4 | Existing nested payload fields used by current BFF clients remain available | Implemented in BFF-B1-003 |

### Affected Files

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/tests/test_bff_me_session_bootstrap.py`
- `docs/04/pantheon_bff_api_gap_2026-05-23/BFF_API_GAP_final_integration_spec.md`
- `execute-plans/src/lib/bff-v1/paths.ts` (verified; no code change)

### Task

BFF-B1-003 — Owner: Codex, Reviewer: Claude

---

## §12 POST /bff/auth/refresh — Cookie or Bearer Refresh

### Gap

The strict-mode frontend can call `POST /bff/auth/refresh` from either a browser
cookie session or an injected bearer-token session. The prior route only refreshed
after the shared auth facade accepted the current request credential directly, but
it did not make refresh credential resolution explicit and could not distinguish
"no refresh path" from a generic auth failure.

### Fix

**File: `services/control-plane/bff/main.py`**

- Resolve refresh credentials in this order: request body `refresh_token` /
  `refreshToken`, `X-Refresh-Token`, `pantheon_refresh` /
  `pantheon_refresh_token` cookie, `pantheon_session` cookie, then
  `Authorization: Bearer ...` for backwards compatibility.
- Validate the selected credential through the existing BFF auth facade and keep
  `_require_read_role` as the role gate.
- Preserve the `BFF-LUV-SEM-001` current-session DTO envelope, idempotency replay,
  and idempotency conflict behaviour.
- Persist `last_refreshed_at` and `last_refresh_credential_source` in the BFF
  session lifecycle store so refresh source and session freshness are visible in
  the response.
- Return a typed HTTP 401 `INVALID_TOKEN` error with
  `details.reason = "AUTH_REFRESH_CREDENTIAL_REQUIRED"` and
  `precondition_failed = "refresh_credential"` when no body/header/cookie/bearer
  refresh path is present.
- Add `X-Refresh-Token` to the CORS allow-header list for browser refresh-token
  handoff.

### Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | Bearer refresh credential returns HTTP 200, `operation.type = "refresh"`, and records refresh credential source as `bearer` | ✅ test added |
| 2 | Cookie refresh credential returns HTTP 200 with `session.session_kind = "cookie"` and records refresh credential source as `refresh_cookie` | ✅ test added |
| 3 | Missing body/header/cookie/bearer refresh credential returns typed HTTP 401 without a raw 500 | ✅ test added |
| 4 | Existing `pantheon_session` cookie and `Authorization: Bearer ...` compatibility paths remain covered by the session auth contract tests | ✅ preserved |

### Affected Files

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/tests/test_bff_auth_refresh.py`
- `services/control-plane/bff/test_bff_session_auth_me_contract.py`
- `docs/04/pantheon_bff_api_gap_2026-05-23/BFF_API_GAP_final_integration_spec.md`

### Task

BFF-B1-005 — Owner: Codex2, Reviewer: Claude

---

## §13 Command / Action Compatibility

### Gap

The execute-plans strict-mode write path needs `POST /bff/v1/commands` to be the
canonical command admission facade while legacy `/api/v1/operator/commands` status
polling and `/bff/actions/*` compatibility remain intact. The facade must accept the
frontend command schema, preserve idempotency and trace headers, project a
`CommandResponse<T>` envelope, and fail closed for live broker scope when live broker
execution is not explicitly enabled.

### Fix

**File: `services/control-plane/bff/main.py`**

- Keep `POST /bff/v1/commands` as the final BFF command admission route.
- Accept the final command payload shape:
  `command`, `target`, optional `action`, `params`, `audit_context`, and
  top-level precondition aliases `confirmToken`, `approvalDecisionId`, and
  `twoManSignatureId` (with snake_case aliases also supported).
- Require operator authentication and a header idempotency key. `Idempotency-Key`
  is canonical; `X-Idempotency-Key` remains a temporary compatibility alias when
  the canonical header is absent.
- Reject `idempotencyKey` / `idempotency_key` in the body.
- Propagate `X-Correlation-Id`, `X-Request-Id`, `X-Trace-Id`, and the resolved
  idempotency key into the foundation command trace and persisted audit record.
- Persist commands through the shared command store used by
  `/api/v1/operator/commands`; the response `trackingUrl` points to
  `GET /api/v1/operator/commands/{command_id}` so existing operator polling stays
  compatible.
- Return `CommandResponse<T>` with `status=accepted`, `data.receipt_id`,
  `data.command_id` / `data.commandId`, `data.trackingUrl`, and
  `meta.idempotency`.
- Replay duplicate idempotency keys with the same request hash and return HTTP 409
  `IDEMPOTENCY_CONFLICT` when the same key is reused with a different body.
- Preserve the live broker fail-closed gate: payloads or runtime targets that signal
  live broker scope return HTTP 403 unless `PANTHEON_LIVE_BROKER_ENABLED=true`.
- Keep the deprecated action compatibility facade
  `POST /bff/actions/{entityType}/{entityId}/{actionId}` live. It must translate
  the path and body into the final command envelope, persist through the same command
  store, mark `admission_route=POST /bff/v1/commands`, preserve
  `source_route=POST /bff/actions/{entityType}/{entityId}/{actionId}` for audit,
  emit deprecation headers/body metadata, and reject body-level idempotency keys
  with no command side effect.

`/api/v1/operator/commands` remains available as the legacy foundation route and
continues to return `CommandSubmissionResponse`; it is not changed to the final
`CommandResponse<T>` shape.

### Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | `POST /bff/v1/commands` accepts final command schema fields (`command`, `target`, `action`, `params`, `audit_context`, `confirmToken`, `approvalDecisionId`, `twoManSignatureId`) | Implemented in BFF-B1-007 |
| 2 | `Authorization`, `X-Correlation-Id`, `X-Request-Id`, and resolved `Idempotency-Key` / `X-Idempotency-Key` are honored and persisted in command foundation trace/audit | Implemented in BFF-B1-007 |
| 3 | Response is `CommandResponse<T>` with accepted status, command receipt identifiers, `trackingUrl`, and `meta.idempotency` | Implemented in BFF-B1-007 |
| 4 | Duplicate idempotency key with the same payload replays the original receipt | Implemented in BFF-B1-007 |
| 5 | Duplicate idempotency key with a different payload returns HTTP 409 `IDEMPOTENCY_CONFLICT` | Implemented in BFF-B1-007 |
| 6 | Live broker scope remains fail-closed when `PANTHEON_LIVE_BROKER_ENABLED` is false | Implemented in BFF-B1-007 |
| 7 | Legacy `/api/v1/operator/commands` remains unaffected and keeps `CommandSubmissionResponse` | Implemented in BFF-B1-007 |
| 8 | `POST /bff/actions/{entityType}/{entityId}/{actionId}` remains route-discoverable and adapts accepted calls through the final command admission facade with deprecation metadata | Implemented in BFF-B1-008 |
| 9 | Action facade requests honor `Idempotency-Key` / `X-Idempotency-Key`, persist the resolved key in foundation context, and reject body-level idempotency keys before command-store writes | Implemented in BFF-B1-008 |
| 10 | Action facade policy denials preserve final-command foundation error/audit metadata with `source_route=POST /bff/actions/{entityType}/{entityId}/{actionId}` | Implemented in BFF-B1-008 |

### Affected Files

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/tests/test_actions_to_commands_adapter.py`
- `services/control-plane/bff/test_governance_command_submission.py`
- `docs/04/pantheon_bff_api_gap_2026-05-23/BFF_API_GAP_final_integration_spec.md`

### Task

- BFF-B1-007 — Owner: Codex, Reviewer: Claude
- BFF-B1-008 — Owner: Codex, Reviewer: Claude

---

## §14 Confirm-Token Lifecycle

### Gap

The execute-plans high-risk action flow needs the confirm-token lifecycle to be
observable through both the canonical token routes and the legacy
command-confirmation compatibility routes. The backend already had command-store-backed
token create/read/redeem/delete behavior, but the BFF gap inventory requires the P0
five-endpoint surface to be explicit:

| ID | Method | Path |
|---|---|---|
| B1-012 | POST | `/bff/confirm-tokens` |
| B1-013 | GET | `/bff/confirm-tokens/{tokenId}` |
| B1-014 | POST | `/bff/confirm-tokens/{tokenId}/redeem` |
| B1-015 | POST | `/bff/command-confirmations` |
| B1-016 | GET | `/bff/command-confirmations/{token}` |

The compatibility route also needed a read endpoint, and expired issued tokens needed
to fail closed with a typed HTTP 410 envelope instead of falling through as an
unstructured server error.

### Fix

**File: `services/control-plane/bff/main.py`**

- Kept `POST /bff/confirm-tokens` as the canonical token issue route and preserved
  stable replay semantics for server-generated token IDs.
- Projected `GET /bff/confirm-tokens/{tokenId}` from the command store with lifecycle
  states: `available`, `created`, `redeemed`, `deleted`, or `expired`.
- Updated `POST /bff/confirm-tokens/{tokenId}/redeem` to return token lifecycle fields
  (`data.id`, `data.tokenId`, `data.status=redeemed`, `data.redeemed=true`) while
  preserving the accepted command receipt envelope.
- Added `GET /bff/command-confirmations/{token}`.
- Updated `POST /bff/command-confirmations` to accept `confirm_token`/`confirmToken`
  aliases, write a matching confirm-token redeem record, and return token lifecycle
  fields while preserving the legacy flat response shape.
- Added typed expired-token handling for token read/redeem and command-confirmation
  read/write paths: HTTP 410 with `INVALID_STATE` and
  `precondition_failed=confirm_token_expired`.

`DELETE /bff/confirm-tokens/{tokenId}` remains available as an existing compatibility
route and now also returns `data.tokenId`, `data.status=deleted`, and `data.deleted=true`.

### Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | `POST /bff/confirm-tokens` issues a token and returns `data.tokenId` / `data.status=created` | Implemented in BFF-B1-009 |
| 2 | `GET /bff/confirm-tokens/{tokenId}` returns the current token lifecycle state | Implemented in BFF-B1-009 |
| 3 | `POST /bff/confirm-tokens/{tokenId}/redeem` marks the token redeemed and preserves the command receipt | Implemented in BFF-B1-009 |
| 4 | `POST /bff/command-confirmations` mirrors the lifecycle by marking the token redeemed | Implemented in BFF-B1-009 |
| 5 | `GET /bff/command-confirmations/{token}` returns the mirrored confirmation lifecycle state | Implemented in BFF-B1-009 |
| 6 | Expired issued tokens return typed HTTP 410 (`INVALID_STATE`, `confirm_token_expired`) on read/redeem/confirmation paths | Implemented in BFF-B1-009 |

### Affected Files

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/tests/test_confirm_token_lifecycle.py`
- `services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py`
- `docs/04/pantheon_bff_api_gap_2026-05-23/BFF_API_GAP_final_integration_spec.md`

### Task

BFF-B1-009 — Owner: Codex2, Reviewer: Claude

---

## §15 CORS — Lovable Preview and Published Origins

### Gap

The BFF CORS allowlist had two deficiencies that caused browser `CORS` failures for
Lovable-hosted frontend deployments:

1. **Missing execute-plans project origin.** The Lovable project that hosts the
   `execute-plans` frontend has UUID `140c41d5-9cd8-4d6b-ba02-66d5941d0dbe`. Its
   published preview URL `https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com`
   was absent from `_DEFAULT_LOVABLE_CORS_ORIGINS` and from the `docker-compose.yml`
   default. This caused CORS rejections from the Lovable in-IDE preview pane.

2. **Dynamic preview URL format not handled.** Lovable per-commit preview URLs follow
   the format `https://id-preview-<commit_hash>--<project_uuid>.lovable.app`. The commit
   hash changes with every deployment, so exact-match allowlists cannot enumerate them.
   The previous list had the static entry
   `https://id-preview--b75d3452-...lovable.app` (no commit hash), which does not match
   any real Lovable-generated URL. Observed live preview URL:
   `https://id-preview-a7067bd5--140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovable.app`.

### Fix

**File: `services/control-plane/bff/main.py`**

- Added `https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com` to
  `_DEFAULT_LOVABLE_CORS_ORIGINS` (and `_DEV_LOVABLE_CORS_ORIGINS` so it is filtered
  out in production strict mode).

- Added `_LOVABLE_PREVIEW_UUIDS`, `_LOVABLE_PREVIEW_ORIGIN_REGEX`, and
  `_LOVABLE_PREVIEW_ORIGIN_PATTERN` constants covering both known project UUIDs
  (`b75d3452-f667-4cf4-893a-1061de45b347` and `140c41d5-9cd8-4d6b-ba02-66d5941d0dbe`).

- Updated `_build_bff_app()` to pass `allow_origin_regex` to `CORSMiddleware` when
  not in production strict mode. This enables the FastAPI/Starlette middleware to accept
  `id-preview-<commit>--<uuid>.lovable.app` origins without enumerating every commit.

- Updated `_cors_origin_allowed()` to check the regex pattern in addition to the exact
  match list (non-strict mode only).

**File: `docker-compose.yml`**

- Added `https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com` to the
  `PANTHEON_BFF_CORS_ORIGINS` default value.

### Production Strict Mode Boundary

- The regex is suppressed (`allow_origin_regex` is not set) when `_is_production_strict_mode()`
  is `True` (i.e., `PANTHEON_BFF_AUTH_MODE=strict` + `PANTHEON_ENV` or
  `PANTHEON_DEPLOYMENT_STAGE` in `{canary, live, prod, production, staging-live}`).
- `https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com` is in
  `_DEV_LOVABLE_CORS_ORIGINS` and is therefore filtered from the allowlist in
  production strict mode, matching the existing policy for dev-tier origins.

### Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | `_cors_origins_from_env()` in dev mode includes `140c41d5...lovableproject.com` | ✅ test added |
| 2 | `_cors_origins_from_env()` in production strict mode excludes the above origin | ✅ test added |
| 3 | CORS preflight for `id-preview-<commit>--140c41d5-...lovable.app` returns 200 in non-strict | ✅ test added |
| 4 | CORS preflight for `id-preview-<commit>--b75d3452-...lovable.app` returns 200 in non-strict | ✅ test added |
| 5 | CORS preflight for an unknown UUID preview URL is rejected | ✅ test added |
| 6 | CORS preflight for known-UUID preview URL is rejected in production strict mode | ✅ test added |
| 7 | `_cors_origin_allowed()` returns `True` for known-UUID preview URL in non-strict | ✅ test added |
| 8 | `_cors_origin_allowed()` returns `False` for unknown-UUID preview URL | ✅ test added |
| 9 | `pytest services/control-plane/bff/tests/test_auth_jwks_strict.py` passes 15 tests | ✅ verified |

### Affected Files

- `services/control-plane/bff/main.py`
- `docker-compose.yml`
- `services/control-plane/bff/tests/test_auth_jwks_strict.py`

### Task

BFF-B1-001 — Owner: Claude, Reviewer: Codex

---

## §16 PATCH /bff/me/locale — Operator Locale Preference

### Gap

The BFF session surface had no write path for the operator locale preference.
Clients that needed to persist a locale choice had to resubmit `X-Locale` on
every request; there was no way to store the preference server-side in the
session and have it reflected back on subsequent `GET /bff/me` calls.

### Fix

**File: `services/control-plane/bff/main.py`**

The `PATCH /bff/me/locale` endpoint was added with the following behaviour:

- Requires a valid Bearer token with at least the `operator` role (same gate as
  all BFF session mutation endpoints via `_require_read_role`).
- Accepts `{"locale": "<BCP-47-ish tag>"}` in the request body.
- Validates the submitted value through `_normalize_locale`; returns HTTP 400
  `INVALID_PARAMS` when the value is absent, empty, or fails the BCP-47-ish
  regex (`[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*`).
- Normalises the tag (lowercase language, uppercase 2-char region, title-cased
  4-char script).
- Persists the normalised value in the session store via
  `session_lifecycle_store.upsert_session` so subsequent `GET /bff/me` calls
  reflect `locale.source = "session"`.
- Returns the full `_sem_session_current_response` envelope with
  `data.operation.type = "update_locale"`, `data.locale.resolved` set to the
  submitted value, and `data.locale.source = "session"`.

No changes were made to `execute-plans` paths; the frontend can call this
endpoint after the user changes their locale in the UI to make the preference
durable across page loads.

### Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | Authenticated `PATCH /bff/me/locale` with a valid BCP-47 tag returns HTTP 200 with `data.locale.resolved` equal to the submitted tag | ✅ test added |
| 2 | Response `data.locale.source` is `"session"` | ✅ test added |
| 3 | Response `data.operation.type` is `"update_locale"` | ✅ test added |
| 4 | Locale tag is normalised (e.g. `ZH-tw` → `zh-TW`) | ✅ test added |
| 5 | A subsequent `GET /bff/me` from the same session reflects the persisted locale | ✅ test added |
| 6 | Anonymous `PATCH /bff/me/locale` returns HTTP 401 | ✅ test added |
| 7 | Missing `locale` field returns HTTP 400 `INVALID_PARAMS` with `precondition_failed: "locale"` | ✅ test added |
| 8 | Invalid locale tag (e.g. single-char sub-tag `not-a`) returns HTTP 400 `INVALID_PARAMS` | ✅ test added |
| 9 | `pytest services/control-plane/bff/tests/test_bff_me_locale.py` passes 6 tests | ✅ verified |

### Affected Files

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/tests/test_bff_me_locale.py`
- `docs/04/pantheon_bff_api_gap_2026-05-23/BFF_API_GAP_final_integration_spec.md`

### Task

BFF-B1-004 — Owner: Claude, Reviewer: Codex

---

## §17 POST /bff/logout — Clear Session

### Gap

The BFF session surface lacked a specified, tested logout endpoint. The `POST /bff/logout`
route existed in `main.py` but had no spec section and no test coverage, leaving the
session-clear behaviour undocumented and unverified against the acceptance bar required
by Sprint BFF-1.

### Fix

**File: `services/control-plane/bff/main.py`**

The `POST /bff/logout` endpoint is implemented with the following behaviour:

- Requires a valid Bearer token or cookie session with at least the `operator` role
  (via `_require_read_role`).
- Accepts an optional JSON body (ignored beyond idempotency-key hashing).
- Accepts optional `Idempotency-Key` / `X-Idempotency-Key` headers for safe retries.
- Writes `{"state": "logged_out", "logged_out_at": <iso-timestamp>}` into the
  session-scoped lifecycle store entry for the caller.
- Clears the `pantheon_session` cookie with `Set-Cookie: ... Max-Age=0`.
- Returns the full `_sem_session_current_response` envelope with:
  - `data.operation.type = "logout"`
  - `data.session.state = "logged_out"`
  - `data.session.authenticated = false`
  - `data.session.fresh = false`
  - `data.session.logged_out_at = <iso-timestamp>`
  - `meta.idempotency.idempotencyKey` and `meta.idempotency.replayed` for replay tracking.
- Subsequent `GET /bff/me` from the same bearer/cookie session returns HTTP 401 with
  typed BFF error reason `SESSION_LOGGED_OUT`; the BFF no longer bootstraps a
  logged-out DTO as an authenticated session.
- If an idempotency key is reused with a different payload, returns HTTP 409
  `IDEMPOTENCY_CONFLICT`.

**File: `services/control-plane/bff/tests/test_bff_logout.py`** (new)

Seven focused tests covering the acceptance criteria below, plus the existing
session lifecycle contract tests updated for cookie clearing and logged-out 401
semantics.

### Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | Authenticated `POST /bff/logout` returns HTTP 200 with `data.operation.type = "logout"`, `data.session.state = "logged_out"`, and `data.session.authenticated = false` | ✅ test added |
| 2 | A subsequent `GET /bff/me` from the same bearer/session returns HTTP 401 `INVALID_TOKEN` with reason `SESSION_LOGGED_OUT` | ✅ test added |
| 3 | Anonymous `POST /bff/logout` returns HTTP 401 | ✅ test added |
| 4 | Idempotent logout: same idempotency key returns the same response with `meta.idempotency.replayed = true` | ✅ test added |
| 5 | Reusing an idempotency key with a different payload returns HTTP 409 `IDEMPOTENCY_CONFLICT` | ✅ test added |
| 6 | Response `data.session.logged_out_at` is present and `data.session.fresh` is `false` | ✅ test added |
| 7 | Cookie-backed logout clears `pantheon_session` and a follow-up `GET /bff/me` returns HTTP 401 | ✅ test added |
| 8 | `pytest services/control-plane/bff/tests/test_bff_logout.py services/control-plane/bff/test_bff_session_auth_me_contract.py` passes | ✅ verified |

### Affected Files

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/tests/test_bff_logout.py`
- `services/control-plane/bff/test_bff_session_auth_me_contract.py`
- `docs/04/pantheon_bff_api_gap_2026-05-23/BFF_API_GAP_final_integration_spec.md`

### Task

BFF-B1-006 — Owner: Codex2, Reviewer: Claude

---

## B7 — Agora Compatibility APIs

### Gap

The execute-plans Agora workbench depends on six strict/live read surfaces for
its core bootstrap path. Most of the backing read models already existed, but
the task needed one focused acceptance slice that proves the routes return BFF
envelopes and that `/bff/agora/inbox` is not a single-dataset shortcut.

### Fix

**File: `services/control-plane/bff/main.py`**

Verify and preserve these six Agora compatibility reads:

| Compatibility path | Canonical handler/source |
|---|---|
| `GET /bff/agora/ask/sessions` | `agora_sessions` filtered to `mode=quick_ask` |
| `GET /bff/agora/ask/sessions/{id}` | `agora_sessions` detail / ask SSE resync route |
| `GET /bff/agora/signals` | `agora_signals` |
| `GET /bff/agora/journal` | `decision_journal_entries` |
| `GET /bff/agora/postmortems` | `postmortems` |
| `GET /bff/agora/inbox` | composed `insight_cards` + `agora_signals` + `research_tickets` |

The inbox route now returns a composed list with stable `inboxType` and
`sourceDataset` markers while preserving the standard `data`, `items`,
`page_info`, and `meta.surfaces` BFF envelope. The previously added historical
read aliases (`/markets`, `/committee-sessions`, `/market-notes`,
`/decision-journal`, `/research-tasks`, `/incoming`) remain registered on their
canonical handlers; this task does not add write authority or a separate DTO
projection.

### Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | `/bff/agora/ask/sessions` and `/bff/agora/ask/sessions/{id}` return live envelopes from the existing Agora session store | ✅ test added |
| 2 | `/bff/agora/signals`, `/bff/agora/journal`, and `/bff/agora/postmortems` return BFF read envelopes with seeded local read-store data | ✅ test added |
| 3 | `/bff/agora/inbox` composes insight cards, signals, and research tasks with per-source surface metadata | ✅ implemented and tested |
| 4 | Historical Agora read aliases still share their canonical handler outputs and read-surface metadata | ✅ preserved by existing test |
| 5 | Compatibility reads preserve the existing read-role auth gate and do not add write authority | ✅ implemented by shared handlers |

### Affected Files

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/test_bff_b2_005_agora_canonical_aliases.py`
- `docs/04/pantheon_bff_api_gap_2026-05-23/BFF_API_GAP_final_integration_spec.md`

### Task

BFF-B2-005 — Owner: Codex, Reviewer: Claude2
