import { paths } from "./paths";

export type ManagementSurfaceStatus = "ok" | "degraded" | "unavailable" | string;

export interface ManagementSurfaceRef {
  status: ManagementSurfaceStatus;
  source?: string;
  message?: string;
  note?: string;
  staleness?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ManagementCockpitMeta {
  snapshot_at?: string;
  staleness?: Record<string, unknown>;
  surfaces: {
    management_cockpit: ManagementSurfaceRef;
    operator_home?: ManagementSurfaceRef;
    runtime_health?: ManagementSurfaceRef;
    alerts?: ManagementSurfaceRef;
    human_inbox?: ManagementSurfaceRef;
    trading_pulse?: ManagementSurfaceRef;
    anomalies?: ManagementSurfaceRef;
    [key: string]: ManagementSurfaceRef | undefined;
  };
}

export interface ManagementCockpitSection<T = Record<string, unknown>> {
  items?: T[];
  summary?: Record<string, unknown>;
  cards?: Record<string, unknown>[];
  rankings?: Record<string, unknown>[];
  meta?: {
    snapshot_at?: string;
    surfaces?: Record<string, ManagementSurfaceRef>;
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

export interface ManagementCockpitData {
  id: "management-cockpit" | string;
  snapshotAt?: string;
  snapshot_at?: string;
  operatorHome: Record<string, unknown>;
  operator_home: Record<string, unknown>;
  runtimeHealth: Record<string, unknown>;
  runtime_health: Record<string, unknown>;
  alerts: ManagementCockpitSection;
  humanInbox: ManagementCockpitSection;
  human_inbox: ManagementCockpitSection;
  tradingPulse: ManagementCockpitSection;
  trading_pulse: ManagementCockpitSection;
  anomalies: ManagementCockpitSection;
  links?: Record<string, string>;
}

export interface ManagementCockpitResponse {
  data: ManagementCockpitData;
  operator_home: Record<string, unknown>;
  runtime_health: Record<string, unknown>;
  alerts: ManagementCockpitSection;
  human_inbox: ManagementCockpitSection;
  trading_pulse: ManagementCockpitSection;
  anomalies: ManagementCockpitSection;
  meta: ManagementCockpitMeta;
}

export interface ManagementEvidenceQuery {
  ref_id?: string;
  linked_entity_type?: string;
  linked_entity_ref?: string;
  link_type?: string;
  credibility_tier?: string;
  verified?: boolean;
  page_token?: string;
  page_size?: number;
}

export interface ManagementPortfolioBookHolding {
  id: string;
  holding_id: string;
  runtime_id?: string;
  runtime_binding_id?: string;
  deployment_plan_id?: string;
  capital_pool_id?: string;
  capitalPoolId?: string;
  persona_id?: string;
  personaId?: string;
  strategy_id?: string;
  strategyId?: string;
  artifact_id?: string;
  artifact_version?: string;
  deployment_stage?: string;
  deploymentStage?: string;
  status?: string;
  symbol?: string;
  side?: string;
  quantity?: number | null;
  average_price?: number | null;
  avgPrice?: number | null;
  mark_price?: number | null;
  markPrice?: number | null;
  market_value?: number | null;
  marketValue?: number | null;
  notional?: number | null;
  exposure?: number | null;
  weight?: number | null;
  total_pnl?: number | null;
  unrealized_pnl?: number | null;
  realized_pnl?: number | null;
  last_mark_at?: string | null;
  instrument?: Record<string, unknown>;
  capital_pool?: Record<string, unknown>;
  pnl?: Record<string, unknown>;
  telemetry?: Record<string, unknown>;
  links?: Record<string, string | null | undefined>;
  [key: string]: unknown;
}

export interface ManagementPortfolioBookHoldingsSummary {
  holding_count: number;
  returned_holding_count: number;
  active_holding_count: number;
  paper_holding_count: number;
  live_holding_count: number;
  runtime_count: number;
  telemetry_runtime_count: number;
  total_notional?: number | null;
  total_market_value?: number | null;
  total_unrealized_pnl?: number | null;
  total_realized_pnl?: number | null;
  total_pnl?: number | null;
  latest_mark_at?: string | null;
  [key: string]: unknown;
}

export interface ManagementPortfolioBookHoldingsResponse {
  data: {
    summary: ManagementPortfolioBookHoldingsSummary;
    items: ManagementPortfolioBookHolding[];
    holdings: ManagementPortfolioBookHolding[];
    [key: string]: unknown;
  };
  items: ManagementPortfolioBookHolding[];
  summary: ManagementPortfolioBookHoldingsSummary;
  page_info: {
    next_page_token: string | null;
    total: number;
  };
  meta: {
    snapshot_at?: string;
    surfaces?: Record<string, ManagementSurfaceRef>;
    composition_sources?: string[];
    [key: string]: unknown;
  };
}

export interface ManagementPortfolioBookHoldingsQuery {
  capital_pool_id?: string;
  persona_id?: string;
  runtime_id?: string;
  deployment_stage?: string;
  status?: string;
  q?: string;
  page_token?: string;
  page_size?: number;
}

export interface ManagementEvidenceItem {
  id: string;
  refId: string;
  ref_id: string;
  title?: string;
  displayLabel?: string;
  display_label?: string;
  sourceType?: string;
  source_type?: string;
  sourceRef?: string;
  source_ref?: string;
  capturedAt?: string;
  captured_at?: string;
  linkType?: string;
  link_type?: string;
  credibility?: Record<string, unknown>;
  linkedObjectSummary?: Record<string, unknown>;
  linked_object_summary?: Record<string, unknown>;
  resolvedLink?: Record<string, unknown>;
  resolved_link?: Record<string, unknown>;
  routeHref?: string;
  route_href?: string;
  managementHref?: string;
  management_href?: string;
  kind?: string;
  requiredCapability?: string;
  required_capability?: string;
  reason?: string;
  redacted?: boolean;
  [key: string]: unknown;
}

export interface ManagementEvidenceSummary {
  totalEvidence: number;
  total_evidence: number;
  returnedEvidence: number;
  returned_evidence: number;
  visibleEvidence: number;
  visible_evidence: number;
  redactedEvidence: number;
  redacted_evidence: number;
  verifiedEvidence: number;
  verified_evidence: number;
  bySourceType: Record<string, number>;
  by_source_type: Record<string, number>;
  byLinkType: Record<string, number>;
  by_link_type: Record<string, number>;
  byCredibilityTier: Record<string, number>;
  by_credibility_tier: Record<string, number>;
  [key: string]: unknown;
}

export interface ManagementEvidenceResponse {
  data: ManagementEvidenceItem[];
  items: ManagementEvidenceItem[];
  summary: ManagementEvidenceSummary;
  facets: {
    sourceTypes?: Record<string, number>;
    source_types?: Record<string, number>;
    linkTypes?: Record<string, number>;
    link_types?: Record<string, number>;
    credibilityTiers?: Record<string, number>;
    credibility_tiers?: Record<string, number>;
    [key: string]: unknown;
  };
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size: number;
  };
  pagination?: {
    next_page_token?: string | null;
    has_more?: boolean;
    page_size?: number;
    [key: string]: unknown;
  };
  meta: {
    snapshot_at?: string;
    staleness?: Record<string, unknown>;
    surfaces: {
      management_evidence: ManagementSurfaceRef;
      evidence_refs?: ManagementSurfaceRef;
      knowledge_evidence?: ManagementSurfaceRef;
      [key: string]: ManagementSurfaceRef | undefined;
    };
    redacted_evidence_count?: number;
    [key: string]: unknown;
  };
}

type ManagementQueryValue = string | number | boolean | undefined | null;

function withQuery(path: string, query?: object): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query as Record<string, ManagementQueryValue>)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  const suffix = params.toString();
  return suffix ? `${path}?${suffix}` : path;
}

export function managementCockpitPath(): string {
  return paths.managementCockpit();
}

export function managementEvidencePath(query?: ManagementEvidenceQuery): string {
  return withQuery(paths.managementEvidence(), query);
}

export function managementPortfolioBookHoldingsPath(
  query?: ManagementPortfolioBookHoldingsQuery,
): string {
  return withQuery(paths.managementPortfolioBookHoldings(), query);
}

export async function fetchManagementCockpit(
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementCockpitResponse> {
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${managementCockpitPath()}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${managementCockpitPath()} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementCockpitResponse>;
}

export async function fetchManagementEvidence(
  query?: ManagementEvidenceQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementEvidenceResponse> {
  const path = managementEvidencePath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementEvidenceResponse>;
}

export async function fetchManagementPortfolioBookHoldings(
  query?: ManagementPortfolioBookHoldingsQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementPortfolioBookHoldingsResponse> {
  const path = managementPortfolioBookHoldingsPath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementPortfolioBookHoldingsResponse>;
}
