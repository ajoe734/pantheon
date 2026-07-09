import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { managementClient, type PersonaFleetAggregate } from "@/lib/bff/client";
import * as managementApi from "@/lib/bff-v1/management";
import type {
  ManagementCostAttributionResponse,
  ManagementPerformanceAttributionResponse,
  ManagementPersonaLeagueMoversResponse,
  ManagementPersonaLeagueResponse,
  ManagementPersonaLeagueRankingsResponse,
  ManagementPortfolioBookExposureResponse,
  ManagementPortfolioBookPositionsResponse,
  ManagementQuarterlyRankingResponse,
  ManagementTradingPulseRankingsResponse,
  ManagementTradingPulseResponse,
} from "@/lib/bff-v1/management";

import {
  ManagementPerformanceReviewPanel,
  PERFORMANCE_REVIEW_LIMITS,
} from "./index";

const surfaceOk = { status: "ok", source: "bff_composed" };
const personaFleetSurface = { status: "ok", source: "bff_composed_slim_list" };

function quarterlyItem(index: number, name = `Persona ${index}`) {
  return {
    id: `rank-${index}`,
    persona_id: `persona-${index}`,
    name,
    owner: "research",
    state: "active",
    risk: index === 1 ? "watch" : "ok",
    archetype: "macro",
    tier: "champion",
    tier_id: "tier-champion",
    tier_label: "Champion",
    rank: index,
    score: 95 - index,
    overall_score: 95 - index,
    score_field: "overall_score",
    metrics: { pnl: 12000 - index },
    components: {
      pnl_quality: 0.42,
      drawdown_control: 0.24,
      execution: 0.18,
      evidence: 0.1,
    },
    quarter: "2026-Q3",
    quarter_window: {
      quarter: "2026-Q3",
      year: 2026,
      quarter_number: 3,
      label: "Q3 2026",
      start_at: "2026-07-01T00:00:00Z",
      end_exclusive_at: "2026-10-01T00:00:00Z",
      timezone: "UTC",
    },
    formula_version: "v2026.3",
    basis: "weighted_score",
    eligible: true,
    exclusion_reason: null,
    evidence_coverage: 0.8,
    source_confidence: "formal",
    period: "quarter",
    criteria: "overall",
    governance_state: "recommendation",
  };
}

const personaLeagueResponse = {
  data: {
    id: "management-persona-league",
    items: [
      {
        id: "persona-alpha",
        persona_id: "persona-alpha",
        name: "Alpha Persona",
        owner: "research",
        state: "active",
        risk: "ok",
        archetype: "macro",
        routed_strategy_count: 3,
        success_rate: 0.71,
      },
    ],
    summary: { persona_count: 12, returned_count: 1 },
  },
  page_info: { next_page_token: null, total: 12, page_size: 1 },
  meta: {
    snapshot_at: "2026-07-03T12:00:00Z",
    surfaces: { management_persona_league: surfaceOk },
  },
} as ManagementPersonaLeagueResponse;

const personaLeagueRankingsResponse = {
  data: {
    id: "management-persona-league-rankings",
    items: [
      {
        id: "persona-league-overall",
        ranking_id: "persona-league-overall",
        criteria: "overall",
        label: "Overall Performance",
        formula_version: "pm12-default-v1",
        weights: { pnl_quality: 0.42, drawdown_control: 0.24, execution: 0.18, evidence: 0.16 },
        items: [
          {
            id: "persona-alpha",
            persona_id: "persona-alpha",
            name: "Alpha Persona",
            owner: "research",
            state: "active",
            risk: "ok",
            archetype: "macro",
            tier: "champion",
            tier_id: "tier-champion",
            tier_label: "Champion",
            rank: 1,
            score: 94,
            overall_score: 94,
            metrics: { telemetry_coverage_count: 5 },
            components: { overall_score: 94 },
            eligible: true,
            exclusion_reason: null,
            evidence_coverage: 0.5,
            source_confidence: "formal",
            period: "short_cycle",
            criteria: "overall",
          },
        ],
        ranked_count: 1,
      },
    ],
    summary: {
      persona_count: 12,
      criteria: ["overall"],
    },
  },
  page_info: { next_page_token: null, total: 1, page_size: 1 },
  meta: { surfaces: { management_persona_league_rankings: surfaceOk } },
} as ManagementPersonaLeagueRankingsResponse;

const emptyPersonaLeagueRankingsResponse = {
  ...personaLeagueRankingsResponse,
  data: {
    ...personaLeagueRankingsResponse.data,
    items: [],
  },
} as ManagementPersonaLeagueRankingsResponse;

const personaFleetResponse = {
  data: {
    items: [
      {
        id: "persona-alpha",
        persona_id: "persona-alpha",
        name: "Alpha Persona",
        owner: "research",
        status: "healthy",
        health: "healthy",
        runtime_id: "runtime-alpha",
        performance_summary: {
          totalPnl: 124000,
          violationCount: 0,
        },
      },
    ],
    summary: {
      total_personas: 12,
      returned_personas: 1,
      critical_personas: 0,
      degraded_personas: 0,
      healthy_personas: 1,
      bound_personas: 1,
      runtime_bound_personas: 1,
    },
  },
  page_info: { next_page_token: null, total: 12, page_size: 1 },
  meta: {
    snapshot_at: "2026-07-03T12:00:00Z",
    surfaces: { persona_fleet: personaFleetSurface },
  },
} as PersonaFleetAggregate;

const personaMoversResponse = {
  data: {
    id: "management-persona-league-movers",
    items: [
      {
        id: "mover-alpha",
        mover_id: "mover-alpha",
        persona_id: "persona-alpha",
        name: "Alpha Persona",
        owner: "research",
        state: "active",
        risk: "ok",
        archetype: "macro",
        tier: "champion",
        tier_id: "tier-champion",
        tier_label: "Champion",
        rank: 1,
        score: 94,
        overall_score: 94,
        metrics: {},
        components: {},
        current_rank: 1,
        previous_rank: 4,
        rank_delta: -3,
        direction: "up",
        current_score: 94,
        previous_score: 88,
        score_delta: 6,
        score_delta_display: "+6",
        baseline_status: "ok",
        formula_version: "v2026.3",
        movement: { direction: "up", rank_delta: -3, score_delta: 6 },
      },
    ],
    summary: {
      persona_count: 12,
      mover_count: 1,
      returned_count: 1,
      direction: "all",
      formula_version: "v2026.3",
      baseline_status: "ok",
      baseline_unavailable_count: 0,
      up_count: 1,
      down_count: 0,
      flat_count: 0,
      new_count: 0,
      basis: "prior_quarter",
    },
  },
  page_info: { next_page_token: null, total: 1, page_size: 1 },
  meta: { surfaces: { management_persona_league_movers: surfaceOk } },
} as ManagementPersonaLeagueMoversResponse;

const quarterlyRankingResponse = {
  data: {
    id: "quarterly-ranking",
    quarter: "2026-Q3",
    quarter_window: {
      quarter: "2026-Q3",
      year: 2026,
      quarter_number: 3,
      label: "Q3 2026",
      start_at: "2026-07-01T00:00:00Z",
      end_exclusive_at: "2026-10-01T00:00:00Z",
      timezone: "UTC",
    },
    formula: {
      id: "formula",
      formula_id: "formula",
      version: "v2026.3",
      formula_version: "v2026.3",
      weights: { pnl_quality: 0.42, drawdown_control: 0.24, execution: 0.18, evidence: 0.16 },
      score_field: "overall_score",
      components: [],
      basis: "weighted_score",
      policy: "performance_review",
      governance_evidence_refs: ["evidence-1"],
      version_history: [],
      change_control: {
        version_policy: "governed",
        requires_governance_evidence: true,
        governance_evidence_refs: ["evidence-1"],
        authority: "management",
      },
    },
    items: [
      quarterlyItem(1, "Alpha Persona"),
      quarterlyItem(2, "Bravo Persona"),
      quarterlyItem(3, "Charlie Persona"),
      quarterlyItem(4, "Delta Persona"),
      quarterlyItem(5, "Echo Persona"),
      quarterlyItem(6, "Foxtrot Persona"),
      quarterlyItem(7, "Golf Persona"),
      quarterlyItem(8, "Hotel Persona"),
      quarterlyItem(9, "Overflow Persona"),
    ],
    evidence_refs: [],
    summary: {
      quarter: "2026-Q3",
      formula_version: "v2026.3",
      persona_count: 12,
      ranked_count: 12,
      returned_count: 9,
      top_persona_id: "persona-1",
      evidence_ref_count: 1,
      redacted_evidence_count: 0,
      basis: "weighted_score",
    },
  },
  page_info: { next_page_token: null, total: 12, page_size: 9 },
  meta: {
    snapshot_at: "2026-07-03T12:00:00Z",
    surfaces: { management_quarterly_ranking: surfaceOk },
  },
} as ManagementQuarterlyRankingResponse;

const personaAttributionResponse = {
  data: {
    id: "persona-attribution",
    period: "quarter",
    dimensions: ["persona"],
    items: [
      {
        id: "attr-persona-alpha",
        dimension: "persona",
        dimension_key: "persona-alpha",
        label: "Alpha Persona",
        period: "quarter",
        rank: 1,
        metrics: {
          runtime_count: 2,
          telemetry_runtime_count: 2,
          holding_count: 4,
          total_pnl: 124000,
          unrealized_pnl: 40000,
          realized_pnl: 84000,
          total_notional: 2100000,
          total_market_value: 2210000,
          total_exposure: 1800000,
          worst_drawdown: -0.041,
          average_fill_rate: 0.982,
          average_slippage_bps: 3.5,
          total_trades: 240,
          latest_telemetry_at: "2026-07-03T11:30:00Z",
          pnl_contribution_pct: 0.31,
          notional_weight: 0.28,
        },
        total_pnl: 124000,
        pnl_contribution_pct: 0.31,
        notional_weight: 0.28,
        runtime_count: 2,
        holding_count: 4,
      },
    ],
    summary: {
      period: "quarter",
      dimensions: ["persona"],
      supported_dimensions: ["persona", "pool"],
      row_count: 1,
      returned_row_count: 1,
      runtime_count: 2,
      telemetry_runtime_count: 2,
      holding_count: 4,
      total_pnl: 124000,
      total_notional: 2100000,
      total_exposure: 1800000,
      worst_drawdown: -0.041,
      average_fill_rate: 0.982,
      average_slippage_bps: 3.5,
      total_trades: 240,
      latest_telemetry_at: "2026-07-03T11:30:00Z",
      basis: "telemetry",
    },
  },
  page_info: { next_page_token: null, total: 1, page_size: 1 },
  meta: { surfaces: { performance_attribution: surfaceOk } },
} as ManagementPerformanceAttributionResponse;

const poolAttributionResponse = {
  ...personaAttributionResponse,
  data: {
    ...personaAttributionResponse.data,
    id: "pool-attribution",
    dimensions: ["pool"],
    items: [
      {
        ...personaAttributionResponse.data.items[0],
        id: "attr-pool-growth",
        dimension: "pool",
        dimension_key: "pool-growth",
        label: "Growth Pool",
      },
    ],
  },
} as ManagementPerformanceAttributionResponse;

const costAttributionResponse = {
  data: {
    id: "management-cost-attribution",
    items: [
      {
        id: "cost-alpha",
        costId: "cost-alpha",
        cost_id: "cost-alpha",
        capitalPoolId: "pool-growth",
        capital_pool_id: "pool-growth",
        capitalPoolName: "Growth Pool",
        capital_pool_name: "Growth Pool",
        personaId: "persona-alpha",
        persona_id: "persona-alpha",
        personaLabel: "Alpha Persona",
        persona_label: "Alpha Persona",
        strategyId: "strategy-carry",
        strategy_id: "strategy-carry",
        strategyLabel: "Carry Strategy",
        strategy_label: "Carry Strategy",
        runtimeIds: ["runtime-alpha"],
        runtime_ids: ["runtime-alpha"],
        totalCost: 9400,
        total_cost: 9400,
        commissionCost: 2400,
        commission_cost: 2400,
        slippageCost: 5100,
        slippage_cost: 5100,
        infrastructureCost: 1900,
        infrastructure_cost: 1900,
        allocatedCapital: 500000,
        allocated_capital: 500000,
        riskBudget: 250000,
        risk_budget: 250000,
        totalTrades: 240,
        total_trades: 240,
        totalNotional: 2100000,
        total_notional: 2100000,
        avgSlippageBps: 3.5,
        avg_slippage_bps: 3.5,
        latestAt: "2026-07-03T11:30:00Z",
        latest_at: "2026-07-03T11:30:00Z",
        costBasis: "execution_allocated",
        cost_basis: "execution_allocated",
      },
    ],
    summary: {
      rowCount: 1,
      row_count: 1,
      returnedRowCount: 1,
      returned_row_count: 1,
      capitalPoolCount: 1,
      capital_pool_count: 1,
      personaCount: 1,
      persona_count: 1,
      strategyCount: 1,
      strategy_count: 1,
      totalCost: 9400,
      total_cost: 9400,
      totalCommissionCost: 2400,
      total_commission_cost: 2400,
      totalSlippageCost: 5100,
      total_slippage_cost: 5100,
      totalInfrastructureCost: 1900,
      total_infrastructure_cost: 1900,
      period: "quarter",
      policy: "performance_review",
      basis: "execution_allocated",
    },
  },
  page_info: { next_page_token: null, total: 1, page_size: 1 },
  meta: { surfaces: { cost_attribution: surfaceOk } },
} as ManagementCostAttributionResponse;

const portfolioExposureResponse = {
  data: {
    id: "portfolio-exposure",
    items: [
      {
        id: "pool-growth",
        pool_id: "pool-growth",
        capital_pool_id: "pool-growth",
        name: "Growth Pool",
        status: "active",
        risk_policy_ref: "risk-policy-growth",
        currency: "USD",
        risk_budget: 1000000,
        current_exposure: 740000,
        exposure_amount: 740000,
        available_budget: 260000,
        risk_budget_utilization: 0.74,
        risk_state: "near_limit",
        exposure_source: "runtime_marks",
        runtime_count: 2,
        active_runtime_count: 2,
      },
    ],
    exposures: [
      {
        id: "pool-growth",
        pool_id: "pool-growth",
        capital_pool_id: "pool-growth",
        name: "Growth Pool",
        status: "active",
        risk_policy_ref: "risk-policy-growth",
        currency: "USD",
        risk_budget: 1000000,
        current_exposure: 740000,
        exposure_amount: 740000,
        available_budget: 260000,
        risk_budget_utilization: 0.74,
        risk_state: "near_limit",
        exposure_source: "runtime_marks",
        runtime_count: 2,
        active_runtime_count: 2,
      },
    ],
    summary: {
      exposure_count: 1,
      returned_exposure_count: 1,
      total_pools: 1,
      active_pool_count: 1,
      risk_budget_total: 1000000,
      current_exposure_total: 740000,
      available_budget_total: 260000,
      risk_budget_utilization: 0.74,
      over_budget_count: 0,
      near_limit_count: 1,
      unknown_exposure_count: 0,
      telemetry_runtime_count: 2,
      total_pnl: 124000,
      max_drawdown: -0.041,
      average_fill_rate: 0.982,
      total_trades: 240,
      latest_telemetry_at: "2026-07-03T11:30:00Z",
      basis: "runtime_marks",
    },
  },
  items: [],
  exposures: [],
  summary: {
    exposure_count: 1,
    returned_exposure_count: 1,
    total_pools: 1,
    active_pool_count: 1,
    risk_budget_total: 1000000,
    current_exposure_total: 740000,
    available_budget_total: 260000,
    risk_budget_utilization: 0.74,
    over_budget_count: 0,
    near_limit_count: 1,
    unknown_exposure_count: 0,
  },
  page_info: { next_page_token: null, total: 1, page_size: 1 },
  meta: { surfaces: { portfolio_book_exposure: surfaceOk } },
} as ManagementPortfolioBookExposureResponse;

const portfolioPositionsResponse = {
  data: {
    summary: {
      position_count: 1,
      returned_position_count: 1,
      active_position_count: 1,
      paper_position_count: 0,
      live_position_count: 1,
      runtime_count: 1,
      telemetry_runtime_count: 1,
      total_notional: 500000,
      total_market_value: 520000,
      total_unrealized_pnl: 20000,
      total_realized_pnl: 104000,
      total_pnl: 124000,
      latest_mark_at: "2026-07-03T11:30:00Z",
    },
    items: [
      {
        id: "position-alpha",
        holding_id: "holding-alpha",
        position_id: "position-alpha",
        runtime_id: "runtime-alpha",
        capital_pool_id: "pool-growth",
        persona_id: "persona-alpha",
        strategy_id: "strategy-carry",
        deployment_stage: "live",
        status: "active",
        symbol: "SPY",
        side: "long",
        quantity: 100,
        average_price: 5000,
        mark_price: 5200,
        market_value: 520000,
        notional: 500000,
        exposure: 520000,
        weight: 0.24,
        total_pnl: 20000,
        unrealized_pnl: 20000,
        realized_pnl: 0,
        last_mark_at: "2026-07-03T11:30:00Z",
      },
    ],
    positions: [
      {
        id: "position-alpha",
        holding_id: "holding-alpha",
        position_id: "position-alpha",
        runtime_id: "runtime-alpha",
        capital_pool_id: "pool-growth",
        persona_id: "persona-alpha",
        strategy_id: "strategy-carry",
        deployment_stage: "live",
        status: "active",
        symbol: "SPY",
        side: "long",
        quantity: 100,
        average_price: 5000,
        mark_price: 5200,
        market_value: 520000,
        notional: 500000,
        exposure: 520000,
        weight: 0.24,
        total_pnl: 20000,
        unrealized_pnl: 20000,
        realized_pnl: 0,
        last_mark_at: "2026-07-03T11:30:00Z",
      },
    ],
  },
  items: [],
  positions: [],
  summary: {
    position_count: 1,
    returned_position_count: 1,
    active_position_count: 1,
    paper_position_count: 0,
    live_position_count: 1,
    runtime_count: 1,
    telemetry_runtime_count: 1,
  },
  page_info: { next_page_token: null, total: 1, page_size: 1 },
  meta: { surfaces: { portfolio_book_positions: surfaceOk } },
} as ManagementPortfolioBookPositionsResponse;

const tradingPulseResponse = {
  data: {
    id: "management-trading-pulse",
    summary: {
      runtime_count: 1,
      telemetry_coverage_count: 1,
      by_status: { active: 1 },
      by_stage: { live: 1 },
      total_pnl: 124000,
      worst_drawdown: -0.041,
      average_fill_rate: 0.982,
      worst_slippage_bps: 3.5,
      total_trades: 240,
      baseline_comparison_count: 1,
      baseline_breached_count: 0,
      baseline_watch_count: 1,
      by_baseline_status: { watch: 1 },
    },
    cards: [],
    rankings: [],
    runtime_rows: [
      {
        runtime_id: "runtime-alpha",
        runtime_binding_id: "binding-alpha",
        deployment_stage: "live",
        status: "active",
        telemetry_summary: {
          total_pnl: 124000,
          fill_rate: 0.982,
          avg_slippage_bps: 3.5,
          total_trades: 240,
        },
        baseline_comparison: {
          runtime_id: "runtime-alpha",
          runtime_binding_id: "binding-alpha",
          deployment_stage: "live",
          status: "watch",
        },
        last_updated_at: "2026-07-03T11:30:00Z",
      },
    ],
    baseline_comparisons: [],
  },
  page_info: { next_page_token: null, total: 1, page_size: 1 },
  meta: { surfaces: { management_trading_pulse: surfaceOk } },
} as ManagementTradingPulseResponse;

const tradingRankingsResponse = {
  data: {
    id: "management-trading-pulse-rankings",
    items: [
      {
        block_id: "top-pnl",
        label: "Top PnL",
        metric: "pnl",
        sort_order: "desc",
        items: [
          {
            runtime_id: "runtime-alpha",
            rank: 1,
            pnl: 124000,
            ranking_metric: "pnl",
            ranking_metric_value: 124000,
          },
        ],
      },
    ],
    summary: {
      runtime_count: 1,
      ranking_block_count: 1,
      ranked_item_count: 1,
      criteria: ["pnl"],
      limit: 5,
      top_runtime_id: "runtime-alpha",
    },
  },
  page_info: { next_page_token: null, total: 1, page_size: 1 },
  meta: { surfaces: { management_trading_pulse_rankings: surfaceOk } },
} as ManagementTradingPulseRankingsResponse;

const emptyPersonaLeagueResponse = {
  ...personaLeagueResponse,
  data: {
    ...personaLeagueResponse.data,
    items: [],
    summary: { persona_count: 0, returned_count: 0 },
  },
} as ManagementPersonaLeagueResponse;

const emptyPersonaFleetResponse = {
  ...personaFleetResponse,
  data: {
    ...personaFleetResponse.data,
    items: [],
    summary: {
      ...personaFleetResponse.data.summary,
      total_personas: 0,
      returned_personas: 0,
      healthy_personas: 0,
      runtime_bound_personas: 0,
    },
  },
} as PersonaFleetAggregate;

const emptyPersonaMoversResponse = {
  ...personaMoversResponse,
  data: {
    ...personaMoversResponse.data,
    items: [],
    summary: {
      ...personaMoversResponse.data.summary,
      persona_count: 0,
      mover_count: 0,
      returned_count: 0,
      up_count: 0,
      down_count: 0,
    },
  },
} as ManagementPersonaLeagueMoversResponse;

const emptyQuarterlyRankingResponse = {
  ...quarterlyRankingResponse,
  data: {
    ...quarterlyRankingResponse.data,
    items: [],
    summary: {
      ...quarterlyRankingResponse.data.summary,
      persona_count: 0,
      ranked_count: 0,
      returned_count: 0,
    },
  },
} as ManagementQuarterlyRankingResponse;

const emptyAttributionResponse = {
  ...personaAttributionResponse,
  data: {
    ...personaAttributionResponse.data,
    items: [],
    summary: {
      ...personaAttributionResponse.data.summary,
      row_count: 0,
      returned_row_count: 0,
      total_trades: 0,
      total_pnl: null,
    },
  },
} as ManagementPerformanceAttributionResponse;

const emptyCostResponse = {
  ...costAttributionResponse,
  data: {
    ...costAttributionResponse.data,
    items: [],
    summary: {
      ...costAttributionResponse.data.summary,
      rowCount: 0,
      row_count: 0,
      returnedRowCount: 0,
      returned_row_count: 0,
      totalCost: null,
      total_cost: null,
    },
  },
} as ManagementCostAttributionResponse;

const emptyExposureResponse = {
  ...portfolioExposureResponse,
  data: {
    ...portfolioExposureResponse.data,
    items: [],
    exposures: [],
  },
  items: [],
  exposures: [],
} as ManagementPortfolioBookExposureResponse;

const emptyPositionsResponse = {
  ...portfolioPositionsResponse,
  data: {
    ...portfolioPositionsResponse.data,
    items: [],
    positions: [],
  },
  items: [],
  positions: [],
} as ManagementPortfolioBookPositionsResponse;

const emptyTradingPulseResponse = {
  ...tradingPulseResponse,
  data: {
    ...tradingPulseResponse.data,
    runtime_rows: [],
    rankings: [],
  },
} as ManagementTradingPulseResponse;

const emptyTradingRankingsResponse = {
  ...tradingRankingsResponse,
  data: {
    ...tradingRankingsResponse.data,
    items: [],
  },
} as ManagementTradingPulseRankingsResponse;

const operationsReadModelFormalResponse = {
  data: {
    identity: {
      persona_id: "persona-alpha",
      persona_label: "Alpha Persona",
      stage: "paper_running",
      runtime_ids: ["runtime-alpha"],
      paper_ledger_ids: ["ledger-alpha"],
      capital_pool_ids: ["pool-growth"],
      sleeve_ids: [],
      strategy_ids: ["strategy-carry"],
      artifact_ids: [],
      broker_ids: ["broker-alpha"],
      period: "quarter",
      as_of: "2026-07-03T12:00:00Z",
    },
    data_confidence: "formal",
    performance: {
      pnl: 124000,
      sharpe: 1.8,
      drawdown_pct: 0.04,
      score: 95,
    },
    sources: [
      { source_name: "performance_attribution", source_status: "ok", source_row_count: 1 },
      { source_name: "portfolio_holdings", source_status: "ok", source_row_count: 2 },
      { source_name: "capital_pools", source_status: "ok", source_row_count: 1 },
      { source_name: "persona_fleet_summary", source_status: "ok", source_row_count: 1 },
    ],
    diagnostics: [],
  },
  meta: { snapshot_at: "2026-07-03T12:00:00Z" },
};

const operationsReadModelFallbackResponse = {
  data: {
    identity: {
      persona_id: "persona-20260528-04688755",
      persona_label: "Crypto-Alt-Hunter",
      stage: "paper_running",
      runtime_ids: ["runtime-crypto"],
      paper_ledger_ids: ["paper-ledger-persona-20260528-04688755"],
      capital_pool_ids: [],
      sleeve_ids: [],
      strategy_ids: [],
      artifact_ids: [],
      broker_ids: [],
      period: "quarter",
      as_of: "2026-07-03T12:00:00Z",
    },
    data_confidence: "fallback",
    performance: {
      pnl: 48000.0,
      sharpe: 1.76,
      drawdown_pct: 0.064,
    },
    sources: [
      { source_name: "performance_attribution", source_status: "unavailable", source_row_count: 0 },
      { source_name: "portfolio_holdings", source_status: "unavailable", source_row_count: 0 },
      { source_name: "capital_pools", source_status: "unavailable", source_row_count: 0 },
      { source_name: "persona_fleet_summary", source_status: "ok", source_row_count: 1 },
    ],
    diagnostics: [
      {
        source_name: "performance_attribution",
        code: "MISSING_ATTRIBUTION_MATCH",
        message: "No performance-attribution row matched persona persona-20260528-04688755 in period quarter.",
      },
      {
        source_name: "portfolio_holdings",
        code: "MISSING_HOLDINGS_MATCH",
        message: "No holdings source returned a matching row for persona persona-20260528-04688755.",
      },
      {
        source_name: "persona_fleet_summary",
        code: "FORMAL_ATTRIBUTION_MISSING_USING_FLEET_FALLBACK",
        message: "Performance is synthesized from the persona-fleet summary because no formal attribution or holdings row matched this persona; treat as fallback, not formal evidence.",
      },
    ],
  },
  meta: { snapshot_at: "2026-07-03T12:00:00Z" },
};

function mockHappyPath() {
  vi.spyOn(managementClient.personaFleet, "list").mockResolvedValue(personaFleetResponse);
  vi.spyOn(managementApi, "fetchManagementPersonaLeague").mockResolvedValue(personaLeagueResponse);
  vi.spyOn(managementApi, "fetchManagementPersonaLeagueRankings").mockResolvedValue(personaLeagueRankingsResponse);
  vi.spyOn(managementApi, "fetchManagementPersonaLeagueMovers").mockResolvedValue(personaMoversResponse);
  vi.spyOn(managementApi, "fetchManagementQuarterlyRanking").mockResolvedValue(quarterlyRankingResponse);
  vi.spyOn(managementApi, "fetchManagementPerformanceAttributionByPersona").mockResolvedValue(personaAttributionResponse);
  vi.spyOn(managementApi, "fetchManagementPerformanceAttributionByPool").mockResolvedValue(poolAttributionResponse);
  vi.spyOn(managementApi, "fetchManagementCostAttribution").mockResolvedValue(costAttributionResponse);
  vi.spyOn(managementApi, "fetchManagementPortfolioBookExposure").mockResolvedValue(portfolioExposureResponse);
  vi.spyOn(managementApi, "fetchManagementPortfolioBookPositions").mockResolvedValue(portfolioPositionsResponse);
  vi.spyOn(managementClient.tradingPulse, "list").mockResolvedValue(tradingPulseResponse);
  vi.spyOn(managementClient.tradingPulse, "rankings").mockResolvedValue(tradingRankingsResponse);
  vi.spyOn(managementApi, "fetchManagementOperationsReadModel").mockImplementation((personaId) => {
    if (personaId === "persona-20260528-04688755") {
      return Promise.resolve(operationsReadModelFallbackResponse as any);
    }
    return Promise.resolve(operationsReadModelFormalResponse as any);
  });
}

function mockEmptyPath() {
  vi.spyOn(managementClient.personaFleet, "list").mockResolvedValue(emptyPersonaFleetResponse);
  vi.spyOn(managementApi, "fetchManagementPersonaLeague").mockResolvedValue(emptyPersonaLeagueResponse);
  vi.spyOn(managementApi, "fetchManagementPersonaLeagueRankings").mockResolvedValue(emptyPersonaLeagueRankingsResponse);
  vi.spyOn(managementApi, "fetchManagementPersonaLeagueMovers").mockResolvedValue(emptyPersonaMoversResponse);
  vi.spyOn(managementApi, "fetchManagementQuarterlyRanking").mockResolvedValue(emptyQuarterlyRankingResponse);
  vi.spyOn(managementApi, "fetchManagementPerformanceAttributionByPersona").mockResolvedValue(emptyAttributionResponse);
  vi.spyOn(managementApi, "fetchManagementPerformanceAttributionByPool").mockResolvedValue(emptyAttributionResponse);
  vi.spyOn(managementApi, "fetchManagementCostAttribution").mockResolvedValue(emptyCostResponse);
  vi.spyOn(managementApi, "fetchManagementPortfolioBookExposure").mockResolvedValue(emptyExposureResponse);
  vi.spyOn(managementApi, "fetchManagementPortfolioBookPositions").mockResolvedValue(emptyPositionsResponse);
  vi.spyOn(managementClient.tradingPulse, "list").mockResolvedValue(emptyTradingPulseResponse);
  vi.spyOn(managementClient.tradingPulse, "rankings").mockResolvedValue(emptyTradingRankingsResponse);
  vi.spyOn(managementApi, "fetchManagementOperationsReadModel").mockResolvedValue({
    data: {
      identity: {
        persona_id: "empty",
        runtime_ids: [],
        paper_ledger_ids: [],
        capital_pool_ids: [],
        sleeve_ids: [],
        strategy_ids: [],
        artifact_ids: [],
        broker_ids: [],
        period: "quarter",
        as_of: "2026-07-03T12:00:00Z",
      },
      data_confidence: "unavailable",
      performance: {},
      sources: [
        { source_name: "performance_attribution", source_status: "unavailable", source_row_count: 0 },
        { source_name: "portfolio_holdings", source_status: "unavailable", source_row_count: 0 },
        { source_name: "capital_pools", source_status: "unavailable", source_row_count: 0 },
        { source_name: "persona_fleet_summary", source_status: "unavailable", source_row_count: 0 },
      ],
      diagnostics: [],
    },
    meta: {},
  } as any);
}

describe("ManagementPerformanceReviewPanel", () => {
  beforeEach(() => {
    mockHappyPath();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("requests bounded review payloads and renders domain-specific summaries", async () => {
    render(<ManagementPerformanceReviewPanel />);

    expect(screen.getByRole("status").textContent).toContain("Loading performance review");

    await waitFor(() => {
      expect(managementClient.personaFleet.list).toHaveBeenCalledWith({
        page_size: PERFORMANCE_REVIEW_LIMITS.personaLeague,
      });
      expect(managementApi.fetchManagementPersonaLeague).toHaveBeenCalledWith({
        page_size: PERFORMANCE_REVIEW_LIMITS.personaLeague,
      });
      expect(managementApi.fetchManagementPersonaLeagueRankings).toHaveBeenCalledWith({
        limit: PERFORMANCE_REVIEW_LIMITS.personaLeague,
      });
    });
    expect(managementApi.fetchManagementPersonaLeagueMovers).toHaveBeenCalledWith({
      limit: PERFORMANCE_REVIEW_LIMITS.personaMovers,
    });
    expect(managementApi.fetchManagementQuarterlyRanking).toHaveBeenCalledWith({
      page_size: PERFORMANCE_REVIEW_LIMITS.quarterlyRanking,
    });
    expect(managementApi.fetchManagementPerformanceAttributionByPersona).toHaveBeenCalledWith({
      period: "quarter",
      page_size: PERFORMANCE_REVIEW_LIMITS.attribution,
      persona_id: undefined,
      runtime_id: undefined,
      source_hint: undefined,
      source_confidence: undefined,
    });
    expect(managementApi.fetchManagementPerformanceAttributionByPool).toHaveBeenCalledWith({
      period: "quarter",
      page_size: PERFORMANCE_REVIEW_LIMITS.attribution,
    });
    expect(managementApi.fetchManagementCostAttribution).toHaveBeenCalledWith({
      period: "quarter",
      page_size: PERFORMANCE_REVIEW_LIMITS.costAttribution,
    });
    expect(managementApi.fetchManagementPortfolioBookExposure).toHaveBeenCalledWith({
      page_size: PERFORMANCE_REVIEW_LIMITS.portfolioBook,
    });
    expect(managementApi.fetchManagementPortfolioBookPositions).toHaveBeenCalledWith({
      page_size: PERFORMANCE_REVIEW_LIMITS.portfolioBook,
    });
    expect(managementClient.tradingPulse.rankings).toHaveBeenCalledWith({
      limit: PERFORMANCE_REVIEW_LIMITS.tradingRankings,
    });

    await screen.findByText("Performance Review");
    expect(screen.getByText("Q3 2026")).toBeTruthy();
    expect(screen.getAllByText("Quarterly Ranking").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Persona Fleet").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Performance Attribution").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Portfolio Book").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Cost Attribution").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Trading Pulse").length).toBeGreaterThan(0);

    const summary = screen.getByTestId("performance-review-summary");
    expect(within(summary).getAllByText("$124,000").length).toBeGreaterThan(0);
    expect(within(summary).getByText("Portfolio Book")).toBeTruthy();
    expect(within(summary).getByText("Cost And Trading")).toBeTruthy();

    const rankingTable = screen.getByTestId("performance-review-table-quarterly-ranking");
    expect(rankingTable.getAttribute("data-management-dense-table")).toBe("true");
    expect(within(rankingTable).getByText("Alpha Persona")).toBeTruthy();
    expect(within(rankingTable).getByText("Hotel Persona")).toBeTruthy();
    expect(within(rankingTable).queryByText("Overflow Persona")).toBeNull();

    expect(within(screen.getByTestId("performance-review-table-persona-fleet")).getByText("runtime-alpha")).toBeTruthy();
    expect(within(screen.getByTestId("performance-review-table-persona-attribution")).getByText("Alpha Persona")).toBeTruthy();
    expect(within(screen.getByTestId("performance-review-table-pool-attribution")).getByText("Growth Pool")).toBeTruthy();
    expect(within(screen.getByTestId("performance-review-table-cost-attribution")).getByText("Carry Strategy")).toBeTruthy();
    expect(within(screen.getByTestId("performance-review-table-portfolio-positions")).getByText("SPY")).toBeTruthy();
    expect(within(screen.getByTestId("performance-review-table-portfolio-exposure")).getByText("near limit")).toBeTruthy();
    expect(within(screen.getByTestId("performance-review-table-trading-pulse")).getByText("runtime-alpha")).toBeTruthy();
    expect(screen.getByTestId("performance-review-trading-rankings").textContent).toContain("Top PnL");
  });

  it("keeps partial review data visible when one source fails", async () => {
    vi.spyOn(managementApi, "fetchManagementCostAttribution").mockRejectedValue(new Error("cost service offline"));

    render(<ManagementPerformanceReviewPanel />);

    await screen.findByTestId("performance-review-degraded");
    expect(screen.getByTestId("performance-review-degraded").textContent).toContain("Cost attribution");
    expect(screen.getByTestId("performance-review-degraded").textContent).toContain("cost service offline");
    expect(screen.getAllByText("Quarterly Ranking").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Portfolio Book").length).toBeGreaterThan(0);
    expect(screen.queryByText("Performance review unavailable")).toBeNull();
  });

  it("opens Persona Fleet performance links as fallback diagnostics when formal attribution rows are absent", async () => {
    vi.spyOn(managementApi, "fetchManagementPerformanceAttributionByPersona").mockResolvedValue(emptyAttributionResponse);
    vi.spyOn(managementApi, "fetchManagementPerformanceAttributionByPool").mockResolvedValue(emptyAttributionResponse);

    render(<ManagementPerformanceReviewPanel />);

    const fleetTable = await screen.findByTestId("performance-review-table-persona-fleet");
    const link = within(fleetTable).getByTestId("persona-fleet-performance-link-persona-alpha") as HTMLAnchorElement;

    expect(link.getAttribute("href")).toBe(
      "/management/performance-attribution?dimension=persona&period=quarter&persona_id=persona-alpha&runtime_id=runtime-alpha&source_hint=bff_composed_slim_list&source_confidence=fallback&mode=fallback-diagnostic",
    );
    expect(within(fleetTable).getAllByText("Fallback source").length).toBeGreaterThan(0);
    expect(within(fleetTable).queryByText("Formal source")).toBeNull();
  });

  it("suppresses nan and undefined values in operator-facing metric cells", async () => {
    vi.spyOn(managementClient.personaFleet, "list").mockResolvedValue({
      ...personaFleetResponse,
      data: {
        ...personaFleetResponse.data,
        items: [
          {
            ...personaFleetResponse.data.items[0],
            runtime_id: "undefined",
            performance_summary: {
              totalPnl: Number.NaN,
              violationCount: "nan",
            },
          },
        ],
      },
    } as PersonaFleetAggregate);

    const { container } = render(<ManagementPerformanceReviewPanel />);

    await screen.findByTestId("performance-review-table-persona-fleet");
    expect(container.textContent?.toLowerCase()).not.toContain("nan");
    expect(container.textContent?.toLowerCase()).not.toContain("undefined");
  });

  it("shows an empty state when all bounded lists return no rows", async () => {
    vi.restoreAllMocks();
    mockEmptyPath();

    render(<ManagementPerformanceReviewPanel />);

    await screen.findByText("No performance review data");
    expect(screen.queryByTestId("performance-review-summary")).toBeNull();
  });

  it("shows an error state when every review source fails", async () => {
    vi.restoreAllMocks();
    const failure = new Error("bff offline");
    vi.spyOn(managementClient.personaFleet, "list").mockRejectedValue(failure);
    vi.spyOn(managementApi, "fetchManagementPersonaLeague").mockRejectedValue(failure);
    vi.spyOn(managementApi, "fetchManagementPersonaLeagueRankings").mockRejectedValue(failure);
    vi.spyOn(managementApi, "fetchManagementPersonaLeagueMovers").mockRejectedValue(failure);
    vi.spyOn(managementApi, "fetchManagementQuarterlyRanking").mockRejectedValue(failure);
    vi.spyOn(managementApi, "fetchManagementPerformanceAttributionByPersona").mockRejectedValue(failure);
    vi.spyOn(managementApi, "fetchManagementPerformanceAttributionByPool").mockRejectedValue(failure);
    vi.spyOn(managementApi, "fetchManagementCostAttribution").mockRejectedValue(failure);
    vi.spyOn(managementApi, "fetchManagementPortfolioBookExposure").mockRejectedValue(failure);
    vi.spyOn(managementApi, "fetchManagementPortfolioBookPositions").mockRejectedValue(failure);
    vi.spyOn(managementClient.tradingPulse, "list").mockRejectedValue(failure);
    vi.spyOn(managementClient.tradingPulse, "rankings").mockRejectedValue(failure);

    render(<ManagementPerformanceReviewPanel />);

    await screen.findByText("Performance review unavailable");
    expect(screen.getByText("Every performance review source failed.")).toBeTruthy();
  });

  it("renders fallback attribution drilldown with confidence banner, source statuses, and actionable diagnostics for focus persona persona-20260528-04688755 when formal attribution is absent", async () => {
    const originalLocation = window.location;
    delete (window as any).location;
    window.location = {
      ...originalLocation,
      search: "?dimension=persona&period=quarter&persona_id=persona-20260528-04688755&runtime_id=runtime-crypto&source_confidence=fallback",
    } as any;

    try {
      render(<ManagementPerformanceReviewPanel />);

      await screen.findByText("Focus persona-20260528-04688755");

      const banner = await screen.findByTestId("attribution-confidence-banner");
      expect(banner.textContent).toContain("FALLBACK");
      expect(banner.textContent).toContain("Do not treat as formal evidence");

      const metaPanel = screen.getByTestId("attribution-metadata-panel");
      expect(metaPanel.textContent).toContain("persona-20260528-04688755");
      expect(metaPanel.textContent).toContain("runtime-crypto");

      const fallbackRow = screen.getByTestId("fallback-attribution-row");
      expect(fallbackRow.textContent).toContain("Fleet Fallback");
      expect(fallbackRow.textContent).toContain("$48,000");

      const coveragePanel = screen.getByTestId("attribution-source-coverage-panel");
      expect(coveragePanel.textContent).toContain("performance attribution");
      expect(coveragePanel.textContent).toContain("unavailable");

      const diagnosticsPanel = screen.getByTestId("attribution-diagnostics-panel");
      expect(diagnosticsPanel.textContent).toContain("MISSING_ATTRIBUTION_MATCH");
      expect(diagnosticsPanel.textContent).toContain("MISSING_HOLDINGS_MATCH");

      expect(screen.getByTestId("actionable-missing-holdings")).toBeTruthy();
      expect(screen.getByTestId("actionable-missing-holdings").textContent).toContain("Action Required");
    } finally {
      window.location = originalLocation;
    }
  });
});
