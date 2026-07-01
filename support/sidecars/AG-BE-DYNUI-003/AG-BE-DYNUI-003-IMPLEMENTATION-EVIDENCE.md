# AG-BE-DYNUI-003 Implementation Evidence

Task: AG-BE-DYNUI-003
Owner: Codex2
Reviewer: Codex
Date: 2026-06-29

## Sources Read

- `/home/lupin/code/pantheon/AI Trading Desk Design.zip`
- `/tmp/ai-trading-desk-design/uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V11_WinnerBranch_TradingRoom_2026-06-19.md`
- `/tmp/ai-trading-desk-design/uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V4_AI_Dashboard_Control_2026-05-20.md`
- `/tmp/ai-trading-desk-design/Agora.dc.html`
- `docs/04/agora_design_pack_dynui_2026-06-28/README.md`
- `docs/04/agora_design_pack_dynui_2026-06-28/source-map-and-gap-map.md`
- `support/sidecars/AG-BE-DYNUI-002/AG-BE-DYNUI-002-SIDECAR-ACCEPTANCE.md`

## Delivered Scope

- Added `integrations/openclaw/skills/agora/trading_room_workspace/` as the
  servant-side generator boundary for V11 Trading Room workspace proposals.
- The generator only emits declarative `TradingRoomWidgetSpec` / `ChartSpec`
  payloads and checks widget type, renderer, chart kind, data source,
  interactions, and executable-content patterns against the widget registry.
- Unsupported renderers return either `supportedFallbacks` metadata or
  `componentTaskRequests`; no React, JavaScript, HTML, arbitrary URL renderer,
  or production code injection path is generated.
- Wired `POST /bff/agora/strategies/{strategy_id}/trading-room/proposals` to
  the generator while retaining the existing route-level `_validate_view` /
  `_validate_widget` allowlist validator as a second gate.
- Persisted generator metadata with workspace proposals so `GET` can read back
  evidence refs, data freshness, fallback, and component-task metadata.

## Not Changed

- No schema field names or V11 contract semantics were broadened.
- No OpenAPI/generated frontend type work was done; that remains
  `AG-XR-DYNUI-001`.
- No frontend runtime, grid editor, widget drawer, visual parity, or E2E work
  was done.
- No Management, RuntimeBinding, broker order, live order, or capital-binding
  route was added.

## Validation

```bash
python3 -m pytest integrations/openclaw/skills/agora/trading_room_workspace/test_skill.py -q
```

Result: `4 passed in 0.65s`.

```bash
python3 -m pytest services/control-plane/bff/agora/trading_room/test_trading_room.py -q
```

Result: `40 passed in 13.13s`.

```bash
python3 -m pytest integrations/openclaw/adapter/test_agora_context_bundle.py -q
```

Result: `21 passed in 3.16s`.

```bash
rg -n "dangerouslySetInnerHTML|eval\(|new Function|place_order|enable_live|change_capital_binding|write_runtime_binding|open_management_route" \
  integrations/openclaw/skills/agora/trading_room_workspace services/control-plane/bff/agora/trading_room
```

Result: one expected hit in `integrations/openclaw/skills/agora/trading_room_workspace/skill.py`
where `"eval("` is listed as a forbidden executable-content pattern; no executable
call site was introduced.

## Closeout Evidence

- Implementation PR: <https://github.com/ajoe734/pantheon/pull/2585>
- Task commit: `b72678e87fd85fa594dec3e54d7517ab6cfe4a53`
- Merge commit on `dev`: `ef246b2da4d6d48f2fd47ca55dc2465415c71efd`
- GitHub checks observed on PR #2585: `Commit trailers`, `Runtime mirror guard`,
  `Smoke acceptance`, and `Forward to orchestrator` succeeded.

Owner closeout verification on 2026-06-29:

```bash
python3 -m pytest integrations/openclaw/skills/agora/trading_room_workspace/test_skill.py -q
```

Result: `4 passed in 0.63s`.

```bash
python3 -m pytest services/control-plane/bff/agora/trading_room/test_trading_room.py -q
```

Result: `40 passed in 13.57s`.

```bash
python3 -m pytest integrations/openclaw/adapter/test_agora_context_bundle.py -q
```

Result: `21 passed in 3.05s`.

```bash
rg -n "dangerouslySetInnerHTML|eval\(|new Function|place_order|enable_live|change_capital_binding|write_runtime_binding|open_management_route" \
  integrations/openclaw/skills/agora/trading_room_workspace services/control-plane/bff/agora/trading_room
```

Result: one expected hit in `integrations/openclaw/skills/agora/trading_room_workspace/skill.py`
where `"eval("` is listed as a forbidden executable-content pattern; no executable
call site was introduced.
