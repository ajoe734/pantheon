// BFF Contract v1 — typed path builders.
// Source: .lovable/feedback/2026-05-07-final/Pantheon_BFF_OpenAPI_3_1.yaml
// Builders only — no fetching. Use with client.ts.
//
// Live-Wiring Alignment Patch (2026-05-08): aligned to final OpenAPI canonical paths
//   - /bff/me                                (was /bff/session/me)
//   - /bff/auth/refresh                      (was /bff/session/refresh)
//   - /bff/logout                            (was /bff/session/logout)
//   - /bff/approvals/{id}/decide             (was /bff/approvals/{id}/decision)
//   - /bff/v5/interventions/{id}/decide      (was .../decision)
//   - /bff/mcp-servers/{id}/import-tools     (new — replaces /bff/mcp-tools/import)
//   - /bff/actions/{entityType}/{entityId}/{actionId} — canonical action endpoint
//
// Legacy nested per-entity action builders (strategyAction, personaAction, etc.)
// remain only for in-process mock compatibility and are NOT canonical. Live
// callers MUST use `actionCanonical(entityType, entityId, actionId)`.

const BASE = "/bff";

const enc = (s: string | number) => encodeURIComponent(String(s));

export const paths = {
  // ---- Session (canonical) ----
  me: () => `${BASE}/me`,
  meLocale: () => `${BASE}/me/locale`,
  authRefresh: () => `${BASE}/auth/refresh`,
  logout: () => `${BASE}/logout`,
  /** @deprecated Alias of `me()` retained for legacy callers. */
  sessionMe: () => `${BASE}/me`,
  /** @deprecated Alias of `authRefresh()`. */
  sessionRefresh: () => `${BASE}/auth/refresh`,
  /** @deprecated Alias of `logout()`. */
  sessionLogout: () => `${BASE}/logout`,

  // ---- Canonical action endpoint (Final §1772) ----
  action: (entityType: string, entityId: string, actionId: string) =>
    `${BASE}/actions/${enc(entityType)}/${enc(entityId)}/${enc(actionId)}`,

  // ---- Strategies / Personas / etc. (resource paths still canonical) ----
  strategies: () => `${BASE}/strategies`,
  strategy: (id: string) => `${BASE}/strategies/${enc(id)}`,
  /** @deprecated Use `paths.action("strategy", id, action)`. Kept for in-process mocks. */
  strategyAction: (id: string, action: string) => `${BASE}/strategies/${enc(id)}/actions/${enc(action)}`,

  personas: () => `${BASE}/personas`,
  persona: (id: string) => `${BASE}/personas/${enc(id)}`,
  personaRoutePolicy: (id: string) => `${BASE}/personas/${enc(id)}/route-policy`,
  personaEvaluations: (id: string) => `${BASE}/personas/${enc(id)}/evaluations`,
  personaMemory: (id: string) => `${BASE}/personas/${enc(id)}/memory`,
  /** @deprecated Use `paths.action("persona", id, action)`. */
  personaAction: (id: string, action: string) => `${BASE}/personas/${enc(id)}/actions/${enc(action)}`,

  capitalPools: () => `${BASE}/capital-pools`,
  capitalPool: (id: string) => `${BASE}/capital-pools/${enc(id)}`,
  /** @deprecated Use `paths.action("capitalPool", id, action)`. */
  capitalPoolAction: (id: string, action: string) => `${BASE}/capital-pools/${enc(id)}/actions/${enc(action)}`,

  rebalances: () => `${BASE}/rebalances`,
  rebalance: (id: string) => `${BASE}/rebalances/${enc(id)}`,
  /** @deprecated Use `paths.action("rebalance", id, action)`. */
  rebalanceAction: (id: string, action: string) => `${BASE}/rebalances/${enc(id)}/actions/${enc(action)}`,

  deployments: () => `${BASE}/deployments`,
  deployment: (id: string) => `${BASE}/deployments/${enc(id)}`,
  /** @deprecated Use `paths.action("deployment", id, action)`. */
  deploymentAction: (id: string, action: string) => `${BASE}/deployments/${enc(id)}/actions/${enc(action)}`,

  // ---- Evolution ----
  evolutionPrograms: () => `${BASE}/evolution-programs`,
  evolutionProgram: (id: string) => `${BASE}/evolution-programs/${enc(id)}`,
  evolutionProgramRuns: (id: string) => `${BASE}/evolution-programs/${enc(id)}/runs`,
  evolutionProgramCandidates: (id: string) => `${BASE}/evolution-programs/${enc(id)}/candidates`,

  // ---- Jobs / Approvals / Incidents ----
  jobs: () => `${BASE}/jobs`,
  job: (id: string) => `${BASE}/jobs/${enc(id)}`,
  approvals: () => `${BASE}/approvals`,
  approval: (id: string) => `${BASE}/approvals/${enc(id)}`,
  approvalDecide: (id: string) => `${BASE}/approvals/${enc(id)}/decide`,
  approvalsBatchDecide: () => `${BASE}/approvals/batch-decide`,
  /** @deprecated Alias of `approvalDecide(id)` — final OpenAPI uses `/decide`. */
  approvalDecision: (id: string) => `${BASE}/approvals/${enc(id)}/decide`,
  alerts: () => `${BASE}/alerts`,
  alertAcknowledge: (id: string) => `${BASE}/alerts/${enc(id)}/acknowledge`,
  incidents: () => `${BASE}/incidents`,
  incident: (id: string) => `${BASE}/incidents/${enc(id)}`,

  // ---- Audit / Artifacts ----
  audit: () => `${BASE}/audit`,
  artifacts: () => `${BASE}/artifacts`,
  artifact: (id: string) => `${BASE}/artifacts/${enc(id)}`,

  // ---- Runtime / MCP / Skill / Channel / Tool / Ranking ----
  runtimes: () => `${BASE}/runtimes`,
  mcpServers: () => `${BASE}/mcp-servers`,
  mcpServerImportTools: (id: string) => `${BASE}/mcp-servers/${enc(id)}/import-tools`,
  mcpTools: () => `${BASE}/mcp-tools`,
  /** @deprecated Use `mcpServerImportTools(serverId)` per final OpenAPI. */
  mcpToolImport: () => `${BASE}/mcp-tools/import`,
  skills: () => `${BASE}/skills`,
  channels: () => `${BASE}/channels`,
  tools: () => `${BASE}/tools`,
  rankingFormulas: () => `${BASE}/ranking-formulas`,
  search: () => `${BASE}/search`,

  // ---- Research ----
  researchExperiments: () => `${BASE}/research-experiments`,
  strategySpecs: (id: string) => `${BASE}/strategies/${enc(id)}/specs`,

  // ---- Command confirmations (v3 §6.2) — submission endpoint ----
  // /bff/command-confirmations requires confirm_token + command_id in body (submission of an already-issued token).
  commandConfirmations: () => `${BASE}/command-confirmations`,
  commandConfirmation: (token: string) => `${BASE}/command-confirmations/${enc(token)}`,

  // ---- Confirm-token lifecycle (v3 §6.2 create/read/redeem/delete) ----
  confirmTokens: () => `${BASE}/confirm-tokens`,
  confirmToken: (tokenId: string) => `${BASE}/confirm-tokens/${enc(tokenId)}`,
  confirmTokenRedeem: (tokenId: string) => `${BASE}/confirm-tokens/${enc(tokenId)}/redeem`,

  // ---- SSE ----
  sse: () => `${BASE}/events/stream`,

  // ---- Agora ----
  agoraSignals: () => `${BASE}/agora/signals`,
  agoraInbox: () => `${BASE}/agora/inbox`,
  agoraJournal: () => `${BASE}/agora/journal`,
  agoraPostmortems: () => `${BASE}/agora/postmortems`,
  agoraAskSessions: () => `${BASE}/agora/ask/sessions`,
  agoraAskSession: (id: string) => `${BASE}/agora/ask/sessions/${enc(id)}`,

  // ---- v5 closed-loop ----
  v5LoopRuns: () => `${BASE}/v5/loop-runs`,
  v5LoopRun: (id: string) => `${BASE}/v5/loop-runs/${enc(id)}`,
  v5SentinelFindings: () => `${BASE}/v5/sentinel/findings`,
  v5Interventions: () => `${BASE}/v5/interventions`,
  v5Intervention: (id: string) => `${BASE}/v5/interventions/${enc(id)}`,
  v5InterventionDecide: (id: string) => `${BASE}/v5/interventions/${enc(id)}/decide`,
  /** @deprecated Alias of `v5InterventionDecide(id)`. */
  v5InterventionDecision: (id: string) => `${BASE}/v5/interventions/${enc(id)}/decide`,
  v5ExecutionPersonaHealth: () => `${BASE}/v5/execution/persona-health`,

  // ---- Management OODA packet read surface ----
  oodaPackets: () => `${BASE}/ooda/packets`,
  oodaPacket: (id: string) => `${BASE}/ooda/packets/${enc(id)}`,
  strategyOodaPackets: (id: string) => `${BASE}/strategies/${enc(id)}/ooda`,
  runtimeOodaPackets: (id: string) => `${BASE}/runtimes/${enc(id)}/ooda`,
  evolutionProgramOodaPackets: (id: string) => `${BASE}/evolution-programs/${enc(id)}/ooda`,
} as const;
