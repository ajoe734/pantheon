# Schema Coexistence and Migration Decision

## 1. Immutable base

Do not edit these `AG-XR-001` files in place:

```text
services/control-plane/specs/agora/widget_spec.schema.json
services/control-plane/specs/agora/dashboard_recipe.schema.json
services/control-plane/specs/agora/capability_manifest.json
services/control-plane/openapi/agora_v1.openapi.yaml
services/control-plane/specs/agora/bundle_index.json
```

They remain the v1 baseline and must continue to pass:

```bash
python3 scripts/agora_schema_bundle.py --verify
```

## 2. Additive extension

Add:

```text
services/control-plane/specs/agora/v2/widget_spec_v2.schema.json
services/control-plane/specs/agora/v2/chart_spec_v1.schema.json
services/control-plane/specs/agora/v2/dashboard_recipe_v2.schema.json
services/control-plane/specs/agora/v2/compatibility_manifest.schema.json
services/control-plane/specs/agora/v2/capability_manifest_v1_1.json
services/control-plane/openapi/agora_v1_1.openapi.yaml
services/control-plane/specs/agora/bundle_index.v1_1.json
```

The extension index must include:

```json
{
  "bundle_version": "1.1",
  "extends": {
    "bundle_path": "services/control-plane/specs/agora/bundle_index.json",
    "bundle_version": "1.0",
    "frozen_by": "AG-XR-001",
    "bundle_index_sha256": "<raw-byte-sha256>"
  },
  "files": {}
}
```

## 3. Contract usage

- Legacy Daily/Watchlist/Journal widgets may continue to read WidgetSpec v1.
- The new Trading Room, Strategy Workshop and Strategy Performance surfaces must write and render WidgetSpec v2 and DashboardRecipe v2 only.
- The BFF may read both versions.
- The BFF must never silently coerce a v1 `custom` widget to v2.
- Generated TypeScript types must retain explicit `WidgetSpecV1` and `WidgetSpecV2` names.

## 4. Legacy adapter mapping

A v1 widget can be projected to v2 only when:

1. `widget_type` has an explicit registry mapping.
2. `data_source.bff_path` maps to one allowlisted logical `data_source_id`.
3. The renderer has an explicit default ChartSpec.
4. No unknown executable/display field is present.

Mapping failure returns `LEGACY_WIDGET_MAPPING_REQUIRED`; it must not guess.

## 5. No hash invalidation

The A3 closure files are design inputs. Their content is adopted under new v2 file names and IDs. They do not replace the files whose hashes are recorded in the XR-001 bundle.
