# Product Functional Closure Evidence: Agora Projections & Identity Resolution

**Task ID**: `PFG-AGORA-PROJECTIONS-20260820`  
**Milestone**: `pantheon_product_functional_closure_2026-08-20`  
**Worker Identity**: `Antigravity2`  

---

## 1. Executive Summary

This task closes the Agora functional projection gap (§6 of `01_CURRENT_PRODUCT_FUNCTION_GAP_2026-08-20.md`):
1. **Workspace & Candidate Pool Identity**: Strategy/version resolution yields exactly one authoritative candidate pool and workspace identity. Lens (`lens-A`..`E`) is treated strictly as a frontend view recipe, failing closed (404) when requested as a pool ID.
2. **Durable Decision Event Production**: Candidate reviews (`review_candidate_pool_member`) and domain signal/risk evaluations produce durable, owner-scoped `TradingDecisionEvent` records conforming to the `agora.trading.v1` contract with `no_order_route_proof="agora_decision_support_only"`.
3. **Performance Suggestion & Telemetry Projection**: Introduced `PerformanceSuggestionProducer` that maps telemetry and paper evaluation outcomes (drift, drawdown breaches, sharpe shifts) to typed `AdjustmentSuggestion` entries in `PerformanceSuggestionStore`. `PerformanceProjectionService` integrates journeys, suggestions, and explicit typed availability/freshness indicators.
4. **Active Widget Query Adapters**: Extended `TradingDataService` allowlist registry with adapters for active Trading Room widgets (`signal_decision_queue`, `candidate_funnel`, `candidate_ranking_table`, `evidence_trace`, `strategy_performance`, `account_positions`, `risk_metrics`). Unwired widget queries return explicit typed `UNAVAILABLE` with reason `unwired_widget_type` rather than seed mocks or fabricated states.
5. **No Duplicate Stores / Single Producer per Projection**: Kept existing stores (`TradingRoomStore`, `ResearchPlanStore`, `PerformanceSuggestionStore`, `DecisionEventStore`, `TradeJourneyStore`) and wired authoritative domain producers.

---

## 2. Key Architecture Components

### A. Candidate Pool & Workspace Resolution
- **Endpoints**:
  - `GET /bff/agora/trading-room/strategies/{strategy_id}/workspace`: Resolves the active workspace for `(strategy_id, version)`.
  - `GET /bff/agora/trading-room/workspaces/lookup`: Parameterized lookup route.
  - `GET /bff/agora/candidate-pools/lookup`: Resolves candidate pool by strategy / version with flexible identifier normalization.
  - `GET /bff/agora/strategies/{strategy_id}/candidate-pool`: Direct strategy pool resolution.
- **Store Methods**:
  - `TradingRoomStore.get_workspace_for_strategy` & `list_workspaces` (supported in both in-memory and PostgreSQL stores).
  - `MemoryResearchPlanStore.list_candidate_pools` & `get_candidate_pool_for_strategy`.

### B. Decision Event Production
- **`DecisionEventProducer`** (`services/control-plane/bff/agora/decision_projection/producer.py`):
  - Ingests `DecisionProjectionCommand` with signal and risk evidence.
  - Validates freshness, point-in-time cutoffs, and fail-closed risk criteria.
  - Generates owner-scoped `DecisionEventRecord` and projects to `TradingRoomStore` via `project_to_trading_room`.
- **Candidate Member Review Linkage** (`services/control-plane/bff/agora/research/router.py`):
  - On `POST /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/review`, updates candidate lifecycle state and automatically emits a durable `TradingDecisionEvent` (`decision_state="approved_by_trader"` / `"rejected_by_trader"` / `"deferred"`).

### C. Performance Suggestion Production
- **`PerformanceSuggestionProducer`** (`services/control-plane/bff/agora/performance/producer.py`):
  - Ingests `PerformanceOutcomeEvaluationInput` with telemetry / paper performance measurements.
  - Generates deterministic, typed `AdjustmentSuggestion` models with provenance and `no_order_route_proof="agora_suggestion_state_only"`.
  - Persists directly to `PerformanceSuggestionStore`.

### D. Widget Data Query Adapters
- **`TradingDataService`** (`services/control-plane/bff/agora/trading_data/`):
  - `SignalDecisionQueueWidgetAdapter` (`agora.trading.events`): Queries `TradingRoomStore.list_decision_events`.
  - `CandidateFunnelWidgetAdapter` (`agora.candidate.members`): Aggregates candidate lifecycle stages from `ResearchPlanStore`.
  - `CandidateRankingWidgetAdapter` (`agora.candidate.members`): Queries candidate pool members.
  - `EvidenceTraceWidgetAdapter` (`agora.research.evidence_refs`): Extracts research evidence references.
  - `StrategyPerformanceWidgetAdapter`, `AccountPositionsWidgetAdapter`, `RiskMetricsWidgetAdapter`.
  - Unwired widget types fail closed with `WidgetUnavailableReason.UNWIRED_WIDGET_TYPE`.

---

## 3. Verification & Validation

Full Agora test suite execution:
```bash
.venv-pantheon/bin/python3 -m pytest -q services/control-plane/bff/agora/
```
**Results**: `192 passed, 16 skipped, 3 warnings` (100% passing).

Dedicated test coverage includes:
- `services/control-plane/bff/agora/trading_room/test_agora_projections_workspace_pool_identity.py` (Workspace/pool resolution, lens isolation)
- `services/control-plane/bff/agora/decision_projection/test_agora_projections_decision_producer.py` (Review -> decision event emission, producer replay)
- `services/control-plane/bff/agora/performance/test_agora_projections_performance_widgets.py` (Telemetry suggestions, widget query OK & unavailable states)
