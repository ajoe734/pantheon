# BFF-FINAL-004 - Canonical Action Catalog

Priority: P0

Depends on: BFF-FINAL-001

Area: action metadata and frontend contract handoff

## Goal

Publish the backend canonical BFF action catalog that the frontend maps into v4 `ActionDescriptor[]`.

## Contract Inputs

- BFF action table is canonical for backend implementation.
- BFF must emit `ActionDescriptor[]` compatible metadata.
- v3/frontend `availableActions` is compatibility only.

## Implementation Scope

Likely files:

- new `services/control-plane/bff/action_catalog.py`
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/models.py`
- `services/control-plane/bff/BFF_API_CONTRACT.md`
- `docs/bff/README.md`
- new catalog tests

## Steps

1. Define `BffActionCatalogEntry` with:
   - action id
   - entity type
   - endpoint
   - risk level
   - approval requirement
   - confirm token requirement
   - two-man requirement
   - cooldown seconds
   - idempotency required
   - required capabilities / roles
2. Materialize the initial catalog for current Pantheon operator actions.
3. Add `GET /bff/actions` or equivalent read endpoint for frontend discovery.
4. Ensure catalog entries can be mapped to frontend `ActionDescriptor[]`.
5. Add tests that every exposed action endpoint has a catalog entry.
6. Add tests that no catalog entry advertises `requires_*` as a success status.

## Acceptance Criteria

- Backend repo has the canonical action table.
- Frontend can fetch descriptor-compatible action metadata.
- High-risk actions have approval/confirm/two-man/cooldown/idempotency metadata.
- Tests fail when a new action endpoint lacks catalog metadata.

## Implementation (Claude, 2026-05-07)

### Delivered artifacts

| File | Role |
|---|---|
| `services/control-plane/bff/action_catalog.py` | Canonical catalog — 20 entries covering all CommandType values |
| `services/control-plane/bff/models.py` | Added `RiskLevel`, `BffActionCatalogEntry`, `BffActionCatalogResponse` |
| `services/control-plane/bff/main.py` | Added `GET /bff/actions` endpoint (auth-gated) |
| `services/control-plane/bff/test_action_catalog.py` | 11 contract tests |

### Endpoint

`GET /bff/actions` — requires `Authorization: Bearer <token>`.  Returns `BffActionCatalogResponse` with:
- `catalog`: list of `BffActionCatalogEntry` (one per `CommandType`)
- `version`: `"v1"`
- `generated_at`: UTC timestamp

### Catalog highlights

- All 20 `CommandType` values have a catalog entry.
- CRITICAL entries (`ActivateKillSwitch`, `HardRollback`, `LiquidateAll`) require `requires_two_man=True`, `requires_confirm_token=True`, `requires_approval=True`.
- HIGH entries (`PauseRuntime`, `IssueRiskOff`, `IssueSafeMode`, `ExecuteRollback`) require `requires_confirm_token=True`.
- `ActionCommandStatus` contains only final-success values — no `requires_*` status fragments.

## Verification

```bash
python3 -m pytest services/control-plane/bff -k "action_catalog or command or final_contract" -q
# 59 passed in 82s
```
