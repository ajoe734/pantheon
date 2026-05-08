# BFF-2026-05-07-Final Backend Delivery Note

## Status

`delivered`

## Summary

All nine BFF-FINAL contracts (BFF-FINAL-001 through BFF-FINAL-009) have been implemented,
reviewed, and closed. This delivery note marks the completion of the full BFF final contract
wave and serves as the consumable handoff for execute-plans and frontend consumers.

## Delivered Route Surface

### Command surface

| Method | Path | Delivered by |
|--------|------|-------------|
| `POST` | `/bff/v1/commands` | BFF-FINAL-002/003 |
| `POST` | `/api/v1/operator/commands` | BFF-FINAL-001 (legacy compat) |

### Action catalog

| Method | Path | Delivered by |
|--------|------|-------------|
| `GET` | `/bff/actions` | BFF-FINAL-004 |

### SSE stream

| Method | Path | Delivered by |
|--------|------|-------------|
| `GET` | `/api/v1/stream/{channel}` | BFF-FINAL-005 (21 channels) |

### MCP tool import

| Method | Path | Delivered by |
|--------|------|-------------|
| `POST` | `/bff/v1/mcp/servers/{server_id}/import-tools` | BFF-FINAL-006 |
| `POST` | `/bff/mcp-servers/{server_id}/import-tools` | BFF-FINAL-006 (alias) |
| `POST` | `/bff/v1/mcp/servers/{server_id}/tools/{tool_id}/actions/{action}` | BFF-FINAL-006 |
| `POST` | `/bff/mcp-tools/{tool_id}/{action}` | BFF-FINAL-006 (alias) |

### Agora journal

| Method | Path | Delivered by |
|--------|------|-------------|
| `PATCH` | `/bff/agora/journal/{entry_id}` | BFF-FINAL-008 |

### v5 Interventions

| Method | Path | Delivered by |
|--------|------|-------------|
| `GET` | `/bff/v5/interventions` | BFF-FINAL-009 |
| `POST` | `/bff/v5/interventions/{id}/remediate` | BFF-FINAL-009 |
| `GET` | `/bff/approvals` | BFF-FINAL-009 |

### Evidence redaction (cross-cutting)

Applied to all read surfaces that include `evidence_refs`. Delivered by BFF-FINAL-007.

## BFF-FINAL Task Delivery Status

| Task | Status | Closeout commit |
|------|--------|----------------|
| BFF-FINAL-001 | ✅ done | — |
| BFF-FINAL-002 | ✅ done | — |
| BFF-FINAL-003 | ✅ done | — |
| BFF-FINAL-004 | ✅ done | — |
| BFF-FINAL-005 | ✅ done | — |
| BFF-FINAL-006 | ✅ done | `08ac4543` |
| BFF-FINAL-007 | ✅ done | — |
| BFF-FINAL-008 | ✅ done | — |
| BFF-FINAL-009 | ✅ done | `c0eb50cf` |
| BFF-FINAL-010 | ✅ done | `7a1953d0` (this delivery — adds delivery artifacts; runtime code unchanged from `d39496c4`) |

## Verification Evidence

```bash
python3 -m pytest services/control-plane/bff -q --tb=no
```

Result (HEAD `7a1953d0`, 2026-05-08): **457 passed, 0 failures, 36 warnings**

> Note: `7a1953d0` adds delivery artifacts only (DELIVERY_NOTE, CONTRACT_LOCK, coordination
> response, contract-verification doc). BFF runtime code is unchanged from prior commit
> `d39496c4`. Tests were re-confirmed at `7a1953d0` (187.48s).

Warnings are pre-existing `datetime.utcnow()` deprecation notices in `read_store.py`; no
functional impact.

## Known Deferred Items

| # | Item | Disposition |
|---|------|------------|
| D1 | `POST /bff/v5/interventions/{id}/decision` | Deferred to follow-on task |
| D2 | `POST /bff/v5/interventions/{id}/two-man-sign` | Deferred to follow-on task |
| D3 | SSE event emission from `/remediate` handler | Deferred; not blocking |
| D4 | MCP tool read projections (`GET /bff/mcp-tools`, etc.) | Deferred; not in BFF-FINAL-006 scope |
| D5 | `datetime.utcnow()` warnings in `read_store.py` | Pre-existing; file a follow-up tech-debt task |
| D6 | Multi-replica SSE replay store | BFF HA policy explicitly defers |

## Residual Risk

- No live browser QA against a deployed Pantheon environment was performed; paper-mode
  operation only.
- `_V5_INTERVENTIONS_STORE` in-memory stub is gated to `PANTHEON_ENV=dev` (default); safe for
  paper env but must be replaced by a durable store before live deployment.
- Decision and two-man-sign routes for v5 interventions are not yet implemented (D1/D2);
  frontend must hide or disable those flows until delivered.
