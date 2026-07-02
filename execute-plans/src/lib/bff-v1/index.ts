import { readBffEnv } from "./runtimeEnv";

export { paths } from "./paths";
export type { ListEnvelope } from "./lists";
export { normalizeLiveListResponse } from "./lists";
export {
  AGORA_V1_CAPABILITIES,
  AGORA_V1_CONTRACT_SNAPSHOT,
  AGORA_V1_OPERATIONS,
} from "./agora/types";
export type {
  AgoraCapability,
  AgoraCapabilityName,
  AgoraHttpMethod,
  AgoraOperationId,
  AgoraRoute,
  AgoraSchema,
  AgoraSchemaMap,
  AgoraSchemaName,
  AgoraV1ContractFile,
  AgoraUserScope,
  CandidatePool,
  DashboardRecipe,
  PersonalizationEvent,
  ResearchPlan,
  ResearchRunSummary,
  ServantProfile,
  ShadowDecision,
  StrategyCompleteness,
  StrategyWorkshop,
  TradingEvent,
  TradingIntent,
  WidgetSpec,
} from "./agora/types";

export interface LiveStatus {
  mode?: "mock" | "live";
  effective: "mock" | "live";
  baseUrl?: string;
  lastError?: string;
  fellBackAt?: number;
}

let _liveStatus: LiveStatus = { effective: "mock" };

export const liveStatus = {
  get(): LiveStatus {
    return _liveStatus;
  },
  set(status: Partial<LiveStatus>): void {
    _liveStatus = { ..._liveStatus, ...status };
  },
  _reset(status: Partial<LiveStatus> = {}): void {
    _liveStatus = { effective: "mock", ...status };
  },
};

export interface BffErrorPayload {
  code?: string;
  message: string;
  details?: unknown;
  status?: number;
}

export class BffError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly details?: unknown;
  readonly envelope: { error: BffErrorPayload };

  constructor(payload: BffErrorPayload, status = payload.status ?? 500) {
    super(payload.message);
    this.name = "BffError";
    this.status = status;
    this.code = payload.code;
    this.details = payload.details;
    this.envelope = { error: { ...payload, status } };
  }
}

export function makeBffError(payload: BffErrorPayload, status = payload.status ?? 500): BffError {
  return new BffError(payload, status);
}

export interface BffRequest {
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  path: string;
  query?: Record<string, string | number | undefined>;
  body?: unknown;
}

function buildBffUrl(path: string, query?: Record<string, string | number | undefined>): string {
  const baseUrl = readBffEnv().VITE_BFF_BASE_URL?.replace(/\/+$/, "") ?? "";
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const url = baseUrl ? `${baseUrl}${normalizedPath}` : normalizedPath;
  if (!query) return url;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) params.set(key, String(value));
  }
  const queryString = params.toString();
  return queryString ? `${url}?${queryString}` : url;
}

function bffErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}

export async function withLiveOrMock<T>(
  req: BffRequest,
  mockFn: () => Promise<T>,
): Promise<T> {
  const env = readBffEnv();
  if (env.VITE_BFF_MODE !== "live") {
    return mockFn();
  }

  const strict = env.VITE_BFF_FALLBACK === "strict";
  try {
    const response = await fetch(buildBffUrl(req.path, req.query), { method: req.method });
    if (!response.ok) {
      const error = new BffError(
        { code: "BFF_HTTP_ERROR", message: `BFF ${req.method} ${req.path}: HTTP ${response.status}` },
        response.status,
      );
      if (response.status >= 500 && !strict) {
        liveStatus.set({
          effective: "mock",
          lastError: error.message,
          fellBackAt: Date.now(),
        });
        return mockFn();
      }
      throw error;
    }
    liveStatus.set({ mode: "live", effective: "live", baseUrl: env.VITE_BFF_BASE_URL });
    return await response.json() as T;
  } catch (error) {
    if (error instanceof BffError) throw error;
    if (strict) {
      throw new BffError({ code: "BFF_TRANSPORT_ERROR", message: bffErrorMessage(error) }, 0);
    }
    liveStatus.set({
      effective: "mock",
      lastError: bffErrorMessage(error),
      fellBackAt: Date.now(),
    });
    return mockFn();
  }
}

import type { ListEnvelope } from "./lists";

function emptyList<T>(): Promise<ListEnvelope<T>> {
  return Promise.resolve({
    items: [],
    cursor: {},
    pageSize: 0,
    totalCountExact: true,
    estimatedTotal: 0,
  });
}

export const lists = {
  strategies: () => emptyList<{ id: string }>(),
  personas: () => emptyList<{ id: string }>(),
  capitalPools: () => emptyList<{ id: string }>(),
  rankingFormulas: () => emptyList<{ id: string }>(),
  rebalances: () => emptyList<{ id: string }>(),
  deployments: () => emptyList<{ id: string }>(),
  evolution: () => emptyList<{ id: string }>(),
  research: () => emptyList<{ id: string }>(),
  artifacts: () => emptyList<{ id: string }>(),
  tools: () => emptyList<{ id: string }>(),
  mcpServers: () => emptyList<{ id: string }>(),
  mcpTools: () => emptyList<{ id: string }>(),
  skills: () => emptyList<{ id: string }>(),
  channels: () => emptyList<{ id: string }>(),
  jobs: () => emptyList<{ id: string }>(),
  runtimes: () => emptyList<{ id: string }>(),
  alerts: () => emptyList<{ id: string }>(),
  incidents: () => emptyList<{ id: string }>(),
  approvals: () => emptyList<{ id: string }>(),
  audit: () => emptyList<{ id: string }>(),
};
