import { paths } from "@/lib/bff-v1/paths";
import type { AssistantUiContextV1 } from "@/lib/assistant/uiContextRegistry";

// Re-export shared catalog utilities so callers of this module remain unaffected.
export {
  type AssistantCatalogRoute,
  assistantCatalogRouteFromHandlerRef,
  invokeAssistantCatalogRoute,
  getAssistantOrchestratorStatus,
} from "./assistantCatalog";

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

export interface AssistantProviderReadiness {
  provider?: string | null;
  provider_name?: string | null;
  providerName?: string | null;
  runtime?: string | null;
  ready?: boolean | null;
  status?: string | null;
  auth?: string | null;
  auth_status?: string | null;
  authStatus?: string | null;
  degraded_reason?: string | null;
  degradedReason?: string | null;
  mount_mode?: string | null;
  mountMode?: string | null;
  checked_at?: string | null;
  checkedAt?: string | null;
  version?: string | null;
  [key: string]: unknown;
}

export interface AssistantProvidersResponse {
  status?: string;
  data: AssistantProviderReadiness[];
  meta?: Record<string, unknown>;
}

export interface AssistantControlModeActivationRequest {
  mode?: "kernel_debug" | "kernel_repair";
  passphrase: string;
  reason: string;
  ttlSeconds?: number;
  idleTtlSeconds?: number;
}

export interface AssistantProviderReauthRequest {
  provider?: string;
  reason?: string;
  traceId?: string;
  captureTimeoutSeconds?: number;
  pollIntervalSeconds?: number;
  maxWaitSeconds?: number;
}

export interface AssistantProviderReauthSession {
  provider?: string | null;
  provider_name?: string | null;
  providerName?: string | null;
  runtime?: string | null;
  status?: string | null;
  reauth_session_id?: string | null;
  reauthSessionId?: string | null;
  verification_uri?: string | null;
  verificationUri?: string | null;
  verification_uri_complete?: string | null;
  verificationUriComplete?: string | null;
  user_code?: string | null;
  userCode?: string | null;
  started_at?: string | null;
  startedAt?: string | null;
  updated_at?: string | null;
  updatedAt?: string | null;
  completed_at?: string | null;
  completedAt?: string | null;
  error_code?: string | null;
  errorCode?: string | null;
  message?: string | null;
  readiness?: AssistantProviderReadiness | Record<string, unknown> | null;
  credential_exchange?: Record<string, unknown> | null;
  credentialExchange?: Record<string, unknown> | null;
  [key: string]: unknown;
}

export interface AssistantProviderReauthResponse {
  data: AssistantProviderReauthSession;
  meta?: Record<string, unknown>;
}

function resolvedBase(baseUrl?: string): string {
  if (baseUrl) return baseUrl.replace(/\/+$/, "");
  if (typeof window !== "undefined" && window.location && window.location.origin) {
    return window.location.origin.replace(/\/+$/, "");
  }
  return "";
}

function idempotencyKey(prefix: string): string {
  const random =
    typeof crypto !== "undefined" && "randomUUID" in crypto
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

function withQuery(path: string, query: Record<string, string | number | boolean | undefined>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) params.set(key, String(value));
  }
  const encoded = params.toString();
  return encoded ? `${path}?${encoded}` : path;
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

export function getAssistantMode(baseUrl?: string): Promise<Record<string, unknown>> {
  return getJson(paths.assistantMode(), baseUrl);
}

export function activateAssistantControlMode(
  body: AssistantControlModeActivationRequest,
  baseUrl?: string,
): Promise<Record<string, unknown>> {
  return postJson(paths.assistantControlModeActivate(), body as unknown as Record<string, unknown>, baseUrl);
}

export function deactivateAssistantControlMode(
  reason = "operator_deactivated",
  baseUrl?: string,
): Promise<Record<string, unknown>> {
  return postJson(paths.assistantControlModeDeactivate(), { reason }, baseUrl);
}

export function getAssistantProviders(
  options: { authProbe?: boolean } = {},
  baseUrl?: string,
): Promise<AssistantProvidersResponse> {
  return getJson(
    withQuery(paths.assistantProviders(), { auth_probe: options.authProbe ? "true" : undefined }),
    baseUrl,
  );
}

export function startAssistantProviderReauth(
  body: AssistantProviderReauthRequest,
  baseUrl?: string,
): Promise<AssistantProviderReauthResponse> {
  return postJson(paths.assistantProviderReauth(), body as Record<string, unknown>, baseUrl);
}

export function getAssistantProviderReauthStatus(
  sessionId: string,
  provider = "codex",
  baseUrl?: string,
): Promise<AssistantProviderReauthResponse> {
  return getJson(
    withQuery(paths.assistantProviderReauthStatus(sessionId), { provider }),
    baseUrl,
  );
}

export function generateAssistantDevDocs(
  body: AssistantDevDocsGenerateRequest,
  baseUrl?: string,
): Promise<Record<string, unknown>> {
  return postJson(
    paths.assistantDevDocsGenerate(),
    body as unknown as Record<string, unknown>,
    baseUrl,
  );
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
  return postJson(
    paths.assistantDevBridgeTaskPacket(),
    body as Record<string, unknown>,
    baseUrl,
  );
}
