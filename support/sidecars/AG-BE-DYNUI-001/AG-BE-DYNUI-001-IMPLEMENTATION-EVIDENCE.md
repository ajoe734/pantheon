# AG-BE-DYNUI-001 Implementation Evidence

| Field | Value |
|---|---|
| Task ID | `AG-BE-DYNUI-001` |
| Owner | `Codex` |
| Reviewer | `Claude2` |
| Date | 2026-06-29 |
| Mutates canonical truth | `false` |

## Delivered Scope

- Added `services/control-plane/specs/agora/trading_room_workspace.schema.json`
  for V11 `TradingRoomWorkspaceProposal`, `TradingRoomWorkspace`,
  `TradingRoomViewSpec`, `TradingRoomWidgetSpec`, `WidgetPlacement`, and
  `WorkspaceLayoutOperation`.
- Extended `services/control-plane/bff/agora/trading_room/store.py` with
  user/tenant-scoped workspace proposal and workspace records.
- Extended `services/control-plane/bff/agora/trading_room/router.py` with:
  - proposal create/get/accept routes;
  - active workspace read route;
  - ETag-protected layout patch route;
  - view add/update routes;
  - widget add/update routes.
- Added focused coverage in
  `services/control-plane/bff/agora/trading_room/test_trading_room.py`.

## Boundary Kept

- No OpenAPI/generated frontend type sync; that remains `AG-XR-DYNUI-001`.
- No real servant generator integration; that remains `AG-BE-DYNUI-003`.
- No widget revision proposal lifecycle, change-log route, or rollback route;
  that remains `AG-BE-DYNUI-002`.
- No order routing, capital binding, RuntimeBinding mutation, broker control, or
  Management-plane vocabulary in the new workspace routes.

## Validation

Commands run from the task worktree:

```bash
python3 -m py_compile services/control-plane/bff/agora/trading_room/router.py services/control-plane/bff/agora/trading_room/store.py services/control-plane/bff/agora/trading_room/test_trading_room.py
python3 -m pytest services/control-plane/bff/agora/trading_room/test_trading_room.py -q
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py -q
python3 -m pytest services/control-plane/bff/test_route_resolution_no_shadowing.py -q
```

Observed result:

- `37 passed` for focused Trading Room tests.
- `18 passed` for Agora router tests.
- `2 passed` for route shadowing regression.
