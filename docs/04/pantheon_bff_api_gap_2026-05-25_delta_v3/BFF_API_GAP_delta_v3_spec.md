# BFF API GAP - Delta V3 Spec

Status: active
Date: 2026-05-25
Sprint: Sprint BFF-DELTA-V3 / EPIC-BFF-DELTA-V3-INFRA

This document records BFF naming alignment decisions found after the 2026-05-24 delta spec. It is
an execution record, not a new L1 product authority. Canonical naming decisions are codified in
`CANONICAL_PATH_NAMING.md` in this directory.

---

## CORS-DELTA-001 — Lovable `id-preview` Strict-Mode Preflight

Route family: BFF CORS preflight for Lovable-hosted frontend origins

### Gap

Lovable emits both static and deploy-hash preview origins:

- `https://id-preview--<project-uuid>.lovable.app`
- `https://id-preview-<hex-commit>--<project-uuid>.lovable.app`

The BFF default allowlist already included the static Pantheon Frontend `id-preview` origin, but
the production-strict filter also classified that exact origin as dev-only and removed it before
Starlette CORS could answer preflight. Separately, the dynamic preview regex required at least one
hex character after `id-preview-`, so the no-hash `id-preview--<uuid>` shape could not match the
regex path.

### Fix

- Keep the static `id-preview--b75d3452-f667-4cf4-893a-1061de45b347.lovable.app` origin in the
  default allowlist, but remove it from `_DEV_LOVABLE_CORS_ORIGINS` so it survives the
  production-strict exact-match filter.
- Change the dynamic preview regex to make the whole `-<hex>` segment optional:
  `id-preview(?:-[a-f0-9]+)?--<project-uuid>`.
- Preserve the hex-only requirement when the deploy hash is present; non-hex prefixes such as
  `id-preview-main--<uuid>` remain rejected.
- Keep the regex disabled in production-strict mode; production-strict allows only exact default
  origins that survive the filter.

### Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | Static `id-preview--b75d...lovable.app` survives production-strict filtering | Implemented |
| 2 | OPTIONS preflight from the static `id-preview--b75d...` origin returns 204 with echoed ACAO | Implemented |
| 3 | Dynamic regex accepts `id-preview-<hex>--140c41d5...lovable.app` in non-production strict mode | Implemented |
| 4 | Dynamic regex accepts `id-preview--140c41d5...lovable.app` in non-production strict mode | Implemented |
| 5 | Dynamic regex rejects non-hex deploy prefixes | Implemented |

### Verification

```bash
python3 -m pytest services/control-plane/bff/tests/test_auth_jwks_strict.py -q
```

Result: 20 passed.

### Affected files

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/tests/test_auth_jwks_strict.py`
- `execute-plans/.lovable/audits/bff-backend-gap-2026-05-25-delta-v4.md`

---

## NAMING-ALIGN-001 — URL Path Segments: kebab-case Canonical

Route family: all BFF management routes

### Gap

The FE paths.ts TypeScript builder names use camelCase (e.g., `managementPortfolioBook`) while the
actual HTTP URL path segments use kebab-case (e.g., `/bff/management/portfolio-book`). There was no
canonical document stating which convention governs which layer.

### Decision

URL path segments are kebab-case canonical. TypeScript builder names are camelCase by TypeScript
convention. The two conventions apply to different layers and do not conflict.

### Affected routes

All management sub-routes:
- `/bff/management/portfolio-book`, `/bff/management/portfolio-book/pools`,
  `/bff/management/portfolio-book/holdings`, `/bff/management/portfolio-book/positions`,
  `/bff/management/portfolio-book/exposure`
- `/bff/management/performance-attribution`, `/bff/management/performance-attribution/by-strategy`,
  `/bff/management/performance-attribution/by-persona`, `/bff/management/performance-attribution/by-pool`
- `/bff/management/persona-league`, `/bff/management/persona-league/movers`,
  `/bff/management/persona-league/rankings`, `/bff/management/persona-league/tiers`,
  `/bff/management/persona-league/heatmap`
- `/bff/management/quarterly-ranking`, `/bff/management/quarterly-ranking/drilldown`,
  `/bff/management/quarterly-ranking/formula`, `/bff/management/quarterly-ranking/recommendations`
- `/bff/management/capital-flow`, `/bff/management/strategy-allocation`,
  `/bff/management/risk-radar`, `/bff/management/cost-attribution`

### Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | All BFF URL path segments use kebab-case | Verified |
| 2 | No BFF route uses snake_case or camelCase URL path segments | Verified |
| 3 | FE builder names use camelCase (TypeScript convention) | Verified |
| 4 | Canonical convention documented in CANONICAL_PATH_NAMING.md | Done |

### Verification

```bash
python3 -c "
import re, subprocess
result = subprocess.run(['grep', '-n', '@app.get\|@app.post\|@app.put\|@app.patch\|@app.delete',
    'services/control-plane/bff/main.py'], capture_output=True, text=True)
routes = [line for line in result.stdout.split('\n') if '/bff/' in line]
bad = [r for r in routes if re.search(r'/bff/[^\"]*_[a-z]', r)]
print('Routes with underscores in path segments:', len(bad))
for b in bad[:5]:
    print(b)
"
```

---

## NAMING-ALIGN-002 — Path Parameters: snake_case Canonical in FastAPI Route Declarations

Route family: strategies, personas, evolution-programs, jobs

### Gap

The execute-plans BFF contract snapshot (2026-05-08) recorded routes like
`GET /bff/strategies/{strategyId}` as "missing" because the BE had not yet implemented them. When
the BE implemented these routes, it used snake_case parameters (`{strategy_id}`, `{persona_id}`).
The evolution-programs family has both `{program_id}` (primary) and `{programId}` (legacy
alias) declarations active. There was no explicit policy governing which form is canonical.

### Decision

snake_case is the canonical form for FastAPI route path parameters. The HTTP path value is
identical regardless of the Python variable name, so FE callers are unaffected. Legacy camelCase
declarations (`{programId}`, `{jobId}`) are retained for backwards compatibility only and must not
be introduced for new routes.

### Affected route declarations

Primary (canonical):
- `GET /bff/strategies/{strategy_id}` and sub-routes
- `GET /bff/personas/{persona_id}` and sub-routes
- `GET /bff/evolution-programs/{program_id}` and sub-routes
- `GET /bff/jobs/{job_id}` and sub-routes

Legacy alias (compatibility only, do not introduce for new routes):
- `GET /bff/evolution-programs/{programId}` and sub-routes
- `GET /bff/jobs/{jobId}` and sub-routes
- `GET /bff/agora/signals/{signalId}` and similar agora routes

### Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | Primary route declarations use snake_case path params | Verified |
| 2 | Legacy camelCase alias routes exist only for compatibility | Verified |
| 3 | No new routes introduced with camelCase path params | Policy enforced |
| 4 | Canonical convention documented in CANONICAL_PATH_NAMING.md § A2 | Done |

---

## NAMING-ALIGN-003 — Query Parameter Dual Accept: `persona_id` / `personaId`

Route: `GET /bff/management/intervention-stream`

### Gap

The FE TypeScript caller sends `personaId` (camelCase) as a query parameter, following TypeScript
naming convention. The BE canonically uses `persona_id` (snake_case) for query parameter names.
Without explicit dual-accept, the FE filter would have been silently ignored.

### Fix (already implemented)

```python
@app.get("/bff/management/intervention-stream")
async def bff_management_intervention_stream(
    persona_id: Optional[str] = Query(default=None),   # canonical
    personaId: Optional[str] = Query(default=None),    # FE-compatibility alias
    ...
):
    return _management_intervention_stream_response(
        persona_id=persona_id or personaId,            # coalesce; canonical wins if both present
        ...
    )
```

### Decision

`persona_id` is canonical. `personaId` is a FE-compatibility alias. When both are present,
`persona_id` takes precedence. This pattern may be applied to other management routes if a FE
caller is confirmed to send the camelCase form.

### Frontend contract

- `paths.managementInterventionStream()` → `GET /bff/management/intervention-stream`
- `managementInterventionStreamPath(query)` → passes `persona_id` or `personaId`
- `fetchManagementInterventionStream(query, init, baseUrl)` → fetches from canonical path

### Backend acceptance

- unauthenticated request: HTTP 401
- authenticated request with `persona_id=<id>`: HTTP 200, filtered by persona
- authenticated request with `personaId=<id>`: HTTP 200, filtered by persona (alias)
- authenticated request with both: HTTP 200, `persona_id` value wins

### Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | `persona_id` query param filters correctly | Implemented |
| 2 | `personaId` query param filters correctly as alias | Implemented |
| 3 | Both present → `persona_id` wins | Implemented |
| 4 | Canonical form documented in CANONICAL_PATH_NAMING.md § A3 | Done |

---

## NAMING-ALIGN-004 — Query Parameter Dual Accept: `window_hours` / `windowHours`

Route: `GET /bff/management/intervention-stream`

### Gap

The FE TypeScript caller may send `windowHours` (camelCase) for the time window parameter, while
the BE uses `window_hours` (snake_case) as the canonical query parameter. Without dual-accept, the
FE window filter would fall back to the default 24-hour window silently.

### Fix (already implemented)

```python
@app.get("/bff/management/intervention-stream")
async def bff_management_intervention_stream(
    window_hours: int = Query(default=24, ge=1, le=720),          # canonical
    windowHours: Optional[int] = Query(default=None, ge=1, le=720),  # FE-compatibility alias
    ...
):
    return _management_intervention_stream_response(
        window_hours=windowHours or window_hours,                  # camelCase alias wins if present
        ...
    )
```

Note: `windowHours or window_hours` means the camelCase form wins if present (non-zero). This is
reversed from A3 because the canonical form has a non-None default (24), so `or`-chaining would
always resolve to the canonical default. New implementations should use explicit `if`-coalesce to
avoid this asymmetry.

### Decision

`window_hours` is canonical. `windowHours` is a FE-compatibility alias. Time-window parameters
always use snake_case as canonical; camelCase aliases added only when a FE caller is confirmed.

### Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | `window_hours` query param sets window correctly | Implemented |
| 2 | `windowHours` query param sets window correctly as alias | Implemented |
| 3 | Default window (24 h) applies when neither is present | Implemented |
| 4 | Canonical form documented in CANONICAL_PATH_NAMING.md § A4 | Done |

---

## NAMING-ALIGN-005 — Request Body Field: `source_type` / `sourceType` in Command Executor

Route: `POST /bff/v1/commands` and downstream command executor

### Gap

The command executor reads `source_type` from request body params. Some FE callers (legacy and
in-process mock paths) send `sourceType` (camelCase). Without explicit alias handling, the
`source_type` field would be empty for camelCase callers.

### Fix (already implemented)

```python
# In _extract_command_params (command executor):
source_type = str(params.get("source_type") or params.get("sourceType") or "").strip()
params["source_type"] = source_type
params["sourceType"] = source_type  # propagate both for any legacy downstream reader
```

### Decision

`source_type` is canonical in request body fields. `sourceType` is a FE-compatibility alias
normalized at the executor boundary. After normalization, both keys carry the same value.

### Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | `source_type` in body sets command source correctly | Implemented |
| 2 | `sourceType` in body is accepted as alias | Implemented |
| 3 | Post-normalization both keys carry same value | Implemented |
| 4 | Canonical form documented in CANONICAL_PATH_NAMING.md § A5 | Done |

---

## SNAKE-DUP-001 through SNAKE-DUP-012 — Response Field Dual Emission

Routes: trading-pulse, human-inbox, intervention-stream, management evidence refs

### Gap

Twelve response body fields are emitted in both snake_case and camelCase form in the same response
object. The practice was established ad-hoc across multiple delta tasks without a unifying policy.
There was no canonical document stating:
- which form is the authoritative one
- when new dual fields may be added
- how FE and BE callers should read these fields

### Decision

snake_case is the canonical (authoritative) form. camelCase is the FE-compatibility alias. Both
forms carry the same value. See CANONICAL_PATH_NAMING.md Section 2 for the complete field table.

### Affected fields (12 canonical)

| ID | Canonical | Alias | Route family |
|---|---|---|---|
| SNAKE-DUP-001 | `runtime_id` | `runtimeId` | trading-pulse, strategy-allocation |
| SNAKE-DUP-002 | `deployment_stage` | `deploymentStage` | trading-pulse, capital-flow |
| SNAKE-DUP-003 | `runtime_binding_id` | `runtimeBindingId` | trading-pulse baseline |
| SNAKE-DUP-004 | `paper_live_drift` | `paperLiveDrift` | trading-pulse baseline |
| SNAKE-DUP-005 | `drift_groups` | `driftGroups` | trading-pulse baseline |
| SNAKE-DUP-006 | `threshold_evaluation` | `thresholdEvaluation` | trading-pulse baseline |
| SNAKE-DUP-007 | `inbox_type` | `inboxType` | management human-inbox |
| SNAKE-DUP-008 | `source_dataset` | `sourceDataset` | human-inbox, intervention-stream |
| SNAKE-DUP-009 | `risk_level` | `riskLevel` | human-inbox, HIQ backlog |
| SNAKE-DUP-010 | `source_record` | `sourceRecord` | human-inbox items |
| SNAKE-DUP-011 | `stream_sequence` | `streamSequence` | intervention-stream events |
| SNAKE-DUP-012 | `management_href` | `managementHref` | evidence ref links |

### Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | All 12 dual fields emit identical values for both forms | Verified |
| 2 | No semantic difference between canonical and alias forms | Verified |
| 3 | FE TypeScript callers can read camelCase form | Verified |
| 4 | BE internal callers can read snake_case form | Verified |
| 5 | Duplication policy documented in CANONICAL_PATH_NAMING.md § 2 | Done |
| 6 | New dual fields require explicit review approval citing this doc | Policy enforced |

### Verification

```bash
# Verify dual fields in trading-pulse response (baseline comparison subobject)
python3 -m pytest services/control-plane/bff/test_bff_management_delta_routes.py \
    -q -k "trading_pulse or baseline" 2>&1 | tail -5

# Verify dual fields in human-inbox response
python3 -m pytest services/control-plane/bff/test_bff_management_delta_routes.py \
    -q -k "human_inbox or intervention_stream" 2>&1 | tail -5
```

### Affected files

- `services/control-plane/bff/main.py`
- `docs/04/pantheon_bff_api_gap_2026-05-25_delta_v3/CANONICAL_PATH_NAMING.md`
- `execute-plans/.lovable/audits/bff-backend-gap-2026-05-25-delta-v3.md`

### Finalization

- Decision commit: task OPS-DOC-BFF-NAMING-CANONICAL-001
- Reviewer: Codex
- No code change required: all dual fields are already implemented. This is a documentation-only
  decision record that canonicalizes the existing practice.
