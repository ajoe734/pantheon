# Handoff Packet: P0-FE-SOURCE-001-SIDECAR-BFF-HANDOFF

Status: ready for reviewer handoff
Owner: Codex2
Reviewer: Codex
Parent task: P0-FE-SOURCE-001
Helper kind: bff_handoff_packet
Scope: support artifact only; no canonical truth, BFF runtime code, frontend code, registry, or governance implementation changes.

## 1. Purpose

This packet supports `P0-FE-SOURCE-001` by collecting the BFF and frontend handoff requirements for adding source mode and runtime identity to critical frontend surfaces.

Parent acceptance requires:

- critical pages show one of `authoritative_bff`, `derived_projection`, `stale_cache`, `preview_mock_only`, `demo_only`, or `unavailable`;
- runtime detail shows `bridge_repo`, `bridge_commit`, `runtime_binding_id`, `deployment_plan_id`, `artifact_id`, and `capital_pool_id`.

This packet is not a contract promotion. The parent owner should decide which items become implementation scope or formal contract updates.

## 2. Evidence Snapshot

Observed repo evidence:

- `docs/04/pantheon_p0_sd/SD-P0-05_Frontend_Production_Adoption_Demo_Cleanup.md` already names source mode badges, route classification, demo cleanup, and the open question of whether `source_mode` comes from BFF response metadata or a frontend wrapper.
- `services/control-plane/bff/BFF_API_CONTRACT.md` defines the governed BFF as read-oriented and uses `meta.staleness` plus `meta.surfaces.*.status` for degradation instead of command-side or browser-invented truth.
- `docs/bff/PKT-010-runtime-state-board.md` already requires `runtime_id`, `runtime_binding_id`, `deployment_stage`, `capital_pool_id`, `plan_ref.plan_id`, `artifact_ref.artifact_id`, telemetry summary, rollback summary, and surface health for `GET /api/v1/operator/runtime-state`.
- `services/control-plane/bff/main.py` currently projects runtime-state rows from runtime bindings and exposes runtime binding detail, deployment plan detail, capital pool detail, and deployment review composed views.
- `services/telemetry/telemetry_event.schema.json` requires telemetry events to carry `binding_id`, `runtime_id`, `capital_pool_id`, `artifact_id`, `artifact_version`, `deployment_stage`, and `plan_id`; bridge repo/commit is not a required telemetry field in that schema.
- `/home/edna/code/front-ai-trading-system` already has live BFF frontend surfaces such as `OperatorRuntimeStateBoard.tsx`, `DeploymentReviewConsole.tsx`, `DeploymentPlanDetail.tsx`, and degradation handling, but source-mode labels and bridge identity fields are not consistently present in the searched frontend paths.

## 3. BFF Query Gaps

### 3.1 Source Mode Is Not Yet A Single Field

Current BFF responses expose enough status metadata to derive part of the UI source mode:

- `meta.surfaces.*.status = ok` can support `authoritative_bff` when the payload is service-backed and fresh.
- `meta.staleness.served_from = cache` can support `stale_cache`.
- `meta.surfaces.*.status = unavailable` can support `unavailable`.
- composed or telemetry-projection routes can support `derived_projection` only when the BFF marks the source as projection-derived.

Gap: the searched BFF surfaces do not show a stable top-level `source_mode` field matching the parent enum. The parent should either:

- add `meta.source_mode` to critical composed responses; or
- define a frontend resolver that maps existing BFF metadata to the parent enum.

Recommendation for P0: prefer BFF-authored `meta.source_mode` on critical composed routes where practical, and allow frontend-derived `source_mode` only for `preview_mock_only` and `demo_only`, because those are UI route/runtime environment classifications rather than BFF data authority.

### 3.2 Bridge Repo And Commit Are Missing From Runtime Detail

Runtime and telemetry evidence currently covers runtime binding identity, capital pool, artifact, deployment stage, and plan references. The parent acceptance also requires:

- `bridge_repo`
- `bridge_commit`

Gap: the searched BFF and telemetry schema evidence did not reveal bridge repo/commit as consistently exposed runtime-detail fields. The source authority for these should be the bootstrap/runtime context path, not frontend constants.

Parent-facing handoff:

- BFF should pass through `bridge_repo` and `bridge_commit` only when they are present in runtime context, runtime binding metadata, or telemetry projection.
- Frontend should render those fields as `unavailable` when absent; it should not infer them from `.gitmodules`, package metadata, or build-time constants.

### 3.3 Field Name Alignment

Existing BFF runtime-state rows use:

- `runtime_binding_id`
- `plan_ref.plan_id`
- `artifact_ref.artifact_id`
- `capital_pool_id`

Existing runtime binding detail uses `plan_id` inside the runtime binding payload, while parent acceptance names `deployment_plan_id`.

Parent-facing handoff:

- Frontend can display `deployment_plan_id` from `plan_ref.plan_id`, nested `deployment_plan.id`, or runtime binding `plan_id`, but the UI type should normalize it to `deployment_plan_id`.
- If none of those are present, emit a `bff-gap` handoff instead of filling a placeholder.

### 3.4 Derived Projection Boundary

`P0-TEL-PROJ-001` is still `todo` and is expected to project paper telemetry into runtime status, including bridge repo, bridge commit, runtime binding ID, and deployment stage.

Parent-facing handoff:

- Treat telemetry-driven runtime status as `derived_projection` unless the BFF can prove it is direct authoritative runtime binding state.
- Do not block rendering of current runtime binding identity on telemetry projection readiness; show runtime binding fields from BFF read routes and mark projected telemetry fields separately.

## 4. Suggested Critical Surface Mapping

| Frontend surface | Primary BFF route or source | Required display for P0-FE-SOURCE-001 | Gap behavior |
|---|---|---|---|
| Runtime State Board | `GET /api/v1/operator/runtime-state` | row-level `source_mode`, `runtime_binding_id`, `deployment_plan_id`, `artifact_id`, `capital_pool_id`, `deployment_stage` | if required row identity is missing, emit `bff-gap` and show unavailable state |
| Runtime Detail | `GET /api/v1/runtime-bindings/{binding_id}` or `GET /api/v1/runtimes/{runtime_id}/status` | full runtime identity including bridge repo/commit when BFF provides it | render bridge repo/commit as unavailable when absent; do not infer |
| Deployment Review / Plan Detail | `GET /api/v1/operator/deployment-plans/{plan_id}` and deployment plan detail route | `source_mode`, plan ID, artifact ID, runtime binding ID when bound, capital pool ID | source mode follows composed-view surface metadata |
| Operator Home / Health | existing operator health/home BFF routes | coarse source mode and degradation banner | no per-runtime identity required unless the screen links to runtime detail |
| Governance / Evolution critical pages | current BFF composed surfaces | source mode and degradation state | no silent demo/mock fallback in staging/prod |

## 5. Frontend Consumption Rules

Use these as implementation guidance for the parent task:

1. Define a shared UI type for:

```ts
type SourceMode =
  | "authoritative_bff"
  | "derived_projection"
  | "stale_cache"
  | "preview_mock_only"
  | "demo_only"
  | "unavailable";

type RuntimeIdentityView = {
  source_mode: SourceMode;
  bridge_repo?: string | null;
  bridge_commit?: string | null;
  runtime_binding_id?: string | null;
  deployment_plan_id?: string | null;
  artifact_id?: string | null;
  capital_pool_id?: string | null;
};
```

2. Resolve `source_mode` in this order:

- explicit `meta.source_mode` from BFF, if introduced by parent implementation;
- `stale_cache` when `meta.staleness.served_from = "cache"`;
- `unavailable` when the guarded surface is unavailable or the BFF request fails without verifiable payload;
- `derived_projection` for telemetry/projection-owned runtime status once `P0-TEL-PROJ-001` lands;
- `authoritative_bff` when the BFF response is fresh and all required source surfaces are `ok`;
- `preview_mock_only` or `demo_only` only from route/environment classification, never from BFF data.

3. Keep the badge compact on dense operator pages. The badge text should be the source mode; detail text can sit in a tooltip or adjacent metadata row.

4. For absent identity fields, show field-level `unavailable`. Do not show fake values such as `local`, `demo`, `unknown commit`, or package build hash unless the BFF explicitly returned them.

5. For staging/prod, any path classified as `preview_mock_only` or `demo_only` should render blocked/unavailable according to `SD-P0-05`; it should not appear as a healthy operator route.

## 6. Operator Journey Notes

The operator should be able to answer these questions without switching tools:

- Is this screen backed by authoritative BFF data, a derived projection, stale cache, preview mock, demo-only state, or unavailable data?
- Which runtime binding, deployment plan, artifact, and capital pool produced the state?
- Which bridge repo and commit emitted or bootstrapped this runtime, if that identity is available?
- Are action CTAs disabled because the data is stale, projected, missing, or unavailable?

Recommended journey:

1. Start on Operator Home or Health and see the global source/degradation posture.
2. Drill into Runtime State Board and see row-level source mode plus runtime identity.
3. Open Runtime Detail or Deployment Plan Detail and see the full identity block.
4. When bridge repo/commit is unavailable, the page should say unavailable and keep any safety-sensitive CTA guarded by existing BFF surface status rules.

## 7. Review Handoff For Parent Owner

Suggested parent implementation sequence:

1. Add or confirm frontend `SourceMode` and `RuntimeIdentityView` types.
2. Add a small resolver from BFF `meta` and route classification into the parent source-mode enum.
3. Update Runtime State Board and Runtime Detail first, because they cover the acceptance identity fields most directly.
4. Update Deployment Review / Plan Detail and one governance/evolution critical surface with the shared badge.
5. Add tests for source-mode rendering and no silent mock fallback in staging/prod.
6. If bridge repo/commit are not present in BFF payloads, file or implement a BFF gap against runtime context / telemetry projection rather than hard-coding them in the frontend.

Suggested `bff-gap` conditions:

- runtime detail lacks `runtime_binding_id`;
- runtime detail lacks any deployment-plan reference that can normalize to `deployment_plan_id`;
- runtime detail lacks `artifact_id` or `capital_pool_id`;
- BFF cannot identify whether a runtime row is authoritative, projected, stale, or unavailable;
- bridge repo/commit are required by the UI but not available from runtime context/projection.

## 8. Verification Performed

Commands run from `/home/edna/code/pantheon`:

```bash
jq '.tasks[] | select(.id=="P0-FE-SOURCE-001-SIDECAR-BFF-HANDOFF")' ai-status.json
sed -n '1,260p' .orchestrator/task-briefs/p0_fe_source_001_sidecar_bff_handoff.md
sed -n '320,510p' docs/04/pantheon_p0_sd/SD-P0-05_Frontend_Production_Adoption_Demo_Cleanup.md
sed -n '1,260p' services/control-plane/bff/BFF_API_CONTRACT.md
sed -n '1,260p' docs/bff/PKT-010-runtime-state-board.md
sed -n '2620,2785p' services/control-plane/bff/main.py
sed -n '6960,7140p' services/control-plane/bff/main.py
sed -n '7360,7455p' services/control-plane/bff/main.py
sed -n '1,240p' services/telemetry/telemetry_event.schema.json
rg -n "source_mode|sourceMode|runtime_binding_id|runtimeBinding|deployment_plan_id|bridge_repo|bridge_commit|preview_mock_only|demo_only|authoritative_bff|stale_cache|unavailable|@/demo|mock" /home/edna/code/front-ai-trading-system/src /home/edna/code/front-ai-trading-system/scripts /home/edna/code/front-ai-trading-system/docs
```

No runtime tests were run because this sidecar only updates a support artifact.
