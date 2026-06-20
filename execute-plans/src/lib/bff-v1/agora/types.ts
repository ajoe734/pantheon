/* eslint-disable */
/**
 * GENERATED FILE - DO NOT EDIT BY HAND.
 *
 * Source: Pantheon AG-XR-001 Agora v1 OpenAPI/schema bundle.
 * Regenerate with: node scripts/generate-agora-types.mjs
 */

export const AGORA_V1_CONTRACT_SNAPSHOT = {
  "capability_count": 7,
  "contract_name": "pantheon-agora-v1",
  "contract_version": "1.0",
  "files": {
    "openapi/agora_v1.openapi.yaml": "4da5ea91923e40c13a9118ee4f784a5d6627e6cb91e4d4712d8fac244912118f",
    "specs/agora/agora_user_scope.schema.json": "022e147c94cf2b11cde6dc3af49d04ba8a776c50eed857931b12f12aabd02f80",
    "specs/agora/candidate_pool.schema.json": "679077c2f567502883f0b7735332645df30cccd6752a978379c5eeecd058e3b9",
    "specs/agora/capability_manifest.json": "5988cac6d8ca38fc0c51922086c1cc2564b1bb31b2b36ee276e6d363249e9e3e",
    "specs/agora/dashboard_recipe.schema.json": "5b9c33653eb8c85b001b5f6f6a802e83e58276a25f6c00e0a030a7094c78a8f6",
    "specs/agora/personalization_event.schema.json": "df7991d19943650a61d58a065182fe4bda20f967b4098a36f36b1ab4b33bcb21",
    "specs/agora/research_plan.schema.json": "55010d81198a4eef6934cde9392e19d79d014bf3f7ef6d31901b37627bd040d4",
    "specs/agora/research_run_summary.schema.json": "669530ad2aed09133e84344aec7768e375c9cc505728b136d76041366184328b",
    "specs/agora/servant_profile.schema.json": "004c5ebddf25622602f175f9ee10bbba714adf7c24050ecc6101c1bb6cffa0b9",
    "specs/agora/shadow_decision.schema.json": "1414c77ddebb5102b81a10fb57db32520c607daba76cb3736f7eb20eb751b0b2",
    "specs/agora/strategy_completeness.schema.json": "13a8c0e28a6434b93b221a4d33f8022f87ee6ca4df99627728800117474ce2fa",
    "specs/agora/strategy_workshop.schema.json": "d8d1662790f35d61fdaff0580ca488011dd90f4b4007d78e9fb77a5065396aec",
    "specs/agora/trading_event.schema.json": "14bd9e788c855c30a1803a767e66a38efc6f7505bfddf59d3095499d113f02f4",
    "specs/agora/trading_intent.schema.json": "588a0508e6b00206be1361e6337b440b7710df0075d6272adb4e85040be4f7ad",
    "specs/agora/widget_spec.schema.json": "0749275943dc155afa08dbb8736c336d613daf18b99b42f6c10aec15d2eabedb"
  },
  "frozen_by": "AG-XR-001",
  "operation_count": 61,
  "schema_count": 13,
  "source_bundle": "services/control-plane/specs/agora/bundle_index.json"
} as const;

export type AgoraV1ContractFile = keyof typeof AGORA_V1_CONTRACT_SNAPSHOT.files;

export const AGORA_V1_CAPABILITIES = [
  {
  "auth_level": "operator",
  "bff_path_prefixes": [
    "/bff/agora/sessions",
    "/bff/agora/ask"
  ],
  "bff_route_families": [
    "agora-core"
  ],
  "name": "agora.identity.v1",
  "schemas": [
    "agora_user_scope.schema.json",
    "servant_profile.schema.json"
  ]
},
  {
  "auth_level": "operator",
  "bff_path_prefixes": [
    "/bff/agora/ask/sessions",
    "/bff/agora/sessions",
    "/bff/agora/inbox",
    "/bff/agora/incoming",
    "/bff/agora/messages",
    "/bff/agora/handoffs",
    "/bff/channels"
  ],
  "bff_route_families": [
    "agora-core",
    "agora-extended"
  ],
  "name": "agora.session.v1",
  "schemas": []
},
  {
  "auth_level": "operator",
  "bff_path_prefixes": [
    "/bff/agora/evaluation-runs",
    "/bff/agora/evaluation-suites",
    "/bff/agora/training-examples",
    "/bff/agora/skill-coaching",
    "/bff/agora/committee",
    "/bff/agora/committee-sessions",
    "/bff/agora/persona-lab"
  ],
  "bff_route_families": [
    "agora-core",
    "agora-extended"
  ],
  "name": "agora.workshop.v1",
  "schemas": [
    "strategy_workshop.schema.json",
    "strategy_completeness.schema.json"
  ]
},
  {
  "auth_level": "operator",
  "bff_path_prefixes": [
    "/bff/agora/research-tasks",
    "/bff/research/tasks"
  ],
  "bff_route_families": [
    "agora-core"
  ],
  "name": "agora.research.v1",
  "schemas": [
    "research_plan.schema.json",
    "research_run_summary.schema.json",
    "candidate_pool.schema.json"
  ]
},
  {
  "auth_level": "operator",
  "bff_path_prefixes": [
    "/bff/agora/signals",
    "/bff/agora/feedback"
  ],
  "bff_route_families": [
    "agora-core"
  ],
  "name": "agora.trading.v1",
  "schemas": [
    "trading_event.schema.json",
    "trading_intent.schema.json",
    "shadow_decision.schema.json"
  ]
},
  {
  "auth_level": "operator",
  "bff_path_prefixes": [
    "/bff/agora/daily",
    "/bff/agora/markets",
    "/bff/agora/market-notes",
    "/bff/agora/watchlist",
    "/bff/agora/postmortems",
    "/bff/agora/alerts",
    "/bff/agora/journal",
    "/bff/agora/notes",
    "/bff/agora/decision-journal"
  ],
  "bff_route_families": [
    "agora-core"
  ],
  "name": "agora.dashboard.v1",
  "schemas": [
    "dashboard_recipe.schema.json",
    "widget_spec.schema.json"
  ]
},
  {
  "auth_level": "operator",
  "bff_path_prefixes": [
    "/bff/agora/memory",
    "/bff/agora/insights",
    "/bff/insights",
    "/bff/memory"
  ],
  "bff_route_families": [
    "agora-core"
  ],
  "name": "agora.personalization.v1",
  "schemas": [
    "personalization_event.schema.json"
  ]
},
] as const;

export type AgoraCapability = typeof AGORA_V1_CAPABILITIES[number];
export type AgoraCapabilityName = "agora.identity.v1" | "agora.session.v1" | "agora.workshop.v1" | "agora.research.v1" | "agora.trading.v1" | "agora.dashboard.v1" | "agora.personalization.v1";

export const AGORA_V1_OPERATIONS = [
  {
  "method": "GET",
  "operationId": "listAgoraSessions",
  "path": "/bff/agora/sessions",
  "tags": [
    "agora-identity"
  ]
},
  {
  "method": "POST",
  "operationId": "createAgoraSession",
  "path": "/bff/agora/sessions",
  "tags": [
    "agora-identity"
  ]
},
  {
  "method": "GET",
  "operationId": "getAgoraSession",
  "path": "/bff/agora/sessions/{session_id}",
  "tags": [
    "agora-identity"
  ]
},
  {
  "method": "GET",
  "operationId": "listSessionMessages",
  "path": "/bff/agora/sessions/{session_id}/messages",
  "tags": [
    "agora-session"
  ]
},
  {
  "method": "POST",
  "operationId": "postSessionMessage",
  "path": "/bff/agora/sessions/{session_id}/messages",
  "tags": [
    "agora-session"
  ]
},
  {
  "method": "POST",
  "operationId": "submitAskPersonaQuery",
  "path": "/bff/agora/ask",
  "tags": [
    "agora-session"
  ]
},
  {
  "method": "GET",
  "operationId": "listAskSessions",
  "path": "/bff/agora/ask/sessions",
  "tags": [
    "agora-session"
  ]
},
  {
  "method": "POST",
  "operationId": "createAskSession",
  "path": "/bff/agora/ask/sessions",
  "tags": [
    "agora-session"
  ]
},
  {
  "method": "GET",
  "operationId": "getAskSession",
  "path": "/bff/agora/ask/sessions/{session_id}",
  "tags": [
    "agora-session"
  ]
},
  {
  "method": "POST",
  "operationId": "closeAskSession",
  "path": "/bff/agora/ask/sessions/{session_id}/close",
  "tags": [
    "agora-session"
  ]
},
  {
  "method": "POST",
  "operationId": "messageAction",
  "path": "/bff/agora/messages/{message_id}/actions/{action}",
  "tags": [
    "agora-session"
  ]
},
  {
  "method": "GET",
  "operationId": "getAgoraInbox",
  "path": "/bff/agora/inbox",
  "tags": [
    "agora-session"
  ]
},
  {
  "method": "GET",
  "operationId": "getAgoraIncoming",
  "path": "/bff/agora/incoming",
  "tags": [
    "agora-session"
  ]
},
  {
  "method": "GET",
  "operationId": "listAgoraHandoffs",
  "path": "/bff/agora/handoffs",
  "tags": [
    "agora-session"
  ]
},
  {
  "method": "GET",
  "operationId": "listChannels",
  "path": "/bff/channels",
  "tags": [
    "agora-session"
  ]
},
  {
  "method": "GET",
  "operationId": "getChannel",
  "path": "/bff/channels/{channel_id}",
  "tags": [
    "agora-session"
  ]
},
  {
  "method": "GET",
  "operationId": "listEvaluationRuns",
  "path": "/bff/agora/evaluation-runs",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "GET",
  "operationId": "listEvaluationSuites",
  "path": "/bff/agora/evaluation-suites",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "GET",
  "operationId": "listTrainingExamples",
  "path": "/bff/agora/training-examples",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "POST",
  "operationId": "submitTrainingExample",
  "path": "/bff/agora/training-examples",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "GET",
  "operationId": "listSkillCoachingSessions",
  "path": "/bff/agora/skill-coaching/sessions",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "GET",
  "operationId": "listCommitteeSessionsAlias",
  "path": "/bff/agora/committee-sessions",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "GET",
  "operationId": "listCommitteeSessions",
  "path": "/bff/agora/committee/sessions",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "POST",
  "operationId": "createCommitteeSession",
  "path": "/bff/agora/committee/sessions",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "GET",
  "operationId": "getCommitteeSession",
  "path": "/bff/agora/committee/sessions/{committee_session_id}",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "POST",
  "operationId": "openCommitteeSession",
  "path": "/bff/agora/committee/sessions/{committee_session_id}/open",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "POST",
  "operationId": "closeCommitteeSession",
  "path": "/bff/agora/committee/sessions/{committee_session_id}/close",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "GET",
  "operationId": "listCommitteeMemos",
  "path": "/bff/agora/committee/sessions/{committee_session_id}/memos",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "POST",
  "operationId": "createCommitteeMemo",
  "path": "/bff/agora/committee/sessions/{committee_session_id}/memos",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "GET",
  "operationId": "getCommitteeMemo",
  "path": "/bff/agora/committee/sessions/{committee_session_id}/memos/{memo_id}",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "POST",
  "operationId": "publishCommitteeMemo",
  "path": "/bff/agora/committee/sessions/{committee_session_id}/memos/{memo_id}/publish",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "POST",
  "operationId": "createEvidencePack",
  "path": "/bff/agora/committee/{committee_session_id}/evidence-pack",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "POST",
  "operationId": "uploadEvidenceFile",
  "path": "/bff/agora/committee/{committee_session_id}/evidence-pack/files",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "GET",
  "operationId": "listPersonaLabRuns",
  "path": "/bff/agora/persona-lab/runs",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "POST",
  "operationId": "submitPersonaLabCommit",
  "path": "/bff/agora/persona-lab/{run_id}/actions/submit-commit",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "GET",
  "operationId": "listAgoraResearchTasks",
  "path": "/bff/agora/research-tasks",
  "tags": [
    "agora-research"
  ]
},
  {
  "method": "GET",
  "operationId": "listResearchTasksAlias",
  "path": "/bff/research/tasks",
  "tags": [
    "agora-research"
  ]
},
  {
  "method": "GET",
  "operationId": "listSignals",
  "path": "/bff/agora/signals",
  "tags": [
    "agora-trading"
  ]
},
  {
  "method": "POST",
  "operationId": "createSignalObservation",
  "path": "/bff/agora/signals",
  "tags": [
    "agora-trading"
  ]
},
  {
  "method": "GET",
  "operationId": "getSignal",
  "path": "/bff/agora/signals/{signal_id}",
  "tags": [
    "agora-trading"
  ]
},
  {
  "method": "POST",
  "operationId": "submitSignalFeedback",
  "path": "/bff/agora/signals/{signal_id}/feedback",
  "tags": [
    "agora-trading"
  ]
},
  {
  "method": "POST",
  "operationId": "submitGeneralFeedback",
  "path": "/bff/agora/feedback",
  "tags": [
    "agora-trading"
  ]
},
  {
  "method": "GET",
  "operationId": "getDailyBriefing",
  "path": "/bff/agora/daily",
  "tags": [
    "agora-dashboard"
  ]
},
  {
  "method": "GET",
  "operationId": "getMarketContext",
  "path": "/bff/agora/markets",
  "tags": [
    "agora-dashboard"
  ]
},
  {
  "method": "GET",
  "operationId": "getMarketNotes",
  "path": "/bff/agora/market-notes",
  "tags": [
    "agora-dashboard"
  ]
},
  {
  "method": "GET",
  "operationId": "getWatchlist",
  "path": "/bff/agora/watchlist",
  "tags": [
    "agora-dashboard"
  ]
},
  {
  "method": "GET",
  "operationId": "listAgoraPostmortems",
  "path": "/bff/agora/postmortems",
  "tags": [
    "agora-dashboard"
  ]
},
  {
  "method": "GET",
  "operationId": "getAlertsTriage",
  "path": "/bff/agora/alerts/triage",
  "tags": [
    "agora-dashboard"
  ]
},
  {
  "method": "GET",
  "operationId": "getDecisionJournal",
  "path": "/bff/agora/decision-journal",
  "tags": [
    "agora-dashboard"
  ]
},
  {
  "method": "GET",
  "operationId": "listJournalEntries",
  "path": "/bff/agora/journal",
  "tags": [
    "agora-dashboard"
  ]
},
  {
  "method": "POST",
  "operationId": "createJournalEntry",
  "path": "/bff/agora/journal",
  "tags": [
    "agora-dashboard"
  ]
},
  {
  "method": "PATCH",
  "operationId": "updateJournalEntry",
  "path": "/bff/agora/journal/{entry_id}",
  "tags": [
    "agora-dashboard"
  ]
},
  {
  "method": "GET",
  "operationId": "listNotes",
  "path": "/bff/agora/notes",
  "tags": [
    "agora-dashboard"
  ]
},
  {
  "method": "POST",
  "operationId": "createNote",
  "path": "/bff/agora/notes",
  "tags": [
    "agora-dashboard"
  ]
},
  {
  "method": "GET",
  "operationId": "getPersonaMemory",
  "path": "/bff/agora/memory",
  "tags": [
    "agora-personalization"
  ]
},
  {
  "method": "POST",
  "operationId": "memoryAction",
  "path": "/bff/agora/memory/{memory_id}/actions/{action}",
  "tags": [
    "agora-personalization"
  ]
},
  {
  "method": "POST",
  "operationId": "quarantineMemoryEntry",
  "path": "/bff/memory/{memory_id}/actions/quarantine",
  "tags": [
    "agora-personalization"
  ]
},
  {
  "method": "GET",
  "operationId": "listInsights",
  "path": "/bff/agora/insights",
  "tags": [
    "agora-personalization"
  ]
},
  {
  "method": "POST",
  "operationId": "createInsight",
  "path": "/bff/agora/insights",
  "tags": [
    "agora-personalization"
  ]
},
  {
  "method": "POST",
  "operationId": "insightAction",
  "path": "/bff/agora/insights/{insight_id}/actions/{action}",
  "tags": [
    "agora-personalization"
  ]
},
  {
  "method": "POST",
  "operationId": "attachStrategyToInsight",
  "path": "/bff/insights/{insight_id}/actions/attach-strategy",
  "tags": [
    "agora-personalization"
  ]
},
] as const;

export type AgoraRoute = typeof AGORA_V1_OPERATIONS[number];
export type AgoraHttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
export type AgoraOperationId = typeof AGORA_V1_OPERATIONS[number]["operationId"];

export interface AgoraUserScope {
  spec_version: "1.0";
  scope_id: string;
  operator_id: string;
  granted_capabilities: Array<string>;
  denied_capabilities?: Array<string>;
  surfaces?: Array<"management" | "agora" | "developer">;
  persona_ids?: Array<string>;
  created_at: string;
  expires_at?: string;
  policy_refs?: Array<string>;
  metadata?: Record<string, unknown>;
}

export interface ServantProfile {
  spec_version: "1.0";
  persona_id: string;
  display_name: string;
  status: "active" | "suspended" | "paper_only" | "shadow_only" | "retired";
  capability_summary: {
    can_ask: boolean;
    can_research: boolean;
    can_workshop: boolean;
    can_shadow?: boolean;
    asset_classes?: Array<string>;
    strategy_families?: Array<string>;
  };
  description?: string;
  avatar_ref?: string;
  last_active_at?: string;
  metadata?: Record<string, unknown>;
}

export interface StrategyWorkshop {
  spec_version: "1.0";
  workshop_id: string;
  operator_id: string;
  status: "open" | "in_review" | "concluded" | "archived";
  subject: {
    kind: "strategy_spec" | "research_plan" | "candidate_artifact" | "free_form";
    ref: string;
    title?: string;
  };
  participant_persona_ids?: Array<string>;
  completeness_ref?: string;
  research_plan_refs?: Array<string>;
  message_count?: number;
  created_at: string;
  concluded_at?: string;
  metadata?: Record<string, unknown>;
}

export interface StrategyCompleteness {
  spec_version: "1.0";
  completeness_id: string;
  strategy_ref: string;
  workshop_id?: string;
  assessed_by_persona_id: string;
  overall_grade: "complete" | "mostly_complete" | "partial" | "incomplete";
  dimensions: Array<{
    dimension: "hypothesis" | "data_dependencies" | "market_scope" | "evaluation_plan" | "risk_constraints" | "execution_profile" | "governance";
    grade: "complete" | "partial" | "missing";
    gaps?: Array<string>;
    required_actions?: Array<string>;
  }>;
  blockers?: Array<string>;
  research_ready?: boolean;
  assessed_at: string;
  metadata?: Record<string, unknown>;
}

export interface ResearchPlan {
  spec_version: "1.0";
  plan_id: string;
  strategy_ref: string;
  workshop_id?: string;
  completeness_id?: string;
  emitted_by_persona_id: string;
  status: "draft" | "approved" | "running" | "completed" | "cancelled";
  objectives: Array<string>;
  data_requirements: Array<{
    ref: string;
    kind: "dataset" | "feature_set" | "market_data" | "alternative_data" | "internal_signal";
    description?: string;
  }>;
  evaluation_criteria: {
    primary_metric: string;
    min_sharpe: number;
    max_drawdown: number;
    additional_metrics?: Array<string>;
    oos_period?: string;
  };
  execution_constraints?: {
    max_runtime_hours?: number;
    compute_tier?: "light" | "standard" | "heavy";
    environments?: Array<"research" | "paper">;
  };
  run_ids?: Array<string>;
  created_at: string;
  approved_at?: string;
  metadata?: Record<string, unknown>;
}

export interface ResearchRunSummary {
  spec_version: "1.0";
  run_id: string;
  plan_id: string;
  strategy_ref: string;
  status: "running" | "completed" | "failed" | "cancelled";
  outcome: "pass" | "fail" | "inconclusive" | "pending";
  metrics_summary?: {
    sharpe?: number;
    max_drawdown?: number;
    total_return?: number;
    win_rate?: number;
    trade_count?: number;
    [key: string]: unknown;
  };
  candidate_artifact_ref?: string;
  failure_reason?: string;
  environment?: "research" | "paper";
  started_at?: string;
  completed_at: string;
  metadata?: Record<string, unknown>;
}

export interface CandidatePool {
  spec_version: "1.0";
  pool_id: string;
  operator_id: string;
  filter?: {
    asset_classes?: Array<string>;
    strategy_families?: Array<string>;
    lifecycle_states?: Array<"candidate" | "review" | "approved">;
    persona_ids?: Array<string>;
  };
  candidates: Array<{
    artifact_id: string;
    strategy_ref: string;
    title?: string;
    lifecycle_state: "candidate" | "review" | "approved" | "rejected";
    producing_persona_id?: string;
    sharpe_summary?: number;
    run_ref?: string;
    created_at: string;
  }>;
  total?: number;
  snapshot_at: string;
  metadata?: Record<string, unknown>;
}

export interface DashboardRecipe {
  spec_version: "1.0";
  recipe_id: string;
  operator_id: string;
  name: string;
  surface: "agora_main" | "agora_research" | "agora_trading" | "agora_workshop";
  is_default?: boolean;
  widgets: Array<{
    widget_id: string;
    position: {
      row: number;
      col: number;
      row_span?: number;
      col_span?: number;
    };
    override_config?: Record<string, unknown>;
  }>;
  version?: number;
  created_at: string;
  updated_at?: string;
  metadata?: Record<string, unknown>;
}

export interface WidgetSpec {
  spec_version: "1.0";
  widget_id: string;
  widget_type: "market_summary" | "signal_list" | "candidate_pool" | "research_task_list" | "persona_status" | "watchlist" | "insight_feed" | "alert_triage" | "session_history" | "decision_journal" | "postmortem_feed" | "custom";
  title?: string;
  data_source: {
    bff_path: string;
    query_params?: Record<string, string>;
    sse_channel?: string;
    refresh_interval_seconds?: number;
  };
  display_options?: {
    max_items?: number;
    show_sparkline?: boolean;
    compact_mode?: boolean;
    theme_variant?: "default" | "dark" | "compact" | "expanded";
    [key: string]: unknown;
  };
  persona_filter?: Array<string>;
  created_at: string;
  updated_at?: string;
  metadata?: Record<string, unknown>;
}

export interface TradingEvent {
  spec_version: "1.0";
  event_id: string;
  operator_id: string;
  persona_id?: string;
  session_id?: string;
  event_type: "signal_observed" | "price_alert_triggered" | "position_threshold_crossed" | "market_condition_noted" | "news_event_captured" | "trader_decision_noted" | "pattern_flagged";
  subject?: {
    symbol?: string;
    asset_class?: string;
    venue?: string;
    strategy_ref?: string;
    [key: string]: unknown;
  };
  event_data?: Record<string, unknown>;
  signal_ref?: string;
  confidence?: number;
  learning_eligible?: boolean;
  no_order_route_proof: "agora_observation_only" | "research_plane_only";
  observed_at: string;
  metadata?: Record<string, unknown>;
}

export interface TradingIntent {
  spec_version: "1.0";
  intent_id: string;
  operator_id: string;
  persona_id?: string;
  session_id?: string;
  intent_type: "buy_interest" | "sell_interest" | "hold_decision" | "reduce_exposure" | "increase_exposure" | "hedge_intent" | "exit_intent" | "entry_interest";
  direction: "long" | "short" | "neutral" | "reduce" | "exit";
  subject: {
    symbol: string;
    asset_class?: string;
    venue?: string;
    strategy_ref?: string;
  };
  rationale?: string;
  size_hint?: "small" | "medium" | "large" | "full_position";
  timeframe_hint?: string;
  confidence?: number;
  linked_event_ids?: Array<string>;
  learning_eligible?: boolean;
  no_order_route_proof: "agora_intent_record_only";
  expressed_at: string;
  metadata?: Record<string, unknown>;
}

export interface ShadowDecision {
  spec_version: "1.0";
  decision_id: string;
  shadow_persona_id: string;
  reference_operator_id: string;
  reference_intent_id?: string;
  session_id?: string;
  market_context_ref: string;
  decision: {
    action: "buy" | "sell" | "hold" | "reduce" | "hedge" | "exit" | "no_action";
    subject: {
      symbol: string;
      asset_class?: string;
      venue?: string;
    };
    direction?: "long" | "short" | "neutral" | "reduce" | "exit";
    size_hint?: "small" | "medium" | "large" | "full_position";
    rationale?: string;
    confidence?: number;
  };
  agreement_with_human?: "agree" | "partial_agree" | "disagree" | "not_compared";
  divergence_rationale?: string;
  evaluation_context?: "paper" | "research";
  imitation_dataset_eligible?: boolean;
  no_order_route_proof: "shadow_learn_only";
  decided_at: string;
  metadata?: Record<string, unknown>;
}

export interface PersonalizationEvent {
  spec_version: "1.0";
  event_id: string;
  operator_id: string;
  persona_id?: string;
  session_id?: string;
  event_type: "dashboard_recipe_changed" | "widget_added" | "widget_removed" | "widget_reordered" | "persona_preference_set" | "watchlist_updated" | "alert_threshold_set" | "display_mode_changed" | "memory_endorsed" | "memory_quarantined" | "insight_acted_on" | "notification_preference_set";
  target?: {
    target_type?: "dashboard_recipe" | "widget" | "persona" | "watchlist" | "alert" | "memory_entry" | "insight" | "notification_rule";
    target_id?: string;
  };
  before_state?: Record<string, unknown>;
  after_state?: Record<string, unknown>;
  source?: "operator_action" | "persona_suggestion" | "system_default";
  memory_writeback_eligible?: boolean;
  occurred_at: string;
  metadata?: Record<string, unknown>;
}

export interface AgoraSchemaMap {
  AgoraUserScope: AgoraUserScope;
  ServantProfile: ServantProfile;
  StrategyWorkshop: StrategyWorkshop;
  StrategyCompleteness: StrategyCompleteness;
  ResearchPlan: ResearchPlan;
  ResearchRunSummary: ResearchRunSummary;
  CandidatePool: CandidatePool;
  DashboardRecipe: DashboardRecipe;
  WidgetSpec: WidgetSpec;
  TradingEvent: TradingEvent;
  TradingIntent: TradingIntent;
  ShadowDecision: ShadowDecision;
  PersonalizationEvent: PersonalizationEvent;
}

export type AgoraSchemaName = "AgoraUserScope" | "ServantProfile" | "StrategyWorkshop" | "StrategyCompleteness" | "ResearchPlan" | "ResearchRunSummary" | "CandidatePool" | "DashboardRecipe" | "WidgetSpec" | "TradingEvent" | "TradingIntent" | "ShadowDecision" | "PersonalizationEvent";
export type AgoraSchema = AgoraSchemaMap[keyof AgoraSchemaMap];
