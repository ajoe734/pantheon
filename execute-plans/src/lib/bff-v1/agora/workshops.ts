import type { StrategyWorkshop, StrategyCompleteness } from "./types";

export type { StrategyWorkshop, StrategyCompleteness };

export interface WorkshopReadiness {
  workshop_id: string;
  ready: boolean;
  blocking_dimensions?: string[];
  checked_at: string;
}

export interface WorkshopCard {
  card_id: string;
  workshop_id: string;
  kind: string;
  content: Record<string, unknown>;
  created_at: string;
}

function resolvedBase(baseUrl?: string): string {
  if (baseUrl) return baseUrl.replace(/\/+$/, "");
  if (typeof window !== "undefined" && window.location?.origin) {
    return window.location.origin.replace(/\/+$/, "");
  }
  return "";
}

function recordFrom(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

async function parseJson(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { error: { message: text } };
  }
}

function workshopFrom(value: unknown): StrategyWorkshop {
  const root = recordFrom(value);
  const data = recordFrom(root.data ?? root);
  return data as unknown as StrategyWorkshop;
}

function workshopsFrom(value: unknown): StrategyWorkshop[] {
  const root = recordFrom(value);
  const items = root.data ?? root;
  if (Array.isArray(items)) return items as StrategyWorkshop[];
  const data = recordFrom(items);
  const list = data.workshops ?? data.items ?? data.results;
  return Array.isArray(list) ? (list as StrategyWorkshop[]) : [];
}

function completenessFrom(value: unknown): StrategyCompleteness {
  const root = recordFrom(value);
  const data = recordFrom(root.data ?? root);
  return data as unknown as StrategyCompleteness;
}

function readinessFrom(value: unknown): WorkshopReadiness {
  const root = recordFrom(value);
  const data = recordFrom(root.data ?? root);
  return data as unknown as WorkshopReadiness;
}

function cardsFrom(value: unknown): WorkshopCard[] {
  const root = recordFrom(value);
  const items = root.data ?? root;
  if (Array.isArray(items)) return items as WorkshopCard[];
  const data = recordFrom(items);
  const list = data.cards ?? data.events ?? data.items ?? data.results;
  return Array.isArray(list) ? (list as WorkshopCard[]) : [];
}

export async function listWorkshops(baseUrl?: string): Promise<StrategyWorkshop[]> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/workshops`;
  const res = await fetch(url, {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    const body = await parseJson(res);
    const message = recordFrom(recordFrom(body).error).message ?? `GET ${url} failed ${res.status}`;
    throw new Error(String(message));
  }
  const body = await parseJson(res);
  return workshopsFrom(body);
}

export async function getWorkshop(workshopId: string, baseUrl?: string): Promise<StrategyWorkshop | null> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/workshops/${encodeURIComponent(workshopId)}`;
  const res = await fetch(url, {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (res.status === 404) return null;
  if (!res.ok) {
    const body = await parseJson(res);
    const message = recordFrom(recordFrom(body).error).message ?? `GET ${url} failed ${res.status}`;
    throw new Error(String(message));
  }
  const body = await parseJson(res);
  return workshopFrom(body);
}

export async function getWorkshopCompleteness(workshopId: string, baseUrl?: string): Promise<StrategyCompleteness | null> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/workshops/${encodeURIComponent(workshopId)}/completeness`;
  const res = await fetch(url, {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (res.status === 404) return null;
  if (!res.ok) {
    const body = await parseJson(res);
    const message = recordFrom(recordFrom(body).error).message ?? `GET ${url} failed ${res.status}`;
    throw new Error(String(message));
  }
  const body = await parseJson(res);
  return completenessFrom(body);
}

export async function getWorkshopReadiness(workshopId: string, baseUrl?: string): Promise<WorkshopReadiness | null> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/workshops/${encodeURIComponent(workshopId)}/readiness`;
  const res = await fetch(url, {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (res.status === 404) return null;
  if (!res.ok) {
    const body = await parseJson(res);
    const message = recordFrom(recordFrom(body).error).message ?? `GET ${url} failed ${res.status}`;
    throw new Error(String(message));
  }
  const body = await parseJson(res);
  return readinessFrom(body);
}

export async function listWorkshopCards(workshopId: string, baseUrl?: string): Promise<WorkshopCard[]> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/workshops/${encodeURIComponent(workshopId)}/events`;
  const res = await fetch(url, {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    const body = await parseJson(res);
    const message = recordFrom(recordFrom(body).error).message ?? `GET ${url} failed ${res.status}`;
    throw new Error(String(message));
  }
  const body = await parseJson(res);
  return cardsFrom(body);
}
