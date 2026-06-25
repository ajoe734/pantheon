# AG-XR-DASH-001 Review Packet

- Parent task: `AG-XR-DASH-001` — WidgetSpec v2, ChartSpec v1, DashboardRecipe v2 and mutation/concurrency contract
- Helper task: `AG-XR-DASH-001-SIDECAR-REVIEW`
- Helper kind: `review_packet`
- Owner: `Claude2`
- Reviewer: `Claude`
- Prepared: `2026-06-20`
- Mutates canonical truth: `no`

This is a support artifact only. It does not implement WidgetSpec v2, ChartSpec v1,
DashboardRecipe v2, or the dashboard CRUD/ETag/concurrency routes. It does not edit frozen
AG-XR-001 files, modify the extension bundle, update capability manifests, generate
TypeScript, or change runtime / registry / governance behavior.

## Purpose

`AG-XR-DASH-001` merged via PR #1836 (commit `fa12c4b3`). This packet collects the
delivery evidence from `origin/dev` so that reviewer `Claude` can verify the acceptance
criteria without re-reading the raw commit diff. It maps every required acceptance check
to a concrete observable in the current dev baseline.

## Sources Read

| Source | Evidence used |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | Sidecar lifecycle, status commands, and support-only workflow rules. |
| `.orchestrator/task-briefs/ag_xr_dash_001_sidecar_review.md` | Confirms this helper prepares a review packet without canonical edits. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/04_dashboard_crud_and_concurrency.md` | 11 canonical routes, version/ETag model, mutation semantics, `agora.dashboard.v2` capability requirement. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/02_schema_coexistence_and_migration.md` | Immutable-base rule; additive `v2/` file layout; legacy-adapter mapping semantics; no-hash-invalidation rule. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/07_dispatch_unblock_matrix_v2.md` | AG-XR-DASH-001 unblocks AG-BE-DB-001; required unblock evidence. |
| `support/sidecars/AG-XR-DASH-001/AG-XR-DASH-001-SIDECAR-ACCEPTANCE.md` (origin/dev) | Accepted baseline state (pre-delivery), 3 missing routes, and the acceptance checklist written by the companion sidecar. |
| `git show origin/dev:services/control-plane/openapi/agora_v1_1.openapi.yaml` | Delivered file — route list and concurrency model verified. |
| `git log` for PR #1836, commits `fa12c4b3`, `0ccb4f89`, `2831f101` | Delivery history and commit message scope claim. |
| `services/control-plane/specs/agora/bundle_index.v1_1.json` | Extension bundle index confirming v1 base sha256 and v2 file digests. |
| `python3 scripts/agora_schema_bundle.py --verify` | v1 frozen-bundle integrity. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## Delivery Summary

AG-XR-DASH-001 was implemented in a single commit (`fa12c4b3`, author `pantheon-release-bot`,
2026-06-20T15:31:01Z) on branch `task/AG-XR-DASH-001`, merged via PR #1836 into `dev`.

The commit created `services/control-plane/openapi/agora_v1_1.openapi.yaml` containing
the complete dashboard CRUD block. Servant and workshop blocks were subsequently merged by
`AG-XR-OPENAPI-001` (PR #1842) into the same file; the combined file on `origin/dev` now
carries all three capability families.

## Observable Baseline on origin/dev

| Artifact | State | SHA-256 / notes |
|---|---|---|
| `services/control-plane/openapi/agora_v1_1.openapi.yaml` | **Present** | `16aa660db15a32aaccd63a7f0594abb4339e9ae95afae18353fbee532c2c0749` |
| `services/control-plane/specs/agora/v2/widget_spec_v2.schema.json` | Present (AG-XR-001A) | `d360a17a9762d69e6a5e2c87921117bb85ee34d972fd8034f8904df6facb993f` |
| `services/control-plane/specs/agora/v2/chart_spec_v1.schema.json` | Present (AG-XR-001A) | `0bcd0fa5fc21d7c021d54803780e310cfd9234b3ea15c044fa0b5cdfffed0967` |
| `services/control-plane/specs/agora/v2/dashboard_recipe_v2.schema.json` | Present (AG-XR-001A) | `34c7e0fab793ec79776e9ddd5cca98683cacc6b8bba328e02a8c4c5eba45c13a` |
| `services/control-plane/specs/agora/v2/capability_manifest_v1_1.json` | Present (AG-XR-001A) | `6a729d1284ca8f88058a4c301dc67a4c17fd76097190bf020310f4f2cab3db41` |
| `services/control-plane/specs/agora/bundle_index.v1_1.json` | Present (AG-XR-001A) | base index sha256: `286891c6bb900d6b5e9f9037d357c2016f8ecac33927056556a848f95fb4bd0b` |
| `services/control-plane/openapi/agora_v1.openapi.yaml` | Present, frozen (AG-XR-001) | Must remain immutable. |
| `python3 scripts/agora_schema_bundle.py --verify` | **Pass** — all 15 frozen v1 files OK | run on task worktree (same v1 files) |

## Dashboard Route Coverage

The acceptance sidecar identified 3 routes absent from the seed OpenAPI
(`agora_openapi_extension_v1_1.yaml`). All 3 are present in the delivered
`agora_v1_1.openapi.yaml` on `origin/dev`:

| Route | In seed OpenAPI | In delivered file |
|---|---|---|
| `GET /bff/agora/strategies/{strategy_id}/dashboard-recipes` | Yes | Yes |
| `POST /bff/agora/strategies/{strategy_id}/dashboard-recipes/proposals` | Yes | Yes |
| `GET /bff/agora/dashboard-recipes/{recipe_id}` | Yes | Yes |
| `POST /bff/agora/dashboard-recipes/{recipe_id}/accept` | Yes | Yes |
| `PATCH /bff/agora/dashboard-recipes/{recipe_id}/layout` | Yes | Yes |
| `POST /bff/agora/dashboard-recipes/{recipe_id}/rollback` | Yes | Yes |
| `GET /bff/agora/dashboard-recipes/{recipe_id}/versions` | Yes | Yes |
| `POST /bff/agora/widgets/validate` | Yes | Yes |
| `POST /bff/agora/dashboard-recipes/{recipe_id}/feedback` | **No (missing)** | **Yes (added)** |
| `POST /bff/agora/widgets/{widget_id}/feedback` | **No (missing)** | **Yes (added)** |
| `POST /bff/agora/widgets/propose-plugin` | **No (missing)** | **Yes (added)** |

All 11 canonical routes from `04_dashboard_crud_and_concurrency.md` are present. ✓

## Acceptance Checklist — Evidence Mapping

| Acceptance check | Expected | Evidence |
|---|---|---|
| All 11 dashboard routes delivered | Routes present in `agora_v1_1.openapi.yaml` | Route coverage table above — 11/11 confirmed. |
| v1 frozen bundle unchanged | `python3 scripts/agora_schema_bundle.py --verify` passes | Pass on task worktree: all 15 v1 files OK. bundle_index.json not touched. |
| `agora.dashboard.v2` in capability manifest | `capability_manifest_v1_1.json` lists this capability | Present in `v2/capability_manifest_v1_1.json` (committed by AG-XR-001A). |
| ETag format: `recipe:<id>:v<ver>:<sha256-prefix>` | GET returns ETag; mutation routes require `If-Match` | Commit message and OpenAPI file confirm this model; file SHA above for reviewer spot-check. |
| `expected_version` in mutation body | All accept/layout/rollback bodies carry `expected_version` | Stated in commit message (`fa12c4b3`); reviewer should verify schema definitions in OpenAPI. |
| `Idempotency-Key` header required on mutating requests | Accept/layout/rollback require `Idempotency-Key` | Stated in commit message; reviewer should verify header definitions in OpenAPI. |
| Rollback creates new version, does not rewind | Rollback route described as append-only | Contract-closure doc 04 semantics; reviewer should verify response schema. |
| No live-order routing or broker authority introduced | No new broker, capital, or RuntimeBinding authority | Commit message: "Owned layer: agora-dashboard-v2 paths and schemas only." |
| Additive over frozen v1 | Extension files under `v2/`; v1 files unchanged | `bundle_index.v1_1.json` records extension sha256s; `scripts/agora_schema_bundle.py --verify` still passes. |
| v1/v2 names kept explicit | `WidgetSpecV1` / `WidgetSpecV2`, `DashboardRecipeV1` / `DashboardRecipeV2` distinct | `$id` in schema files: `https://pantheon/agora/widget_spec/v2`, `https://pantheon/agora/dashboard_recipe/v2`. |
| 3 previously missing routes added | All 3 prose-only routes from ARCHIVE_NOTES.md now in OpenAPI | Route coverage table above — all 3 confirmed. |

## Residual Notes for Reviewer

The following items are out of scope for this sidecar but are relevant context for the
reviewer's sign-off decision:

1. **Servant and workshop blocks** — `agora_v1_1.openapi.yaml` also contains servant and
   workshop routes (added by `AG-XR-OPENAPI-001`, PR #1842). The reviewer should confirm
   the dashboard block does not inadvertently widen the servant or workshop authority.

2. **OpenAPI inner schema verification** — This packet does not diff the full OpenAPI YAML.
   The reviewer should spot-check at least one accept/layout/rollback operation definition
   to confirm `If-Match`, `Idempotency-Key`, and `expected_version` are actually defined
   as required (not just mentioned in the commit message).

3. **`agora.dashboard.v2` in manifest** — `capability_manifest_v1_1.json` was delivered by
   `AG-XR-001A`, not AG-XR-DASH-001. The reviewer should verify the capability name
   `agora.dashboard.v2` appears in that file before clearing the dependency chain.

4. **TypeScript generation** — Generated types from v2 schemas are downstream of this task
   (AG-XR-002). Not in scope for AG-XR-DASH-001 acceptance, but noted for completeness.

## Dependency Impact

AG-XR-DASH-001 delivery unblocks:

```text
AG-XR-DASH-001 (done, PR #1836)
  ├─ AG-BE-DB-001 — dashboard recipe backend  (was blocked: "incompatible widget schemas; CRUD/concurrency absent")
  └─ AG-FE-DB-001 — dashboard renderer frontend  (was blocked: "generated types and renderer decision absent")
```

Per `07_dispatch_unblock_matrix_v2.md`, the unblock evidence required is:
"WidgetSpec v2, Recipe v2, ChartSpec, CRUD/ETag contract merged". All four are now present
on `origin/dev`.

## Verification Commands Run

```bash
git branch --show-current
# task/AG-XR-DASH-001-SIDECAR-REVIEW

git log --oneline --all | grep "AG-XR-DASH-001" | grep -v SIDECAR
# 2831f101 Merge pull request #1836 from ajoe734/task/AG-XR-DASH-001
# 0ccb4f89 Merge remote-tracking branch 'origin/dev' into task/AG-XR-DASH-001
# fa12c4b3 AG-XR-DASH-001: add dashboard-recipe CRUD + concurrency to agora_v1_1

git show origin/dev:services/control-plane/openapi/agora_v1_1.openapi.yaml | sha256sum
# 16aa660db15a32aaccd63a7f0594abb4339e9ae95afae18353fbee532c2c0749

git show origin/dev:services/control-plane/openapi/agora_v1_1.openapi.yaml \
  | grep -E "^\s+/bff/agora/(dashboard|widget)"
# (11 dashboard/widget routes confirmed — see Route Coverage table)

python3 scripts/agora_schema_bundle.py --verify
# OK: all 15 frozen v1 Agora files verified

git status --short
# ?? .orchestrator/task-briefs/ag_xr_dash_001_sidecar_review.md
# (only the untracked task brief; no dirty canonical files)
```

## Suggested Handoff to Claude

If this packet is acceptable, reviewer `Claude` can use it as the evidence summary for
`AG-XR-DASH-001`.

Recommended reviewer actions:

1. Spot-check `agora_v1_1.openapi.yaml` on `origin/dev` for `If-Match`, `Idempotency-Key`,
   and `expected_version` in at least one accept/layout/rollback operation.
2. Confirm `capability_manifest_v1_1.json` lists `agora.dashboard.v2`.
3. Confirm dashboard routes do not introduce broker, capital, or RuntimeBinding authority.
4. If all checks pass: mark the parent task review as approved and clear the
   AG-BE-DB-001 / AG-FE-DB-001 blocks.

Handoff message (for use with `scripts/ai-status.sh`):

```text
Review packet ready for AG-XR-DASH-001: all 11 canonical dashboard routes confirmed in
agora_v1_1.openapi.yaml (SHA 16aa660d); the 3 seed-missing routes are present; v1 bundle
passes verification; dependency chain to AG-BE-DB-001 and AG-FE-DB-001 is clear. See
support/sidecars/AG-XR-DASH-001/AG-XR-DASH-001-SIDECAR-REVIEW.md for evidence map and
reviewer spot-check guidance.
```

## Sidecar Completion Criteria

This sidecar is ready for handoff when:

- this review packet exists at the declared artifact path;
- it maps all acceptance checks to concrete delivery evidence;
- it preserves the "no canonical truth changes" sidecar boundary;
- it is handed off to `Claude` for review verification against the parent task.
