# Latest `dev` Findings

## Confirmed repository truth

### Frozen contract bundle

`pantheon@dev/services/control-plane/specs/agora/bundle_index.json` is frozen by `AG-XR-001`. It records the exact SHA-256 of the v1 WidgetSpec, DashboardRecipe, capability manifest and Agora OpenAPI. Replacing any one of those files invalidates the reviewed bundle.

### Widget contract conflict

The frozen `WidgetSpec 1.0` uses:

- `widget_type` from a small legacy enum
- `data_source.bff_path`
- `display_options`
- no ChartSpec

The Design Closure A3 schema instead uses:

- open registry `widget_type`
- `data_source` as an allowlisted logical ID
- structured `query`
- `chart_spec`
- interactions and sensitivity

They are structurally incompatible. A3 must not overwrite the frozen v1 file.

### DashboardRecipe gap

The frozen DashboardRecipe has a `version` field, but no route-level concurrency contract, no ETag semantics, no append-only version behavior, and no strategy-version/workspace/phase model required by the new Trading Desk.

### Capability and OpenAPI gap

The frozen capability manifest advertises existing Ask, evaluation, research-task, signal, daily, watchlist, journal and memory route families. It does not advertise:

- `POST /bff/agora/servant/ensure`
- `/bff/agora/workshops` CRUD
- dashboard recipe proposal/accept/layout/rollback CRUD

The frozen OpenAPI likewise represents the legacy Agora surface rather than the new cross-repo SA/SD contract.

### OpenClaw adapter gap

The adapter boundary supports runtime/session/tool/workflow behavior and its canonical contract defines abstract agent provisioning semantics. The currently documented runnable adapter surface does not expose a typed agent ensure/reconcile HTTP API needed by `AG-BE-ID-002`.

### Frontend truth

`execute-plans@dev` still mounts the legacy Agora IA (Daily, Markets, Watchlist, Signals, Notebook, Ask, Committee, Trainer, etc.). The new three-tab Trading Desk IA is not yet mounted.

`execute-plans@dev/package.json` already includes `recharts`. It does not include ECharts or react-grid-layout, which are required for network/heatmap/sankey/candlestick rendering and draggable/resizable dashboard layouts.

## Correct task verdict

The following workers were correct to stop:

- `AG-BE-DB-001`
- `AG-FE-DB-001`
- `AG-BE-SW-001`
- `AG-BE-ID-002`
- `AG-XR-003`

They were blocked by contract authority, not by missing implementation effort.
