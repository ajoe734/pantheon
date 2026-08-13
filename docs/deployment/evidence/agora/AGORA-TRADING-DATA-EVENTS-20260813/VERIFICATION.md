# Verification Readout: AGORA-TRADING-DATA-EVENTS-20260813

**Task ID:** `AGORA-TRADING-DATA-EVENTS-20260813`  
**Title:** Add live widget data adapters and a real decision-event producer  
**Owner:** `Antigravity2`  
**Reviewer:** `Antigravity`  
**Status:** In Progress / Ready for Anchor Commit & Review  

---

## 1. Summary of Changes

### A. Live Widget Data Adapters (`services/control-plane/bff/agora/trading_data/`)
- Implemented `WidgetDataQueryRequest` & `WidgetDataQueryResponse` models strictly following the §18 Agora envelope specification. Every response carries `status`, `source`, `as_of`, `cutoff`, `lineage`, `data`, and `unavailable_reason`.
- Built `WidgetAdapterRegistry` with allowlisted authoritative query adapters:
  - `StrategyPerformanceWidgetAdapter`
  - `AccountPositionsWidgetAdapter`
  - `RiskMetricsWidgetAdapter`
- Unwired widget types remain unavailable with `unavailable_reason="UNWIRED_WIDGET_TYPE"` and cannot enter live registry.
- Point-in-time `cutoff` filtering enforces exclusion of records past specified timestamps.
- Live-profile fixture guard ensures that when profile is `live`/`prod`, no sample/mock fixtures are returned (`unavailable_reason="LIVE_PROFILE_NO_FIXTURES"`).
- Risk metrics fail closed: missing or stale risk data returns `status="unavailable"` with explicit reason `STALE_DATA` / `DATA_MISSING`.

### B. Real Decision Event Producer (`services/control-plane/bff/agora/decision_projection/`)
- Implemented `DecisionEventRecord` & `DecisionProjectionCommand` for owner-scoped decision event projection (`tenant_id`, `user_id`, `owner_scope="user_private"`).
- Projected decision events carry `probability`, `expected_value`, `risk`, `invalidation_conditions`, `freshness`, and `evidence_refs`.
- **Idempotency:** Identical `idempotency_key` per tenant/user returns existing record without side effects or duplicates.
- **Fail-Closed:** Missing or stale signal/risk data sets status to `"invalidated"` / `"stale"`, `probability=0.0`, `expected_value=0.0`, and records explicit invalidation conditions.
- **Restart Recovery:** Persistent `DecisionEventStore` reloads event records across service restarts.
- **Absence of Broker/Order Authority:** Pure event producer with zero order submission or execution authority (`has_broker_authority=False`).

### C. Top-level Agora Router Integration (`services/control-plane/bff/agora/router.py`)
- Mounted `trading_data` and `decision_projection` sub-routers into `create_agora_router()`.

---

## 2. Test Verification

11 targeted unit and integration tests executed with clean 100% pass rate:

```bash
.venv-pantheon/bin/python3 -m pytest -q services/control-plane/bff/agora/trading_data services/control-plane/bff/agora/decision_projection
```

### Verified Test Cases:
1. `test_widget_query_contract_and_allowlist`: Proves all required response envelope fields.
2. `test_unwired_widget_type_fails_closed`: Proves unwired widget types return `UNWIRED_WIDGET_TYPE`.
3. `test_two_tenant_isolation_negative`: Proves Tenant A cannot read Tenant B widget data or decision events.
4. `test_point_in_time_cutoff`: Proves point-in-time filtering works correctly.
5. `test_stale_and_unavailable_source`: Proves risk metrics and stale data fail closed.
6. `test_live_profile_fixture_guard`: Proves live profile rejects mock fixtures.
7. `test_decision_projection_idempotency`: Proves producer idempotency.
8. `test_two_tenant_negative_isolation`: Proves tenant boundary on decision event queries.
9. `test_stale_or_missing_risk_data_fails_closed`: Proves stale/failed risk produces zero probability / invalidated event.
10. `test_restart_recovery`: Proves events survive store re-instantiation.
11. `test_absence_of_broker_order_authority`: Proves no order authority exists in decision producer.
