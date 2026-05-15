export const SNAPSHOT_AT = "2026-05-13T14:30:00Z";

export const SEEDED_IDS = {
  approval: "approval-dev",
  artifact: "artifact-dev",
  auditEvent: "audit-event-dev",
  capital: "capital-dev",
  capitalPool: "capital-dev",
  command: "cmd-dev",
  correlation: "corr-fe-int-gate-dev",
  deployment: "deployment-dev",
  evolutionProgram: "evolution-program-dev",
  intervention: "hiq-intervention-dev",
  operator: "op-fe-gate",
  persona: "persona-dev",
  rankingFormula: "ranking-formula-dev",
  rebalance: "rebalance-dev",
  researchExperiment: "research-experiment-dev",
  strategy: "strategy-dev",
  tenant: "tenant-dev",
} as const;

export const STRATEGY_DEV_ID = SEEDED_IDS.strategy;
export const PERSONA_DEV_ID = SEEDED_IDS.persona;
export const CAPITAL_DEV_ID = SEEDED_IDS.capital;
export const CAPITAL_POOL_DEV_ID = SEEDED_IDS.capitalPool;
export const RANKING_FORMULA_DEV_ID = SEEDED_IDS.rankingFormula;
export const REBALANCE_DEV_ID = SEEDED_IDS.rebalance;
export const DEPLOYMENT_DEV_ID = SEEDED_IDS.deployment;
export const EVOLUTION_PROGRAM_DEV_ID = SEEDED_IDS.evolutionProgram;
export const RESEARCH_EXPERIMENT_DEV_ID = SEEDED_IDS.researchExperiment;
export const ARTIFACT_DEV_ID = SEEDED_IDS.artifact;
export const APPROVAL_DEV_ID = SEEDED_IDS.approval;
export const INTERVENTION_DEV_ID = SEEDED_IDS.intervention;
export const OPERATOR_DEV_ID = SEEDED_IDS.operator;
export const TENANT_DEV_ID = SEEDED_IDS.tenant;
export const CORRELATION_DEV_ID = SEEDED_IDS.correlation;

export const SEEDED_RESOURCE_IDS = {
  artifacts: ARTIFACT_DEV_ID,
  "capital-pools": CAPITAL_POOL_DEV_ID,
  deployments: DEPLOYMENT_DEV_ID,
  "evolution-programs": EVOLUTION_PROGRAM_DEV_ID,
  personas: PERSONA_DEV_ID,
  "ranking-formulas": RANKING_FORMULA_DEV_ID,
  rebalances: REBALANCE_DEV_ID,
  "research-experiments": RESEARCH_EXPERIMENT_DEV_ID,
  strategies: STRATEGY_DEV_ID,
} as const;

export type SeededResource = keyof typeof SEEDED_RESOURCE_IDS;
export type SeededIdKey = keyof typeof SEEDED_IDS;

export const CREATE_INTENT_RESOURCE_KEYS = [
  "strategies",
  "personas",
  "capital-pools",
  "ranking-formulas",
  "rebalances",
  "deployments",
  "evolution-programs",
  "research-experiments",
  "artifacts",
] as const;

export type CreateIntentResource = (typeof CREATE_INTENT_RESOURCE_KEYS)[number];

export function seededId(key: SeededIdKey): string {
  return SEEDED_IDS[key];
}

export function seededResourceId(resource: SeededResource): string {
  return SEEDED_RESOURCE_IDS[resource];
}

export function seededCorrelationId(suffix = "default"): string {
  return `${CORRELATION_DEV_ID}-${suffix}`;
}

export function seededRequestId(suffix = "default"): string {
  return `req-fe-int-gate-${suffix}`;
}

export function seededIdempotencyKey(resource: SeededResource, suffix = "default"): string {
  return `idk-fe-int-gate-${resource}-${suffix}`;
}

export function seededCommandId(suffix = "default"): string {
  return `${SEEDED_IDS.command}-${suffix}`;
}

export function listEnvelope<T>(
  items: T[],
  meta: Record<string, unknown> = {},
): { items: T[]; meta: Record<string, unknown>; totalCount: number; totalCountExact: true } {
  return {
    items,
    meta: {
      snapshot_at: SNAPSHOT_AT,
      ...meta,
    },
    totalCount: items.length,
    totalCountExact: true,
  };
}

export function dataEnvelope<T>(
  data: T,
  meta: Record<string, unknown> = {},
): { data: T; meta: Record<string, unknown> } {
  return {
    data,
    meta: {
      snapshot_at: SNAPSHOT_AT,
      ...meta,
    },
  };
}
