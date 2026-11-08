# Canonical BFF Path and Header Naming Conventions

This document defines the canonical path, path-parameter, and idempotency-header
conventions for the Pantheon Control-Plane BFF (`services/control-plane/bff/`)
and records the `BFF-FINAL-COMPAT-20261107` audit and retirement disposition for
410 route families and header compatibility aliases (findings **A13** and
**A15**).

---

## 1. Canonical Resource Path Naming

1. **Hyphenated plural resource collections under `/bff/`:**
   - Top-level multi-word collection nouns use hyphenated plural segments rather
     than nested slash sub-collections:
     - `/bff/ranking-formulas` and `/bff/ranking-formulas/{formula_id}`
       (replaces `/bff/ranking/formulas` and `/bff/ranking/formulas/{formula_id}`)
     - `/bff/capital-pools` and `/bff/capital-pools/{pool_id}`
     - `/bff/mcp-servers` and `/bff/mcp-servers/{server_id}`
     - `/bff/mcp-tools` and `/bff/mcp-tools/{tool_id}`
2. **Snake-case FastAPI path parameter templates:**
   - Every route parameter uses an explicit `snake_case` resource identifier
     (`{persona_id}`, `{strategy_id}`, `{formula_id}`, `{pool_id}`,
     `{deployment_id}`, `{rebalance_id}`, `{incident_id}`, `{runtime_id}`,
     `{skill_id}`, `{tool_id}`, `{server_id}`) rather than `{id}` or `camelCase`
     (`{formulaId}`, `{actionId}`).
3. **Canonical operator command admission:**
   - Asynchronous operator commands enter through `POST /bff/v1/commands` with a
     typed command envelope (`command`, `target`, `params`, `audit_context`) or
     through resource-specific command endpoints backed by an authoritative domain
     owner.

---

## 2. Canonical Idempotency Header (`Idempotency-Key`) — Finding A15

1. **Single canonical request header:**
   - All mutating BFF routes and command admission endpoints require the
     canonical `Idempotency-Key` HTTP request header.
   - `DEFAULT_CORS_ALLOW_HEADERS` in
     `services/control-plane/bff/core/http_security.py` exposes `Idempotency-Key`
     (and does not advertise `X-Idempotency-Key`).
2. **Removal of `X-Idempotency-Key` server alias (`BFF-FINAL-COMPAT-20261107`):**
   - Following completion of the frontend client migration in
     `FE-COMPAT-CLEANUP-20261107` (`ajoe734/execute-plans` commit
     `d62f0e4f4f2f32b1ed5d94a90e0e9afd1d90ea7d`, which removed all outbound
     `X-Idempotency-Key` dual-header writes and updated CORS probes), the BFF
     server resolvers (`resolve_final_idempotency_key`,
     `resolve_command_idempotency_key`, and router-level idempotency helpers)
     and FastAPI route signatures no longer accept `X-Idempotency-Key`.
   - Requests supplying only `X-Idempotency-Key` without `Idempotency-Key` are
     rejected with HTTP `400 VALIDATION_FAILED` (`precondition_failed="idempotency_key"`).
   - Rejecting `idempotencyKey`, `idempotency_key`, and `X-Idempotency-Key`
     inside JSON request bodies remains enforced by `reject_body_idempotency_key`.
   - Note on outbound internal service adapters: BFF-internal HTTP clients to
     downstream internal services (`openclaw_ops_client.py`,
     `source_management_client.py`, `source_search_ops_client.py`) retain their
     downstream wire headers because those downstream internal services are
     outside the public BFF ingress contract.

---

## 3. Retired 410 Route Family Inventory & Disposition — Finding A13

### 3.1 External Access-Log Prerequisite Truth

- **Available access-log period in worker environment:** Unavailable
  (`unavailable_external_prerequisite`).
- The repository development/worker environment does not have access to a 7-day
  rolling production/hosted HTTP access-log telemetry store. Per
  `BFF-FINAL-COMPAT-20261107` acceptance criteria, no synthetic logging
  infrastructure is invented and zero external traffic is **not** claimed from
  telemetry. Instead, removal decisions below are strictly bounded to obsolete
  notification wrappers whose supported usage is proven ended across the
  maintained `execute-plans` (`origin/dev`) repository and BFF contract
  registries.

### 3.2 Removed Obsolete 410 Notification Wrappers

| Method & Path | Previous Handler | Canonical Replacement | Consumer & Contract Proof | Disposition |
| --- | --- | --- | --- | --- |
| `GET /bff/ranking/formulas` | `ranking_router.py::bff_deprecated_list_ranking_formulas` | `GET /bff/ranking-formulas` | Zero callers in `execute-plans` (`origin/dev` uses `/bff/ranking-formulas` in `src/hooks/use-capital-ranking.ts`, `e2e/04b`, `e2e/08`, `e2e/19`, `scripts/probe-bff-*.mjs`). Marked `superseded_with_reason` in `execute_plans_bff_routes.json`; alias override removed from `scripts/bff_route_manifest_backend.py`. | **Removed** in `BFF-FINAL-COMPAT-20261107` along with `deprecated_bff_path_response` injection into `create_rankings_long_tail_router`. |
| `POST /bff/ranking/formulas` | `ranking_router.py::bff_deprecated_create_ranking_formula` | `POST /bff/ranking-formulas` | Zero callers in `execute-plans`. Marked `superseded_with_reason` in `execute_plans_bff_routes.json`; alias override removed from `scripts/bff_route_manifest_backend.py`. | **Removed** in `BFF-FINAL-COMPAT-20261107`. |
| `GET /bff/ranking/formulas/{formula_id}` | `ranking_router.py::bff_deprecated_get_ranking_formula` | `GET /bff/ranking-formulas/{formula_id}` | Zero callers in `execute-plans`. Marked `superseded_with_reason` in `execute_plans_bff_routes.json`; alias override removed from `scripts/bff_route_manifest_backend.py`. | **Removed** in `BFF-FINAL-COMPAT-20261107`. |
| `PATCH /bff/ranking/formulas/{formula_id}` | `ranking_router.py::bff_deprecated_patch_ranking_formula` | `PATCH /bff/ranking-formulas/{formula_id}` | Zero callers in `execute-plans`. Marked `superseded_with_reason` in `execute_plans_bff_routes.json`; alias override removed from `scripts/bff_route_manifest_backend.py`. | **Removed** in `BFF-FINAL-COMPAT-20261107`. |

### 3.3 Retained Business Retirement Guards (`reject_retired_command` / `ACTION_RETIRED`)

Per `BFF-FINAL-COMPAT-20261107` acceptance criteria, business retirement guards
that enforce authentication/operator role and reject commands lacking an
authoritative domain executor with `410 ACTION_RETIRED` are **not** deprecated
path notification wrappers and must remain mounted:

- `POST /bff/ranking/formulas/{formula_id}/actions/{action_id}`
  (`ranking_router.py::bff_deprecated_ranking_formula_action`) — verifies
  operator identity and calls `reject_retired_command("RankingFormulaAction")`.
- `POST /bff/ranking-formulas` and `PATCH /bff/ranking-formulas/{formula_id}`
  (`ranking_router.py`) — verify operator identity and call
  `reject_retired_command("RankingFormulaAction")`.
- `POST /bff/rankings/{ranking_id}/actions/{action_id}`
  (`ranking_router.py::bff_ranking_action`) — verifies operator identity and
  calls `reject_retired_command("RankingAction")`.
- `POST /bff/deployments` (`deployment/router.py`), `POST /bff/audit/exports`
  (`incidents/router.py`), `POST /bff/management/promotion-reviews/{review_id}/decisions`
  (`personas/routes/lifecycle.py`), `POST /bff/personas/quarterly-rankings/recommendations`
  (`personas/routes/collection.py`), `POST /bff/mcp-servers/{server_id}/actions/{action_id}`
  (`tools_integrations/router.py`), and governance review creation/stage-approval
  retirement guards (`governance/router.py`).

### 3.4 Retained 410 Notification Wrappers With Active Contract or Consumer Dependencies

1. **MCP alternate URL family (`services/control-plane/bff/tools_integrations/router.py`):**
   - `GET /bff/mcp/servers` (replacement: `/bff/mcp-servers`)
   - `GET /bff/mcp/servers/{server_id}` (replacement: `/bff/mcp-servers/{server_id}`)
   - `POST /bff/mcp/tools/{tool_id}/actions/{action_id}` (replacement: `/bff/mcp-tools/{tool_id}/{action_id}`)
   - **Why retained:** Still referenced as canonical `covered_by` targets in
     `scripts/bff_route_manifest_backend.py` and `execute_plans_bff_routes.json`
     (`tools-mcp-skills` family), coexist with live `POST /bff/mcp/servers` and
     `GET /bff/mcp/servers/{server_id}/tools`, and are covered by active
     capability/contract suites (`test_bff_b2_003_capabilities.py`,
     `test_tools_integrations_router.py`, `test_bff_path_dedupe.py`).
2. **Nested resource action notification wrappers (`replacement="/bff/v1/commands"`):**
   - `POST /bff/strategies/{strategy_id}/actions/{action_id}` (`strategies/routes/detail.py`)
   - `POST /bff/personas/{persona_id}/actions/{action_id}` (`personas/routes/lifecycle.py` — note that lifecycle actions `AdvanceLifecycle`, `Promote`, `PromoteCandidate`, and `Demote` on this route are live command admissions, while non-lifecycle actions return 410)
   - `POST /bff/deployments/{deployment_id}/actions/{action_id}` (`deployment/router.py`)
   - `POST /bff/runtimes/{runtime_id}/actions/{action_id}` (`runtime/router.py`)
   - `POST /bff/tools/{tool_id}/actions/{action_id}` and `POST /bff/skills/{skill_id}/actions/{action_id}` (`tools_integrations/router.py`)
   - **Why retained:** Still referenced in `services/control-plane/bff/action_catalog.py`,
     `execute_plans_bff_routes.json`, `execute-plans` probe/adapter scripts
     (`scripts/probe-persona-onboarding-endpoints.mjs`,
     `src/lib/bff-v1/mocks/adapters.ts`), and BFF router contract tests.
