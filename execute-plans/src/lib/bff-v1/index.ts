export { paths } from "./paths";
export type { ListEnvelope } from "./lists";
export { normalizeLiveListResponse } from "./lists";

export interface LiveStatus {
  effective: "mock" | "live";
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
};

export interface BffRequest {
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  path: string;
  query?: Record<string, string | number | undefined>;
  body?: unknown;
}

export async function withLiveOrMock<T>(
  _req: BffRequest,
  mockFn: () => Promise<T>,
): Promise<T> {
  return mockFn();
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
