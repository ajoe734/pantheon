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

export function managementCockpitPath(): string {
  return paths.managementCockpit();
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
