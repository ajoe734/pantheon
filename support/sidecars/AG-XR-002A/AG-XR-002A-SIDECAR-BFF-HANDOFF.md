# AG-XR-002A Sidecar: BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Task ID | `AG-XR-002A-SIDECAR-BFF-HANDOFF` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-XR-002A` - Regenerate execute-plans Agora types to v1.1 + compat manifest frontend half |
| Parent owner / reviewer | Codex / Claude |
| Sidecar owner / reviewer | Codex2 / Codex |
| Date | 2026-06-21 |
| Mutates canonical truth | false |
| Status | Ready for sidecar review after task commit |

## Purpose

This support-only packet gives the `AG-XR-002A` parent owner and reviewer a
compact BFF and frontend handoff for the Agora v1.1 contract/typegen refresh.
It does not modify canonical truth, OpenAPI, schema bundles, BFF runtime code,
route registries, governance policy, OpenClaw adapter code, or execute-plans
frontend source.

The parent task is a frontend contract synchronization slice. Its current
active status says the parent produced a validated execute-plans checkpoint at
`08b8c96d2f977bf27a973bfb6d2a1e2166e13d80`, including v1.1 generated types,
contract drift validation, manifest validation with `--allow-pending`, manifest
pytest, and `npm build:agora`. This sidecar does not re-review that frontend
commit. It packages the backend contract and BFF query risks that the parent
should carry into frontend review and downstream AG-XR/AG-BE/AG-FE work.

## Sources Used

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_xr_002a_sidecar_bff_handoff.md` | Sidecar scope and support-only boundary |
| `AI_COLLABORATION_GUIDE.md` and `ai-status.json` via `AI_NAME=Codex2 ./scripts/ai-status.sh show ...` | Active owner, reviewer, helper kind, and parent checkpoint |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/01_latest_dev_findings.md` | Records the frozen v1 gap and the reason v1.1 is additive |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/02_schema_coexistence_and_migration.md` | Requires v1/v1.1 coexistence and explicit `WidgetSpecV1`/`WidgetSpecV2` type names |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/03_servant_and_workshop_contracts.md` | Servant and canonical `/bff/agora/workshops` contract notes |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/04_dashboard_crud_and_concurrency.md` | DashboardRecipe v2 route, ETag, version, and mutation semantics |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/06_compatibility_manifest_and_hash_rules.md` | Cross-repo manifest path, commit, hash, and deployment gate rules |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/07_dispatch_unblock_matrix_v2.md` | Downstream unblock matrix and execution order |
| `services/control-plane/specs/agora/bundle_index.json` | Immutable AG-XR-001 base bundle |
| `services/control-plane/specs/agora/bundle_index.v1_1.json` | Additive Agora v1.1 extension bundle index |
| `services/control-plane/openapi/agora_v1_1.openapi.yaml` | v1.1 route/type generation source |
| `services/control-plane/specs/agora/v2/capability_manifest_v1_1.json` | Required v1.1 capability extension |
| `services/control-plane/specs/agora/v2/compatibility_manifest.schema.json` | Manifest schema consumed by both repos |
| `services/control-plane/bff/agora/router.py` | Current `/bff/agora/me` and `/bff/agora/capabilities` live route behavior |
| `services/control-plane/bff/agora/servant/router.py` | Current implemented `POST /bff/agora/servant/ensure` behavior |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Current workshop package router is still a placeholder |
| `services/control-plane/bff/agora/dashboard/router.py` | Current dashboard v2 in-memory route implementation |
| `scripts/agora_compat_manifest.py` and `scripts/test_agora_compat_manifest.py` | Backend manifest generator, validator, deployment gate, and tests |

## Contract Package Snapshot

The v1.1 work must preserve the frozen `AG-XR-001` bundle and add the v1.1
bundle beside it. Do not rewrite the v1 files in place.

| File | Role | SHA-256 observed in this worktree |
|---|---|---|
| `services/control-plane/specs/agora/bundle_index.json` | Frozen v1 base bundle | `286891c6bb900d6b5e9f9037d357c2016f8ecac33927056556a848f95fb4bd0b` |
| `services/control-plane/specs/agora/bundle_index.v1_1.json` | Additive v1.1 extension bundle | `5f875202966d1e373ab325b7107de8355798f1e3f55cdac2548aa74607a821ee` |
| `services/control-plane/openapi/agora_v1_1.openapi.yaml` | v1.1 OpenAPI/typegen source | `16aa660db15a32aaccd63a7f0594abb4339e9ae95afae18353fbee532c2c0749` |
| `services/control-plane/specs/agora/v2/capability_manifest_v1_1.json` | v1.1 capability extension | `6a729d1284ca8f88058a4c301dc67a4c17fd76097190bf020310f4f2cab3db41` |
| `services/control-plane/specs/agora/v2/compatibility_manifest.schema.json` | Cross-repo manifest schema | `84c3607195484d09710708c08e7c29821b75d83199376cd5374a2ce0c3ca7827` |

v1.1 required capabilities from the extension manifest:

| Capability | Version | Browser-facing prefixes | Current handoff note |
|---|---:|---|---|
| `agora.servant.v1` | `1.0` | `/bff/agora/servant` | Contract includes full servant facade; current BFF code implements only `POST /bff/agora/servant/ensure` in this package router. |
| `agora.workshop.v1` | `1.1` | `/bff/agora/workshops` | Canonical route family is now `/bff/agora/workshops`; current package router is still a placeholder. |
| `agora.dashboard.v2` | `2.0` | `/bff/agora/dashboard-recipes`, `/bff/agora/strategies`, `/bff/agora/widgets` | Current package router implements the v2 dashboard route family with in-memory backing and ETag-style concurrency. |

## Current BFF Surface Snapshot

### Identity and capability readiness

| Route | Current code state | Handoff consequence |
|---|---|---|
| `GET /bff/agora/me` | Implemented in `services/control-plane/bff/agora/router.py`; returns authenticated Agora identity scope and `agora.identity.v1` envelope. | Frontend shell can use this as the first live readiness call. |
| `GET /bff/agora/capabilities` | Implemented in `services/control-plane/bff/agora/router.py`, but it reads frozen v1 `capability_manifest.json`, not `v2/capability_manifest_v1_1.json`. | Do not use this live route alone to prove v1.1 capabilities are advertised. Parent/runtime owner should decide whether to merge or expose the extension manifest in BFF readback. |

### Servant facade

v1.1 OpenAPI lists the servant route family:

```text
GET  /bff/agora/servant
POST /bff/agora/servant/ensure
POST /bff/agora/servant/reconcile
POST /bff/agora/servant/sessions
GET  /bff/agora/servant/sessions/{session_id}
POST /bff/agora/servant/sessions/{session_id}/messages
POST /bff/agora/servant/sessions/{session_id}/terminate
GET  /bff/agora/servant/sessions/{session_id}/stream
```

Current BFF package code implements only `POST /bff/agora/servant/ensure`.
Observed behavior:

- requires authenticated identity, `Idempotency-Key`, and `X-Request-Id`;
- derives tenant/user from auth scope, not from client-supplied user ids;
- finds or creates a stable `agora_servant` persona profile;
- calls the server-side OpenClaw servant sync hook;
- returns an `agora.servant.v1` envelope with the stable servant profile,
  request id, and idempotency key;
- preserves `execution_authority = none` and prohibits `runtime_binding`,
  `broker_order`, and `capital_binding`.

Frontend implication: type generation can include the full v1.1 servant family,
but the UI must not assume live BFF support for servant profile GET, reconcile,
session message, terminate, or stream until runtime evidence verifies those
handlers.

### Strategy Workshop

v1.1 OpenAPI lists the canonical workshop route family:

```text
GET  /bff/agora/workshops
POST /bff/agora/workshops
GET  /bff/agora/workshops/{workshop_id}
POST /bff/agora/workshops/{workshop_id}/messages
GET  /bff/agora/workshops/{workshop_id}/events
GET  /bff/agora/workshops/{workshop_id}/completeness
GET  /bff/agora/workshops/{workshop_id}/versions
POST /bff/agora/workshops/{workshop_id}/versions
POST /bff/agora/workshops/{workshop_id}/versions/{version_id}/select
POST /bff/agora/workshops/{workshop_id}/research-runs
POST /bff/agora/workshops/{workshop_id}/consultations
POST /bff/agora/workshops/{workshop_id}/conclude
GET  /bff/agora/workshops/{workshop_id}/stream
```

Current BFF package code does not implement these `/workshops` handlers.
`services/control-plane/bff/agora/strategy_workshop/router.py` documents legacy
committee, training, evaluation, skill coaching, and persona-lab routes still
living elsewhere, then returns an empty router.

Frontend implication: generated v1.1 types and route helpers should use
`/bff/agora/workshops` as the canonical future path, but strict live UI must
show backend-not-ready or blocked state until the runtime route family exists.
Do not silently remap the new workshop aggregate to legacy committee aliases
without parent-owner approval.

### DashboardRecipe and WidgetSpec v2

v1.1 OpenAPI and `04_dashboard_crud_and_concurrency.md` define these dashboard
routes:

```text
GET  /bff/agora/strategies/{strategy_id}/dashboard-recipes
POST /bff/agora/strategies/{strategy_id}/dashboard-recipes/proposals
GET  /bff/agora/dashboard-recipes/{recipe_id}
POST /bff/agora/dashboard-recipes/{recipe_id}/accept
PATCH /bff/agora/dashboard-recipes/{recipe_id}/layout
POST /bff/agora/dashboard-recipes/{recipe_id}/rollback
POST /bff/agora/dashboard-recipes/{recipe_id}/feedback
GET  /bff/agora/dashboard-recipes/{recipe_id}/versions
POST /bff/agora/widgets/validate
POST /bff/agora/widgets/{widget_id}/feedback
POST /bff/agora/widgets/propose-plugin
```

Current BFF package code implements this route family in
`services/control-plane/bff/agora/dashboard/router.py`. Important handoff
constraints:

- implementation uses in-memory stores in the BFF process;
- `GET /dashboard-recipes/{recipe_id}` returns an `ETag`;
- accept, layout patch, and rollback require `If-Match`, `expected_version`,
  and idempotency support;
- layout patch operations are restricted to the contract allowlist;
- `POST /bff/agora/widgets/validate` validates against
  `widget_registry.v1.json` and blocks forbidden interactions such as order,
  live, capital, broker, runtime binding, and management-route actions;
- feedback and plugin proposals are append-only records in the current process.

Frontend implication: the typegen side can safely carry `DashboardRecipeV2`,
`WidgetSpecV2`, and `ChartSpecV1` as v1.1 contract shapes. Product UI should
still label runtime evidence according to the actual backing store maturity.
In-memory BFF state is useful for dev contracts and tests, not durable delivery
proof by itself.

## BFF Query Gap Matrix

| Gap | Current evidence | Why it matters for AG-XR-002A | Suggested parent disposition |
|---|---|---|---|
| Live capability route is still v1 | `GET /bff/agora/capabilities` reads `capability_manifest.json`, while v1.1 extension lives at `v2/capability_manifest_v1_1.json`. | A frontend or deployment gate that reads live BFF capabilities may not see `agora.servant.v1`, `agora.workshop.v1` v1.1 prefixes, or `agora.dashboard.v2`. | Parent should keep manifest/typegen validation based on the v1.1 files, and open/assign a runtime readback follow-up if live capability discovery must advertise v1.1. |
| Servant OpenAPI is wider than current BFF handlers | OpenAPI lists servant GET/reconcile/session routes; current `servant/router.py` implements only `POST /ensure`. | Generated clients may expose methods that strict live BFF cannot satisfy yet. | Mark non-ensure servant methods as generated contract surface only until runtime handlers and tests land. |
| Workshop canonical family is contract-only in package router | OpenAPI/manifest define `/bff/agora/workshops`; current `strategy_workshop/router.py` returns an empty router. | New Trading Desk or Strategy Workshop UI cannot truthfully claim live BFF workshop aggregate support. | Keep `/workshops` helpers behind a backend-readiness state; do not map them to legacy committee aliases without explicit parent decision. |
| Dashboard route backing is in-memory | BFF implements dashboard v2 handlers with process-local stores. | UI can pass contract smoke tests but still lacks durable recipe delivery evidence if persistence is required. | Label dashboard runtime evidence as dev/in-memory unless a downstream AG-BE-DB persistence slice verifies durable storage. |
| WidgetSpec v1/v2 coexistence must be explicit | `02_schema_coexistence...` requires legacy WidgetSpec v1 and new WidgetSpec v2 to coexist without silent coercion. | AG-XR-002A generated types must not collapse both schemas into one `WidgetSpec`. | Review `types.ts` for explicit `WidgetSpecV1` and `WidgetSpecV2`, and make legacy projection failures visible as `LEGACY_WIDGET_MAPPING_REQUIRED`. |
| Cross-repo manifest can be `pending` while typegen is useful | `scripts/agora_compat_manifest.py` allows `verify --allow-pending`; deployment gate fails if status is not `compatible`. | Parent can validate generated types before final frontend/runtime commit parity, but must not claim deploy compatibility until hashes and commits match. | Record `--allow-pending` separately from deployment-gate pass. Final deploy evidence must set `compatibility_status=compatible` with no blocking reasons. |
| Local execute-plans checkout is stale | `/home/lupin/code/execute-plans` is on `main` at `6346300647251322a05ae9991d633c1c53135117`, ahead 2 and behind 467, and lacks AG-XR-002A target files. | Reviewers should not use that checkout as evidence for the parent v1.1 generated types. | Use the parent task branch/PR/checkpoint that contains commit `08b8c96d2f977bf27a973bfb6d2a1e2166e13d80` or a later reviewed descendant. |

## Operator and Frontend Journey

Use this as a frontend/BFF smoke and review path for the parent or downstream
workers. This is not a new canonical workflow.

1. Contract preflight:
   - Verify the frontend generated snapshot says Agora contract `1.1`.
   - Verify the frontend source bundle points at
     `services/control-plane/specs/agora/bundle_index.v1_1.json`.
   - Verify generated types include explicit `WidgetSpecV1`,
     `WidgetSpecV2`, `DashboardRecipeV2`, and `ChartSpecV1`.
2. Manifest preflight:
   - Compare frontend and backend `base_bundle_index_sha256`,
     `extension_bundle_index_sha256`, and `openapi_sha256`.
   - Compare `frontend.generated_from_contract_commit` with
     `backend.contract_commit`.
   - Treat `verify --allow-pending` as contract validation, not deployment
     approval.
3. Agora identity preflight:
   - Call `GET /bff/agora/me` with an Agora-audience operator session.
   - If `GET /bff/agora/capabilities` is used, record that current BFF code
     serves the frozen v1 manifest and may not prove v1.1 capability readback.
4. Servant readiness:
   - For ensure smoke, call `POST /bff/agora/servant/ensure` only with
     `Authorization`, `Idempotency-Key`, and `X-Request-Id`.
   - Render 401/403/422/503 as backend-owned readiness states. Do not create a
     local servant profile in strict live mode.
5. Workshop readiness:
   - Treat `/bff/agora/workshops` route helpers as generated v1.1 contract
     surface until live BFF handlers are present.
   - The Strategy Workshop page should show backend-not-ready or use a
     parent-approved legacy route path. It should not seed a successful live
     aggregate in strict mode.
6. Dashboard readiness:
   - Start with `POST /strategies/{strategy_id}/dashboard-recipes/proposals`,
     then `GET /dashboard-recipes/{recipe_id}` to capture the latest `ETag`.
   - For accept, layout patch, or rollback, send `If-Match`,
     `Idempotency-Key`, and `expected_version`.
   - On 409, reload `latest_href`; do not overwrite local state.
   - Treat validator errors from `/bff/agora/widgets/validate` as authoritative.
7. Safety boundary:
   - No Agora UI in this slice may expose runtime binding, broker order,
     capital binding, live enablement, or management-only route actions.
   - Unknown generated methods, missing runtime routes, or missing capability
     readback should disable CTAs rather than falling back to local mock success.

## Frontend Handoff Materials

Expected execute-plans artifacts for the parent branch:

| Frontend artifact | Review expectation |
|---|---|
| `src/lib/bff-v1/agora/types.ts` | Generated from v1.1 OpenAPI/schema bundle; keeps explicit v1/v2 type names. |
| `src/lib/bff-v1/agora/contract-snapshot.json` | Records contract version `1.1` and source bundle `services/control-plane/specs/agora/bundle_index.v1_1.json`. |
| `scripts/contract-drift-check.mjs` | Rejects frontend drift against the v1.1 backend source. |
| `docs/contracts/agora/dev-compatibility-manifest.json` | Frontend half records runtime/generated contract commits and backend hash parity. |

Reviewers should prefer the parent branch or PR that contains the reported
`08b8c96d2f977bf27a973bfb6d2a1e2166e13d80` checkpoint. The stale local
`/home/lupin/code/execute-plans` checkout does not contain the expected
AG-XR-002A files and should not be used as the frontend evidence source.

## Parent Absorption Checklist

Codex should decide which of these items are absorbed into AG-XR-002A review,
and which become follow-up runtime or frontend tasks:

| Check | Expected outcome |
|---|---|
| v1/v1.1 coexistence | Generated TypeScript does not overwrite or collapse frozen v1 contracts. |
| Hash parity | Frontend manifest fields match backend base bundle, extension bundle, and OpenAPI hashes. |
| Commit parity | `frontend.generated_from_contract_commit == backend.contract_commit` before final compatible deployment claims. |
| Capability readback | Decide whether `GET /bff/agora/capabilities` must advertise v1.1 extension capabilities now or in a follow-up. |
| Servant runtime gap | Mark non-ensure servant methods as contract-only unless BFF handlers and tests are present. |
| Workshop runtime gap | Keep `/bff/agora/workshops` UI behind backend-readiness until package handlers land. |
| Dashboard maturity | Distinguish v2 route/validator contract support from durable persistence support. |
| Strict frontend behavior | No seed/mock success in strict live mode for missing v1.1 BFF routes. |
| Safety boundary | No runtime, broker, live-capital, or cross-audience authority leaks through generated clients or UI. |

## Verification Notes

Verification run by Codex2 on 2026-06-21:

```bash
git diff --check -- support/sidecars/AG-XR-002A/AG-XR-002A-SIDECAR-BFF-HANDOFF.md
python3 scripts/agora_schema_bundle.py --verify
python3 -m pytest scripts/test_agora_compat_manifest.py -q
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py -q
```

Result:

- `git diff --check`: passed.
- `agora_schema_bundle.py --verify`: passed for the frozen v1 schema bundle.
- `scripts/test_agora_compat_manifest.py`: 4 passed.
- `services/control-plane/bff/tests/test_agora_router.py`: 18 passed.

Suggested reviewer commands for this sidecar:

```bash
git diff --check -- support/sidecars/AG-XR-002A/AG-XR-002A-SIDECAR-BFF-HANDOFF.md
python3 scripts/agora_schema_bundle.py --verify
python3 -m pytest scripts/test_agora_compat_manifest.py -q
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py -q
```

Expected scope check:

- Only this sidecar support artifact is authored by the task.
- No L1 canonical docs, OpenAPI, schema bundle, BFF runtime implementation,
  route registry, governance code, OpenClaw adapter code, or execute-plans files
  are changed by this sidecar.
- The packet does not claim AG-XR-002A, AG-XR-003, AG-BE-ID-002, AG-BE-SW-001,
  AG-BE-DB-001, or AG-FE-DB-001 are complete.

## Handoff

This packet is ready for Codex review after the task commit. It should be used
as support material for AG-XR-002A frontend contract review and for downstream
runtime/frontend owners who need to separate v1.1 generated contract surface
from live BFF route readiness.
