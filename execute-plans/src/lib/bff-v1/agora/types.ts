/* eslint-disable */
/**
 * GENERATED FILE - DO NOT EDIT BY HAND.
 *
 * Source: Pantheon Agora OpenAPI/schema bundle.
 * Regenerate with: node scripts/generate-agora-types.mjs
 */

export const AGORA_V1_CONTRACT_SNAPSHOT = {
  "capability_count": 9,
  "contract_name": "pantheon-agora-v1",
  "contract_version": "1.1",
  "extends": {
    "bundle_index_sha256": "286891c6bb900d6b5e9f9037d357c2016f8ecac33927056556a848f95fb4bd0b",
    "bundle_path": "services/control-plane/specs/agora/bundle_index.json",
    "bundle_version": "1.0",
    "frozen_by": "AG-XR-001"
  },
  "files": {
    "openapi/agora_v1.openapi.yaml": "4da5ea91923e40c13a9118ee4f784a5d6627e6cb91e4d4712d8fac244912118f",
    "specs/agora/agora_user_scope.schema.json": "ae660aa7719ded37ca8b41bfc6ac287d1eae0bb85a8389b08d069c528a934dee",
    "specs/agora/candidate_pool.schema.json": "679077c2f567502883f0b7735332645df30cccd6752a978379c5eeecd058e3b9",
    "specs/agora/capability_manifest.json": "5988cac6d8ca38fc0c51922086c1cc2564b1bb31b2b36ee276e6d363249e9e3e",
    "specs/agora/dashboard_recipe.schema.json": "5b9c33653eb8c85b001b5f6f6a802e83e58276a25f6c00e0a030a7094c78a8f6",
    "specs/agora/personalization_event.schema.json": "df7991d19943650a61d58a065182fe4bda20f967b4098a36f36b1ab4b33bcb21",
    "specs/agora/research_plan.schema.json": "55010d81198a4eef6934cde9392e19d79d014bf3f7ef6d31901b37627bd040d4",
    "specs/agora/research_run_summary.schema.json": "669530ad2aed09133e84344aec7768e375c9cc505728b136d76041366184328b",
    "specs/agora/servant_profile.schema.json": "71b114fa03fe0d54f72c785855760482d5c1ea5500a039a7c0593c490d58f930",
    "specs/agora/shadow_decision.schema.json": "1414c77ddebb5102b81a10fb57db32520c607daba76cb3736f7eb20eb751b0b2",
    "specs/agora/strategy_completeness.schema.json": "13a8c0e28a6434b93b221a4d33f8022f87ee6ca4df99627728800117474ce2fa",
    "specs/agora/strategy_workshop.schema.json": "d8d1662790f35d61fdaff0580ca488011dd90f4b4007d78e9fb77a5065396aec",
    "specs/agora/trading_event.schema.json": "14bd9e788c855c30a1803a767e66a38efc6f7505bfddf59d3095499d113f02f4",
    "specs/agora/trading_intent.schema.json": "588a0508e6b00206be1361e6337b440b7710df0075d6272adb4e85040be4f7ad",
    "specs/agora/v2/capability_manifest_v1_1.json": "6a729d1284ca8f88058a4c301dc67a4c17fd76097190bf020310f4f2cab3db41",
    "specs/agora/v2/chart_spec_v1.schema.json": "0bcd0fa5fc21d7c021d54803780e310cfd9234b3ea15c044fa0b5cdfffed0967",
    "specs/agora/v2/compatibility_manifest.schema.json": "84c3607195484d09710708c08e7c29821b75d83199376cd5374a2ce0c3ca7827",
    "specs/agora/v2/dashboard_recipe_v2.schema.json": "34c7e0fab793ec79776e9ddd5cca98683cacc6b8bba328e02a8c4c5eba45c13a",
    "specs/agora/v2/widget_spec_v2.schema.json": "d360a17a9762d69e6a5e2c87921117bb85ee34d972fd8034f8904df6facb993f",
    "specs/agora/widget_spec.schema.json": "0749275943dc155afa08dbb8736c336d613daf18b99b42f6c10aec15d2eabedb"
  },
  "frozen_by": "AG-XR-001",
  "operation_count": 96,
  "schema_count": 17,
  "source_bundle": "services/control-plane/specs/agora/bundle_index.v1_1.json"
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
  "auth_level": "agora_user",
  "bff_path_prefixes": [
    "/bff/agora/workshops"
  ],
  "bff_route_families": [],
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
  {
  "auth_level": "agora_user",
  "bff_path_prefixes": [
    "/bff/agora/servant"
  ],
  "bff_route_families": [],
  "name": "agora.servant.v1",
  "schemas": []
},
  {
  "auth_level": "agora_user",
  "bff_path_prefixes": [
    "/bff/agora/dashboard-recipes",
    "/bff/agora/strategies",
    "/bff/agora/widgets"
  ],
  "bff_route_families": [],
  "name": "agora.dashboard.v2",
  "schemas": [
    "v2/dashboard_recipe_v2.schema.json",
    "v2/widget_spec_v2.schema.json",
    "v2/chart_spec_v1.schema.json"
  ]
},
] as const;

export type AgoraCapability = typeof AGORA_V1_CAPABILITIES[number];
export type AgoraCapabilityName = "agora.identity.v1" | "agora.session.v1" | "agora.workshop.v1" | "agora.research.v1" | "agora.trading.v1" | "agora.dashboard.v1" | "agora.personalization.v1" | "agora.servant.v1" | "agora.dashboard.v2";

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
  {
  "method": "GET",
  "operationId": "getAgoraServant",
  "path": "/bff/agora/servant",
  "tags": [
    "agora-servant"
  ]
},
  {
  "method": "POST",
  "operationId": "ensureAgoraServant",
  "path": "/bff/agora/servant/ensure",
  "tags": [
    "agora-servant"
  ]
},
  {
  "method": "POST",
  "operationId": "reconcileAgoraServant",
  "path": "/bff/agora/servant/reconcile",
  "tags": [
    "agora-servant"
  ]
},
  {
  "method": "POST",
  "operationId": "createServantSession",
  "path": "/bff/agora/servant/sessions",
  "tags": [
    "agora-servant"
  ]
},
  {
  "method": "GET",
  "operationId": "getServantSession",
  "path": "/bff/agora/servant/sessions/{session_id}",
  "tags": [
    "agora-servant"
  ]
},
  {
  "method": "POST",
  "operationId": "postServantSessionMessage",
  "path": "/bff/agora/servant/sessions/{session_id}/messages",
  "tags": [
    "agora-servant"
  ]
},
  {
  "method": "POST",
  "operationId": "terminateServantSession",
  "path": "/bff/agora/servant/sessions/{session_id}/terminate",
  "tags": [
    "agora-servant"
  ]
},
  {
  "method": "GET",
  "operationId": "streamServantSession",
  "path": "/bff/agora/servant/sessions/{session_id}/stream",
  "tags": [
    "agora-servant"
  ]
},
  {
  "method": "GET",
  "operationId": "listAgoraWorkshops",
  "path": "/bff/agora/workshops",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "POST",
  "operationId": "createAgoraWorkshop",
  "path": "/bff/agora/workshops",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "GET",
  "operationId": "getAgoraWorkshop",
  "path": "/bff/agora/workshops/{workshop_id}",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "POST",
  "operationId": "postAgoraWorkshopMessage",
  "path": "/bff/agora/workshops/{workshop_id}/messages",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "GET",
  "operationId": "listAgoraWorkshopEvents",
  "path": "/bff/agora/workshops/{workshop_id}/events",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "GET",
  "operationId": "getAgoraWorkshopCompleteness",
  "path": "/bff/agora/workshops/{workshop_id}/completeness",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "GET",
  "operationId": "listAgoraWorkshopVersions",
  "path": "/bff/agora/workshops/{workshop_id}/versions",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "POST",
  "operationId": "createAgoraWorkshopVersion",
  "path": "/bff/agora/workshops/{workshop_id}/versions",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "POST",
  "operationId": "selectAgoraWorkshopVersion",
  "path": "/bff/agora/workshops/{workshop_id}/versions/{version_id}/select",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "POST",
  "operationId": "dispatchAgoraWorkshopResearchRun",
  "path": "/bff/agora/workshops/{workshop_id}/research-runs",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "POST",
  "operationId": "openAgoraWorkshopConsultation",
  "path": "/bff/agora/workshops/{workshop_id}/consultations",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "POST",
  "operationId": "concludeAgoraWorkshop",
  "path": "/bff/agora/workshops/{workshop_id}/conclude",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "GET",
  "operationId": "streamAgoraWorkshop",
  "path": "/bff/agora/workshops/{workshop_id}/stream",
  "tags": [
    "agora-workshop"
  ]
},
  {
  "method": "GET",
  "operationId": "listDashboardRecipes",
  "path": "/bff/agora/strategies/{strategy_id}/dashboard-recipes",
  "tags": [
    "agora-dashboard-v2"
  ]
},
  {
  "method": "POST",
  "operationId": "proposeDashboardRecipe",
  "path": "/bff/agora/strategies/{strategy_id}/dashboard-recipes/proposals",
  "tags": [
    "agora-dashboard-v2"
  ]
},
  {
  "method": "GET",
  "operationId": "getDashboardRecipe",
  "path": "/bff/agora/dashboard-recipes/{recipe_id}",
  "tags": [
    "agora-dashboard-v2"
  ]
},
  {
  "method": "POST",
  "operationId": "acceptDashboardRecipe",
  "path": "/bff/agora/dashboard-recipes/{recipe_id}/accept",
  "tags": [
    "agora-dashboard-v2"
  ]
},
  {
  "method": "PATCH",
  "operationId": "patchDashboardRecipeLayout",
  "path": "/bff/agora/dashboard-recipes/{recipe_id}/layout",
  "tags": [
    "agora-dashboard-v2"
  ]
},
  {
  "method": "POST",
  "operationId": "rollbackDashboardRecipe",
  "path": "/bff/agora/dashboard-recipes/{recipe_id}/rollback",
  "tags": [
    "agora-dashboard-v2"
  ]
},
  {
  "method": "POST",
  "operationId": "submitDashboardRecipeFeedback",
  "path": "/bff/agora/dashboard-recipes/{recipe_id}/feedback",
  "tags": [
    "agora-dashboard-v2"
  ]
},
  {
  "method": "GET",
  "operationId": "listDashboardRecipeVersions",
  "path": "/bff/agora/dashboard-recipes/{recipe_id}/versions",
  "tags": [
    "agora-dashboard-v2"
  ]
},
  {
  "method": "POST",
  "operationId": "validateAgoraWidget",
  "path": "/bff/agora/widgets/validate",
  "tags": [
    "agora-dashboard-v2"
  ]
},
  {
  "method": "POST",
  "operationId": "submitWidgetFeedback",
  "path": "/bff/agora/widgets/{widget_id}/feedback",
  "tags": [
    "agora-dashboard-v2"
  ]
},
  {
  "method": "POST",
  "operationId": "proposeWidgetPlugin",
  "path": "/bff/agora/widgets/propose-plugin",
  "tags": [
    "agora-dashboard-v2"
  ]
},
  {
  "method": "POST",
  "operationId": "openclawAdapterEnsureAgent",
  "path": "/api/openclaw-adapter/agents/ensure",
  "tags": [
    "openclaw-adapter"
  ]
},
  {
  "method": "GET",
  "operationId": "openclawAdapterGetAgent",
  "path": "/api/openclaw-adapter/agents/{persona_id}",
  "tags": [
    "openclaw-adapter"
  ]
},
  {
  "method": "POST",
  "operationId": "openclawAdapterReconcileAgent",
  "path": "/api/openclaw-adapter/agents/{persona_id}/reconcile",
  "tags": [
    "openclaw-adapter"
  ]
},
] as const;

export type AgoraRoute = typeof AGORA_V1_OPERATIONS[number];
export type AgoraHttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
export type AgoraOperationId = typeof AGORA_V1_OPERATIONS[number]["operationId"];

export interface AgoraUserScope {
  spec_version: "1.0";
  scope_id: string;
  tenant_id: string;
  user_id: string;
  operator_id: string;
  granted_capabilities: Array<"agora.identity.v1" | "agora.session.v1" | "agora.workshop.v1" | "agora.research.v1" | "agora.trading.v1" | "agora.dashboard.v1" | "agora.personalization.v1">;
  capabilities?: Array<"agora.identity.v1" | "agora.session.v1" | "agora.workshop.v1" | "agora.research.v1" | "agora.trading.v1" | "agora.dashboard.v1" | "agora.personalization.v1">;
  roles?: Array<string>;
  denied_capabilities?: Array<string>;
  surfaces?: Array<"agora">;
  persona_ids?: Array<string>;
  read_predicate: {
    tenant_id: string;
    user_id: string;
    required_fields: Array<"tenant_id" | "user_id">;
    fail_closed: true;
  };
  servant_policy: {
    persona_class: "agora_servant";
    owner_scope: "user_private";
    visibility_scope: "private" | "redacted_management";
    memory_scope: "private_user";
    persona_registry_backed: true;
    execution_authority: "none";
    prohibited_authority: Array<"runtime_binding" | "broker_order" | "capital_binding">;
  };
  created_at: string;
  expires_at?: string | null;
  policy_refs?: Array<string>;
  metadata?: Record<string, unknown>;
}

export interface ServantProfile {
  spec_version: "1.0";
  persona_id: string;
  display_name: string;
  status: "active" | "suspended" | "paper_only" | "shadow_only" | "retired";
  tenant_id: string;
  agora_user_id: string;
  persona_class: "agora_servant";
  owner_scope: "user_private";
  visibility_scope: "private" | "redacted_management";
  memory_scope: "private_user";
  capability_summary: {
    can_ask: boolean;
    can_research: boolean;
    can_workshop: boolean;
    can_shadow?: boolean;
    asset_classes?: Array<string>;
    strategy_families?: Array<string>;
    allowed_agora_capabilities?: Array<"agora.identity.v1" | "agora.session.v1" | "agora.workshop.v1" | "agora.research.v1" | "agora.trading.v1" | "agora.dashboard.v1" | "agora.personalization.v1">;
  };
  policy: {
    persona_class: "agora_servant";
    owner_scope: "user_private";
    visibility_scope: "private" | "redacted_management";
    memory_scope: "private_user";
    persona_registry_backed: true;
    execution_authority: "none";
    prohibited_authority: Array<"runtime_binding" | "broker_order" | "capital_binding">;
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

export interface WidgetSpecV2 {
  spec_version: "2.0";
  widget_id: string;
  widget_type: string;
  title: string;
  description?: string;
  why_this_widget?: string;
  data_source_id: string;
  query: {
    filters: Record<string, unknown>;
    sort?: Record<string, "asc" | "desc">;
    limit?: number;
    window?: string;
  };
  chart_spec: ChartSpecV1;
  interactions: Array<{
    kind: "open_candidate" | "open_strategy" | "open_position" | "open_evidence" | "open_research_run" | "open_shadow_record" | "filter_workspace" | "cross_highlight" | "add_to_monitoring" | "remove_from_monitoring" | "park_candidate" | "request_more_research" | "send_to_shadow" | "request_widget_revision" | "create_journal_note";
    params?: Record<string, unknown>;
  }>;
  sensitivity: "public_market" | "user_private" | "broker_sensitive" | "restricted";
  can_export: boolean;
  registry_version: "widget_registry.v1";
  layout_constraints?: {
    min_w?: number;
    min_h?: number;
    max_w?: number;
    max_h?: number;
  };
  version: number;
  content_sha256?: string;
  created_at: string;
  updated_at?: string;
  metadata?: Record<string, unknown>;
}

export interface ChartSpecV1 {
  spec_version: "1.0";
  kind: "metric" | "table" | "line" | "area" | "bar" | "stacked_bar" | "heatmap" | "scatter" | "network" | "timeline" | "sankey" | "candlestick" | "gauge";
  encodings: Record<string, {
    field: string;
    type: "quantitative" | "temporal" | "nominal" | "ordinal";
    aggregate?: "sum" | "mean" | "median" | "min" | "max" | "count" | "distinct_count";
    scale?: "linear" | "log" | "symlog" | "time" | "band";
    format?: string;
    title?: string;
  }>;
  transforms?: Array<{
    type: "filter" | "sort" | "top_k" | "aggregate" | "window" | "rolling_mean" | "rolling_sum" | "percent_change" | "rank" | "percentile" | "normalize" | "winsorize" | "zscore" | "bucket" | "time_bucket" | "join_by_key";
    params?: Record<string, unknown>;
  }>;
  tooltip_fields?: Array<string>;
  thresholds?: Array<{
    field: string;
    operator: "lt" | "lte" | "eq" | "gte" | "gt" | "between";
    value: unknown;
    severity?: "info" | "watch" | "warning" | "high" | "critical";
    label?: string;
  }>;
  click_action?: {
    kind: "open_candidate" | "open_strategy" | "open_position" | "open_evidence" | "open_research_run" | "open_shadow_record" | "filter_workspace" | "cross_highlight" | "add_to_monitoring" | "remove_from_monitoring" | "park_candidate" | "request_more_research" | "send_to_shadow" | "request_widget_revision" | "create_journal_note";
    params?: Record<string, unknown>;
  };
  options?: Record<string, unknown>;
}

export interface DashboardRecipeV2 {
  spec_version: "2.0";
  recipe_id: string;
  tenant_id: string;
  user_id: string;
  strategy_id: string;
  strategy_version_id: string;
  workspace: "trading_room" | "strategy_workshop" | "strategy_performance";
  phase: "candidate_review" | "monitoring" | "position_monitoring" | "post_trade_review";
  views: Array<{
    view_id: string;
    title: string;
    purpose: string;
    layout_template_id: string;
    breakpoints: Record<string, number>;
    placements: Array<{
      widget_id: string;
      x: number;
      y: number;
      w: number;
      h: number;
      min_w: number;
      min_h: number;
      max_w?: number;
      max_h?: number;
      pinned?: boolean;
    }>;
    widgets: Array<WidgetSpecV2>;
  }>;
  generated_by: "system_default" | "servant" | "user" | "learned";
  change_reason: string;
  version: number;
  previous_version?: number | null;
  status: "proposal" | "active" | "archived" | "rolled_back";
  content_sha256?: string;
  created_at: string;
  updated_at: string;
  metadata?: Record<string, unknown>;
}

export interface AgoraCrossRepoCompatibilityManifest {
  manifest_version: "1.0";
  contract_family: "agora.v1.1";
  environment: "dev" | "staging" | "production";
  generated: true;
  backend: {
    repo: "ajoe734/pantheon";
    runtime_commit: string;
    contract_commit: string;
    base_bundle_index_sha256: string;
    extension_bundle_index_sha256: string;
    openapi_sha256: string;
  };
  frontend: {
    repo: "ajoe734/execute-plans";
    runtime_commit: string;
    generated_from_contract_commit: string;
    base_bundle_index_sha256: string;
    extension_bundle_index_sha256: string;
    openapi_sha256: string;
    generated_types_sha256: string;
  };
  contract_bundle: {
    base_path: "services/control-plane/specs/agora/bundle_index.json";
    extension_path: "services/control-plane/specs/agora/bundle_index.v1_1.json";
    openapi_path: "services/control-plane/openapi/agora_v1_1.openapi.yaml";
  };
  required_capabilities: Array<{
    name: string;
    version: string;
    required: boolean;
  }>;
  hash_policy: {
    file_hash: "sha256-exact-git-bytes-v1";
    generated_types_hash: "sha256-path-tab-filehash-lf-v1";
  };
  compatibility_status: "compatible" | "incompatible" | "pending";
  blocking_reasons?: Array<string>;
  generated_at: string;
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
  WidgetSpecV2: WidgetSpecV2;
  ChartSpecV1: ChartSpecV1;
  DashboardRecipeV2: DashboardRecipeV2;
  AgoraCrossRepoCompatibilityManifest: AgoraCrossRepoCompatibilityManifest;
}

export type AgoraSchemaName = "AgoraUserScope" | "ServantProfile" | "StrategyWorkshop" | "StrategyCompleteness" | "ResearchPlan" | "ResearchRunSummary" | "CandidatePool" | "DashboardRecipe" | "WidgetSpec" | "TradingEvent" | "TradingIntent" | "ShadowDecision" | "PersonalizationEvent" | "WidgetSpecV2" | "ChartSpecV1" | "DashboardRecipeV2" | "AgoraCrossRepoCompatibilityManifest";
export type AgoraSchema = AgoraSchemaMap[keyof AgoraSchemaMap];
