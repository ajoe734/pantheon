# PKT-003 Evolution Center — UI Decisions

- The existing `/evolution` route was preserved by making `src/pages/evolution/Center.tsx` a thin wrapper around the new PKT-003 implementation.
- All production data comes through the shared `operatorApi` BFF client; no component-level raw network call was added.
- Evolution decision filters submit `action_type`, `risk_level`, and `status` as BFF query params rather than filtering locally.
- Freeze orders expose only the `scope` filter so the default screen still shows both active and lifted orders together.
- Rollbacks expose only `runtime_id` and `action_type`; `time_range` is intentionally omitted because the published v1 BFF store does not apply it.
- The stale / degradation banner is driven only by returned `meta.staleness` data from the independent panel responses.
- Each panel owns its own loading, permission, error, empty, and contract-gap state so one degraded surface does not suppress unrelated panel content.
- Decision detail selection is stored in the URL query string as `?decision=<id>` so the drawer state stays deep-linkable without adding another route.
