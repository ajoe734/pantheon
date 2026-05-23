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

## B7 — Agora Compatibility APIs

### Gap

The execute-plans Agora workbench still references six historical Agora route
names that had only registry-level `implemented_by_alias` coverage. The canonical
BFF read models already existed, but the legacy path names were not registered
as live FastAPI routes. In strict/live mode this could make a frontend route
probe see a 404 even though the canonical surface was available.

### Fix

**File: `services/control-plane/bff/main.py`**

Register the six historical Agora names as canonical read aliases on the
existing handlers:

| Compatibility path | Canonical handler/source |
|---|---|
| `GET /bff/agora/markets` | `GET /bff/agora/watchlist` |
| `GET /bff/agora/committee-sessions` | `GET /bff/agora/sessions` |
| `GET /bff/agora/market-notes` | `GET /bff/agora/notes` |
| `GET /bff/agora/decision-journal` | `GET /bff/agora/journal` |
| `GET /bff/agora/research-tasks` | `GET /bff/research/tasks` |
| `GET /bff/agora/incoming` | `GET /bff/agora/handoffs` |

These aliases do not introduce new write authority, fallback data, or separate
DTO projections. They share the canonical handler, auth gate, pagination
parameters, response envelope, and read-surface metadata.

### Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | All six B7 compatibility paths are registered in FastAPI and return HTTP 200 with seeded local read-store data | ✅ test added |
| 2 | Each alias returns the same item IDs as its canonical route | ✅ test added |
| 3 | Each alias reports the same read-surface `status` and `source` as its canonical route | ✅ test added |
| 4 | Aliases preserve the existing read-role auth gate and do not add write authority | ✅ implemented by shared handlers |

### Affected Files

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/test_bff_b2_005_agora_canonical_aliases.py`
- `docs/04/pantheon_bff_api_gap_2026-05-23/BFF_API_GAP_final_integration_spec.md`

### Task

BFF-B2-005 — Owner: Codex, Reviewer: Claude2
