# AG-GAP-014: Agora Postgres Store Restart-Persistence Audit

## Audit Summary

Conducted on: **2026-07-13**
Deployed BFF Git SHA: **30491c3f3** (dev base)
Target Environment: **dev (pantheon-lupin-dev)**
Testing local port mapping: `127.0.0.1:18001` (mapped to `pantheon-operator-bff-1:8001`)

| Store / Backend | Target Env Backend Setting | Startup Log Output | Persistence Result | Verification Reference |
| :--- | :--- | :--- | :--- | :--- |
| **Strategy Workshop** | `postgres` | `backend=postgres store=PostgresWorkshopStore schema=agora` | **SUCCESS** | (Audited in AG-GAP-001) |
| **Dashboard Recipes** | `postgres` | `backend=postgres store=PostgresDashboardRecipeStore` | **SUCCESS** | [dashboard_success.md](dashboard_success.md) |
| **Research Plans** | `postgres` | *(No logs emitted)* | **SUCCESS** | [research_success.md](research_success.md) |
| **Trading Room** | `postgres` | `backend=memory store=TradingRoomStore` | **FAILED (Blocked)** | [trading_room_blocker.md](trading_room_blocker.md) |

---

## Blocker Verdict

> [!WARNING]
> **AG-GAP-014 is BLOCKED** due to a severe regression where the `PostgresTradingRoomStore` implementation and factory loading logic from task **AG-GAP-002** was discarded/overwritten during the merge of **AG-GAP-003** (commit `5266361ffbbaf1087c29d0d8bfa3b64d6d4e5cbf`).
> 
> As a result:
> 1. `operator-bff` silently falls back to `TradingRoomStore` (in-memory backend) on startup despite `AGORA_TRADING_ROOM_STORE_BACKEND=postgres` being configured.
> 2. Agora Trading Room workspaces, proposals, and versions **do not survive restarts** (HTTP 404 returned on readback).

Please refer to [trading_room_blocker.md](trading_room_blocker.md) for the detailed regression trace and log analysis.
