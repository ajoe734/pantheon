import { paths } from "@/lib/bff-v1/paths";
import type { AssistantUiContextV1 } from "@/lib/assistant/uiContextRegistry";

export interface ManagementAssistantAskRequest {
  question: string;
  sessionId?: string;
  focus?: string;
  context?: string;
  controlMode?: {
    mode?: "user" | "kernel_observe" | "kernel_debug" | "kernel_repair";
    reason?: string;
    ttlSeconds?: number;
    idleTtlSeconds?: number;
  };
  openclaw?: {
    repair?: {
      taskId?: string;
      taskWorktree?: string;
      declaredScope?: string[];
      expectedBranch?: string;
      remote?: string;
      mergeTarget?: string;
      requireClean?: boolean;
      requirePr?: boolean;
    };
  };
  ui?: AssistantUiContextV1;
  conversation?: {
    source?: "client_hint" | "server_readback";
    recentTurns?: Array<Record<string, unknown>>;
    summary?: string;
  };
  attachments?: Array<Record<string, unknown>>;
  metadata?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface AssistantDevDocsGenerateRequest {
  conversationId: string;
  featureSummary: string;
  affectedModules?: string[];
  proposedOwner?: string;
  proposedReviewer?: string;
  archive?: boolean;
  emitTaskPacket?: boolean;
  contextPack?: Record<string, unknown>;
  contextPackRequest?: Record<string, unknown>;
  extraContext?: Record<string, unknown>;
}

export interface AssistantDevBridgeTaskPacketRequest {
  devDocPacket?: Record<string, unknown>;
  packet?: Record<string, unknown>;
  mode?: string;
}

function resolvedBase(baseUrl?: string): string {
  if (baseUrl) return baseUrl.replace(/\/+$/, "");
  if (typeof window !== "undefined" && window.location && window.location.origin) {
    return window.location.origin.replace(/\/+$/, "");
  }
  return "";
}

function idempotencyKey(prefix: string): string {
  const random = typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${random}`;
}

async function parseJsonResponse<T>(res: Response, url: string, method: string): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${method} ${url} failed ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

async function getJson<T>(path: string, baseUrl?: string): Promise<T> {
  const base = resolvedBase(baseUrl);
  const url = `${base}${path}`;
  const res = await fetch(url, {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  return parseJsonResponse<T>(res, url, "GET");
}

async function postJson<T>(
  path: string,
  body: Record<string, unknown>,
  baseUrl?: string,
  idempotencyPrefix?: string,
): Promise<T> {
  const base = resolvedBase(baseUrl);
  const url = `${base}${path}`;
  const headers: Record<string, string> = {
    Accept: "application/json",
    "Content-Type": "application/json",
  };
  if (idempotencyPrefix) {
    headers["Idempotency-Key"] = idempotencyKey(idempotencyPrefix);
  }
  const res = await fetch(url, {
    method: "POST",
    credentials: "include",
    headers,
    body: JSON.stringify(body),
  });
  return parseJsonResponse<T>(res, url, "POST");
}

export function postManagementAssistantAsk(
  body: ManagementAssistantAskRequest,
  baseUrl?: string,
): Promise<Record<string, unknown>> {
  return postJson(paths.managementNlAsk(), body, baseUrl, "mgmt-ai-ask");
}

export function getManagementAssistantConversation(
  sessionId: string,
  baseUrl?: string,
): Promise<Record<string, unknown>> {
  return getJson(paths.managementAiConversation(sessionId), baseUrl);
}

export function getAssistantTranscript(
  sessionId: string,
  baseUrl?: string,
): Promise<Record<string, unknown>> {
  return getJson(paths.assistantSessionTranscript(sessionId), baseUrl);
}

export function getAssistantControlMode(baseUrl?: string): Promise<Record<string, unknown>> {
  return getJson(paths.assistantControlMode(), baseUrl);
}

export function getAssistantOrchestratorStatus(baseUrl?: string): Promise<Record<string, unknown>> {
  return getJson(paths.assistantOrchestratorStatus(), baseUrl);
}

export function generateAssistantDevDocs(
  body: AssistantDevDocsGenerateRequest,
  baseUrl?: string,
): Promise<Record<string, unknown>> {
  return postJson(paths.assistantDevDocsGenerate(), body as Record<string, unknown>, baseUrl);
}

export function getAssistantDevDoc(
  packetId: string,
  baseUrl?: string,
): Promise<Record<string, unknown>> {
  return getJson(paths.assistantDevDoc(packetId), baseUrl);
}

export function createAssistantDevTaskPacket(
  body: AssistantDevBridgeTaskPacketRequest,
  baseUrl?: string,
): Promise<Record<string, unknown>> {
  return postJson(paths.assistantDevBridgeTaskPacket(), body as Record<string, unknown>, baseUrl);
}
