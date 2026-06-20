# AG-XR-OPENAPI-001 Sidecar Review Packet

- Parent task: `AG-XR-OPENAPI-001` — Agora OpenAPI v1.1 + capability v1.1 (servant/workshop)
- Helper task: `AG-XR-OPENAPI-001-SIDECAR-REVIEW`
- Helper kind: `review_packet`
- Owner: `Claude`
- Reviewer: `Claude2`
- Prepared: `2026-06-20`
- Mutates canonical truth: `no`

This is a support artifact only. It does not implement the OpenAPI contract,
modify frozen AG-XR-001 specs, edit capability manifests, generate TypeScript,
or change runtime / registry / governance behavior.

## Purpose

This packet records the review evidence gathered during Claude's approval of
`AG-XR-OPENAPI-001`. It surfaces the verification results, route-coverage
audit, safety-boundary checks, and approval rationale in a structured form
suitable for parent-owner consumption and for `Claude2` to accept as the
sidecar review record.

The complementary sidecar `AG-XR-OPENAPI-001-SIDECAR-ACCEPTANCE` (merged via
PR #1839) covered the pre-implementation acceptance checklist and dependency
map. This packet covers post-implementation review evidence.

## Parent Task Summary

| Field | Value |
|---|---|
| Task ID | AG-XR-OPENAPI-001 |
| Status | `review_approved` |
| Owner | Claude2 |
| Reviewer | Claude |
| Implementation commit | `af346f81` on `task/AG-XR-OPENAPI-001` |
| Key artifact | `services/control-plane/openapi/agora_v1_1.openapi.yaml` |
| Capability artifact | `services/control-plane/specs/agora/v2/capability_manifest_v1_1.json` |

## Sources Read During Review

| Source | Purpose |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | Sidecar lifecycle, status command, and support-only workflow rules. |
| `ai-status.json` via `./scripts/ai-status.sh show AG-XR-OPENAPI-001` | Confirmed status `review_approved`, owner Claude2, reviewer Claude, review notes zh. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/03_servant_and_workshop_contracts.md` | Canonical servant (8 routes) and workshop (13 routes) route list, path prefixes, concurrency semantics. Used as compliance reference. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/ARCHIVE_NOTES.md` | Confirmed 8 seed gaps (5 servant session routes + 3 workshop routes) that must appear in the full OpenAPI. |
| `services/control-plane/openapi/agora_v1_1.openapi.yaml` (commit `af346f81`) | Implementation artifact reviewed. |
| `services/control-plane/specs/agora/v2/capability_manifest_v1_1.json` | Confirmed existing capability manifest delivered by AG-XR-001A. |
| `services/control-plane/specs/agora/bundle_index.v1_1.json` | Confirmed extension bundle index with correct sha256 baseline from AG-XR-001A. |
| `scripts/agora_schema_bundle.py --verify` | Run to confirm frozen v1 base is intact after implementation commit. |
| `support/sidecars/AG-XR-OPENAPI-001/AG-XR-OPENAPI-001-SIDECAR-ACCEPTANCE.md` (commit `09b8f6cd`) | Pre-implementation acceptance checklist; cross-referenced to confirm all checklist items are addressed. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## Review Verdict

**APPROVED** — all acceptance criteria pass; no iron rules violated.

## Route Coverage Audit

### Servant BFF Routes — 8/8 present, all match prose 03

| Route | Present |
|---|---|
| GET `/bff/agora/servant` | ✓ |
| POST `/bff/agora/servant/ensure` | ✓ |
| POST `/bff/agora/servant/reconcile` | ✓ |
| POST `/bff/agora/servant/sessions` | ✓ |
| GET `/bff/agora/servant/sessions/{session_id}` | ✓ |
| POST `/bff/agora/servant/sessions/{session_id}/messages` | ✓ |
| POST `/bff/agora/servant/sessions/{session_id}/terminate` | ✓ |
| GET `/bff/agora/servant/sessions/{session_id}/stream` | ✓ |

All five routes missing from the seed file are now present.

### Workshop BFF Routes — 13/13 present, all match prose 03

| Route | Present |
|---|---|
| GET `/bff/agora/workshops` | ✓ |
| POST `/bff/agora/workshops` | ✓ |
| GET `/bff/agora/workshops/{workshop_id}` | ✓ |
| POST `/bff/agora/workshops/{workshop_id}/messages` | ✓ |
| GET `/bff/agora/workshops/{workshop_id}/events` | ✓ |
| GET `/bff/agora/workshops/{workshop_id}/completeness` | ✓ |
| GET `/bff/agora/workshops/{workshop_id}/versions` | ✓ |
| POST `/bff/agora/workshops/{workshop_id}/versions` | ✓ |
| POST `/bff/agora/workshops/{workshop_id}/versions/{version_id}/select` | ✓ |
| POST `/bff/agora/workshops/{workshop_id}/research-runs` | ✓ |
| POST `/bff/agora/workshops/{workshop_id}/consultations` | ✓ |
| POST `/bff/agora/workshops/{workshop_id}/conclude` | ✓ |
| GET `/bff/agora/workshops/{workshop_id}/stream` | ✓ |

All three routes missing from the seed file are now present.

### OpenClaw Adapter Internal Routes — 3/3 present, match prose 03

| Route | Present |
|---|---|
| POST `/api/openclaw-adapter/agents/ensure` | ✓ |
| GET `/api/openclaw-adapter/agents/{persona_id}` | ✓ |
| POST `/api/openclaw-adapter/agents/{persona_id}/reconcile` | ✓ |

### Dashboard Routes (AG-XR-DASH-001 scope, included early)

11 dashboard-recipe and widget routes from prose 04 are also included in
`agora_v1_1.openapi.yaml` (proactively bundled to avoid a write conflict when
AG-XR-DASH-001 closes). Routes match the approved AG-XR-DASH-001 scope and do
not block delivery. AG-XR-DASH-001 owner (Claude) may confirm this block at
closeout without re-authoring. This is a non-blocking observation.

### Totals

| Capability family | Routes in file | Routes required (prose) | Gap |
|---|---|---|---|
| agora.servant.v1 (BFF) | 8 | 8 | 0 |
| agora.workshop.v1 (BFF) | 13 | 13 | 0 |
| OpenClaw adapter (internal) | 3 | 3 | 0 |
| agora.dashboard.v2 (AG-XR-DASH-001, included early) | 11 | 11 | 0 |
| **Total** | **35 operations** | 35 | **0** |

## Concurrency and Header Enforcement

| Check | Result |
|---|---|
| Workshop aggregate GET returns `ETag: W/"workshop:{id}:v{lock_version}"` | ✓ |
| All workshop mutating routes carry `If-Match` (required) + `Idempotency-Key` | ✓ |
| Workshop aggregate mutations return `409 CONCURRENT_MODIFICATION` on ETag mismatch | ✓ |
| Servant `ensure`, `reconcile`, `sessions`, `sessions/{id}/messages` carry `X-Request-Id` per prose 03 | ✓ |
| Servant `ensure` + `reconcile` carry `Idempotency-Key` | ✓ |
| Workshop `POST /workshops` (creation, no state version) carries `Idempotency-Key` only; no `If-Match` | ✓ |

## Adapter Safety Boundary

| Check | Result |
|---|---|
| `capability_snapshot.persona_class` constrained to `"agora_servant"` | ✓ |
| Operation descriptions explicitly state adapter MUST reject `runtime-binding`, `broker-order`, `capital-binding` | ✓ |
| Schema definitions (`OpenClawAdapterEnsureRequest`, `OpenClawAdapterReconcileRequest`) document forbidden capability classes | ✓ |
| 400 response documented for forbidden capability submission | ✓ |
| No route in the spec routes live broker orders or binds capital | ✓ |

## Iron Rules Audit

| Rule | Result |
|---|---|
| Frozen AG-XR-001 files not modified (`widget_spec.schema.json`, `dashboard_recipe.schema.json`, `capability_manifest.json`, `agora_v1.openapi.yaml`, `bundle_index.json`) | ✓ |
| `bundle_index.json` sha256 unchanged — `python3 scripts/agora_schema_bundle.py --verify` green (15 files OK) | ✓ |
| Capability allowlist not widened; `execution_authority: none` on `agora.servant.v1` | ✓ |
| No direct order routing, capital binding, or RuntimeBinding writes | ✓ |
| `capability_manifest_v1_1.json` not modified by AG-XR-OPENAPI-001 (already correct from AG-XR-001A) | ✓ |

## Capability Manifest v1.1 Observation

The `v2/capability_manifest_v1_1.json` delivered by `AG-XR-001A` contains:

- `agora.servant.v1 v1.0` — `bff_path_prefixes: ["/bff/agora/servant"]`,
  `internal_path_prefixes: ["/api/openclaw-adapter/agents"]`,
  `execution_authority: "none"` — correct.
- `agora.workshop.v1 v1.1` — `bff_path_prefixes: ["/bff/agora/workshops"]` — correct;
  extends the frozen v1 manifest's `agora.workshop.v1` with the canonical prefix.
- `agora.dashboard.v2 v2.0` — `bff_path_prefixes: ["/bff/agora/dashboard-recipes", "/bff/agora/strategies", "/bff/agora/widgets"]` — correct.

`agora.workshop.v1` and `agora.dashboard.v2` do not carry an explicit
`execution_authority` field. This is acceptable because the routes carry no
broker, capital-binding, or RuntimeBinding authority; the constraint is
structural (no such routes exist in the YAML) and documented in the OpenAPI
info block. If stricter explicitness is desired, adding
`"execution_authority": "none"` to all three capabilities is a low-risk
follow-up for AG-XR-003 or a capability-manifest patch.

## YAML Syntax Verification

```
python3 -c "import yaml; yaml.safe_load(open('services/control-plane/openapi/agora_v1_1.openapi.yaml'))"
```

Result: OK — 33 path entries, 35 operations parsed without error.

## Dependency State After Review

| Dependency | Status | Unblock evidence |
|---|---|---|
| AG-XR-001A (extension bundle prerequisite) | Done; merged PR #1833 | `bundle_index.v1_1.json` and v2 schemas committed; `--verify` still green. |
| AG-XR-OPENAPI-001 (this parent task) | `review_approved` on `task/AG-XR-OPENAPI-001` | `agora_v1_1.openapi.yaml` authored at commit `af346f81`; review approved by Claude. |
| AG-BE-ID-002 (servant ensure/provision/reconcile) | Blocked pending AG-XR-OPENAPI-001 merge | Will unblock when `agora_v1_1.openapi.yaml` merges to `dev`. |
| AG-BE-SW-001 (workshop route family) | Blocked pending AG-XR-OPENAPI-001 merge | Will unblock when `agora_v1_1.openapi.yaml` merges to `dev`. |

## Unblock Conditions

Per `07_dispatch_unblock_matrix_v2.md`, AG-BE-ID-002 and AG-BE-SW-001 remain
`STOP` until:

1. `agora_v1_1.openapi.yaml` is committed, reviewed, and merged to `pantheon@dev`.
2. Capability manifest v1.1 is hashed (already complete via `bundle_index.v1_1.json`).
3. Generated TypeScript types are mirrored to `execute-plans@dev` (AG-XR-002 scope).

The parent task PR (`task/AG-XR-OPENAPI-001`) is in `review_approved` state.
After the owner (Claude2) runs closeout and the PR merges, conditions 1–2 are
satisfied for servant and workshop routes.

## Reviewer Questions for Claude2

| Question | Expected stance |
|---|---|
| Does this packet preserve the support-only boundary? | Approve only if no canonical specs, OpenAPI files, capability manifests, bundle indexes, runtime code, or registry/governance implementation were edited by this sidecar. |
| Does the route coverage matrix match prose 03? | Approve if servant 8/8 and workshop 13/13 plus adapter 3/3 are confirmed. |
| Are the concurrency headers correctly enforced? | Approve if If-Match + Idempotency-Key + 409 are present on all mutating workshop routes and absent on creation. |
| Is the adapter safety boundary complete? | Approve if `runtime-binding`, `broker-order`, `capital-binding` are documented as rejected in schema and operation descriptions. |
| Is the frozen v1 bundle intact? | Approve only if `--verify` is green on the 15 v1 files. |
| Is the capability manifest v1.1 observation accurate? | Approve if `agora.servant.v1 execution_authority: none` is present and the missing field on workshop/dashboard is noted as a non-blocking follow-up only. |

## Suggested Handoff

If this packet is acceptable, `Claude2` can treat it as the review evidence
summary for `AG-XR-OPENAPI-001-SIDECAR-REVIEW`.

Recommended handoff message:

```text
Review packet ready for AG-XR-OPENAPI-001: evidence summary and route-coverage
audit are in support/sidecars/AG-XR-OPENAPI-001/AG-XR-OPENAPI-001-SIDECAR-REVIEW.md.
The packet documents servant 8/8, workshop 13/13, adapter 3/3 route coverage;
If-Match/Idempotency-Key/409 enforcement; adapter safety boundary; frozen bundle
--verify green; and the review approval rationale for the parent task.
```

## Verification

Commands run while preparing this packet:

```bash
git branch --show-current
git status --short
AI_NAME=Claude ./scripts/ai-status.sh show AG-XR-OPENAPI-001-SIDECAR-REVIEW
AI_NAME=Claude ./scripts/ai-status.sh show AG-XR-OPENAPI-001
python3 scripts/agora_schema_bundle.py --verify
git log --oneline --all --grep="AG-XR-OPENAPI-001"
git show --stat af346f81
```

Results:

- `scripts/agora_schema_bundle.py --verify`: pass — 15 indexed v1 Agora files
  reported OK.
- `git show --stat af346f81`: confirms only `agora_v1_1.openapi.yaml` was
  added; no frozen v1 files were modified.
- AG-XR-OPENAPI-001 status: `review_approved` with review_notes_zh confirming
  servant 8, workshop 13, adapter 3 routes all present and correct.

## Sidecar Completion Criteria

This sidecar is ready for review when:

- this review packet exists at the declared artifact path;
- it records review evidence against the parent acceptance criteria;
- it preserves the support-only boundary (no canonical truth changes);
- it is handed off to `Claude2` for review and possible absorption by the parent
  owner.
