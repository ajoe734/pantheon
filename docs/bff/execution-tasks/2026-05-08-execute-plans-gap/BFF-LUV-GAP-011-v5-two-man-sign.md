# BFF-LUV-GAP-011 - v5 Two-Man Sign Alias Decision

Priority: P1

Area: v5 HIQ intervention decision aliases

## Goal

Resolve the remaining final-contract route reference:

- `POST /bff/v5/interventions/{id}/two-man-sign`

Pantheon currently implements:

- `GET /bff/v5/interventions`
- `POST /bff/v5/interventions/{id}/remediate`
- `POST /bff/v1/commands` with `RemediateSentinelIntervention`

## Decision Required

Choose one:

1. Implement `/two-man-sign` as an alias that records the second operator signature and then delegates to the existing remediation/approval flow when complete.
2. Mark `/two-man-sign` as superseded by `/remediate` plus `RemediateSentinelIntervention`, then update the contract registry and Lovable handoff notes so the frontend does not call it.

## Decision

**Supersede** `/bff/v5/interventions/{id}/two-man-sign` in favor of `/bff/v5/interventions/{id}/remediate` + `RemediateSentinelIntervention`.

Rationale:
- `POST /bff/v5/interventions/{id}/remediate` is already a two-man guarded surface. It requires `two_man_signature_id` (or a recognized alias) and returns `409` when the second-operator signature is missing.
- Adding a separate `/two-man-sign` step would split what the canonical remediation command already handles atomically, introducing an unnecessary two-phase call without architectural benefit.
- Frontend callers should submit `/remediate` with the second-operator signature directly.

## Contract Registry Update

Row updated in `services/control-plane/bff/contract_snapshots/execute_plans_bff_routes.json`:
- status: `superseded_with_reason`
- reason: Superseded by POST /bff/v5/interventions/{id}/remediate + RemediateSentinelIntervention

## Acceptance Criteria

- The route registry has an explicit disposition for `/bff/v5/interventions/{id}/two-man-sign`. ✓
- Existing `test_v5_interventions.py` remains green. ✓

## Verification

```bash
python3 -m pytest services/control-plane/bff/test_v5_interventions.py -q
# 21 passed
python3 -m pytest services/control-plane/bff/test_execute_plans_contract_registry.py -q
# 5 passed
```
