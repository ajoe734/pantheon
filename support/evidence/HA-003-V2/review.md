# HA-003-V2 Review: Degraded Mode Matrix (Part D4)

Reviewer: Claude
Date: 2026-05-19
Commit reviewed: 5d737503

## Artifacts reviewed

- `services/bff/ha/degraded_mode.py`
- `tests/bff/test_degraded_mode.py`

## Acceptance criteria

| Criterion | Result |
|---|---|
| Schema/code matches 2026-05-19 supplement Part D4 | ✅ Pass |
| Unit tests cover happy path and at least one fail-closed case | ✅ Pass (9 tests) |
| Reviewer signs off via ai-status.sh approve | ✅ This document |
| Artifact exists in worktree at closeout | ✅ Both files present |
| No L1 canonical doc modified | ✅ Confirmed |

## Matrix verification

7 rows verified:

| key | error_code | guard | http_status | strict | fallback_allowed |
|---|---|---|---|---|---|
| auth_oidc_jwks_unavailable | AUTH_UNAVAILABLE | reject_all | 503 | true | false |
| idempotency_store_unavailable | IDEMPOTENCY_UNAVAILABLE | reject_all | 503 | true | false |
| audit_handoff_unavailable | AUDIT_UNAVAILABLE | reject_all | 503 | true | false |
| registry_governance_unavailable | REGISTRY_GOVERNANCE_UNAVAILABLE | reject_registry_governance | 503 | true | false |
| runtime_manager_unavailable | RUNTIME_MANAGER_UNAVAILABLE | reject_runtime_lifecycle | 503 | true | false |
| telemetry_incident_unavailable | TELEMETRY_UNAVAILABLE | block_health_dependent | 503 | true | false |
| sse_fanout_unavailable | SSE_FANOUT_UNAVAILABLE | guard_realtime_dependent | 503 | true | false |

## Key behaviors confirmed

- `build_typed_503_response` returns correct shape with `error`, `meta`, `surfaces`; no silent `data` fallback
- `guard_command` blocks correctly per guard type and command group membership
- Unknown failure keys → `ValueError` (no silent fallback)
- Unclassified commands during active degradation → blocked by `rows[0]` (fail-closed)
- Frozen dataclasses enforce immutability at runtime

## Test run

```
python3 -m pytest tests/bff/test_degraded_mode.py -v
9 passed in 0.79s
```

## Verdict

APPROVED. Implementation correctly realizes the Part D4 7-row degraded mode matrix with strict fail-closed semantics throughout.
