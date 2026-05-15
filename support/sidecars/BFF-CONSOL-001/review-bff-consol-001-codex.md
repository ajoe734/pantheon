# BFF-CONSOL-001 Review - Codex

Task: Backend FastAPI route manifest extractor
Owner: Claude
Reviewer: Codex
Date: 2026-05-13
Verdict: APPROVED

## Artifacts Reviewed

- `scripts/bff_route_manifest_backend.py`
- `services/control-plane/bff/contract_snapshots/backend_routes_manifest.json`
- Commit `96d1101c`
- Re-review commit `50fa50bc`

## Re-review Result

Approved after commit `50fa50bc`.

The prior blocking issue is resolved: emitted `entries[].path` values now use normalized `{param}` route shapes, and `covered_by` route targets are normalized to the same comparable form.

Checks run during re-review:

```bash
python3 scripts/bff_route_manifest_backend.py --out /tmp/bff_backend_manifest_review.json
diff -u services/control-plane/bff/contract_snapshots/backend_routes_manifest.json /tmp/bff_backend_manifest_review.json
python3 scripts/bff_route_manifest_backend.py --check
jq -r '[.entries[].path | select(test("\\{(?!param\\})[^}]+\\}"))] | length' services/control-plane/bff/contract_snapshots/backend_routes_manifest.json
jq -r '[.entries[].covered_by? // empty | select(test("\\{(?!param\\})[^}]+\\}"))] | length' services/control-plane/bff/contract_snapshots/backend_routes_manifest.json
jq -e '(.entries | length) == .metadata.total_routes and .metadata.total_routes == 371 and all(.entries[]; (.method|type)=="string" and (.path|type)=="string" and (.family|type)=="string" and (.status|IN("implemented","alias","superseded","deferred"))) and ([.entries[].method] | all(. != "HEAD" and . != "OPTIONS"))' services/control-plane/bff/contract_snapshots/backend_routes_manifest.json
```

Observed:

- Regenerated manifest matched the committed snapshot.
- `--check` passed.
- Non-normalized `entries[].path` dynamic parameter count: `0`.
- Non-normalized `covered_by` dynamic parameter count: `0`.
- Required field/status schema check passed for `371` routes.

## Prior Blocking Finding

The manifest does not normalize dynamic path parameters in emitted `entries[].path` values to `{param}`.

Acceptance requires FastAPI dynamic params to use `{param}` form. The implementation has `normalize_path()`, but `app_route_index()` writes the raw FastAPI route path into the manifest entry:

```python
"path": path,
```

As a result, the snapshot still contains 188 parameterized paths with named parameters such as:

```text
GET /bff/agora/sessions/{sessionId}
GET /api/v1/capital-pools/{pool_id}
GET /bff/strategies/{strategy_id}
```

Required change:

- Emit normalized route paths in `entries[].path`, e.g. `/bff/agora/sessions/{param}`.
- Normalize `covered_by` route targets as well, or document and test why those are intentionally not part of the comparable route-shape surface.
- Regenerate `services/control-plane/bff/contract_snapshots/backend_routes_manifest.json`.

## Checks Run

```bash
python3 scripts/bff_route_manifest_backend.py --check
python3 scripts/bff_route_manifest_backend.py --out /tmp/bff_backend_manifest_review.json
diff -u services/control-plane/bff/contract_snapshots/backend_routes_manifest.json /tmp/bff_backend_manifest_review.json
jq -e '(.entries | length) == .metadata.total_routes and all(.entries[]; (.method|type)=="string" and (.path|type)=="string" and (.family|type)=="string" and (.status|IN("implemented","alias","superseded","deferred")))' services/control-plane/bff/contract_snapshots/backend_routes_manifest.json
jq -r '.entries[] | select(.path|test("\\{(?!param\\})[^}]+\\}")) | .path' services/control-plane/bff/contract_snapshots/backend_routes_manifest.json | wc -l
```

Observed:

- `--check` passed.
- Full regenerated snapshot matched current snapshot on 2026-05-13.
- Required field/status schema check passed.
- Non-normalized dynamic path count was `188`.

## Decision

Changes requested before approval. The route extraction and snapshot stability are otherwise in reasonable shape, but the emitted manifest does not yet satisfy the dynamic parameter normalization acceptance criterion.
