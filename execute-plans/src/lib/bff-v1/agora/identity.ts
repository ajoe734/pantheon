import { withStrictLiveOrMock } from "@/lib/bff/liveRead";
import type { AgoraCapabilityName, AgoraUserScope } from "@/lib/bff-v1/agora/types";

const MOCK_USER_SCOPE: AgoraUserScope = {
  spec_version: "1.0",
  scope_id: "mock-scope",
  tenant_id: "mock-tenant",
  user_id: "mock-user",
  operator_id: "mock-operator",
  granted_capabilities: [],
  read_predicate: {
    tenant_id: "mock-tenant",
    user_id: "mock-user",
    required_fields: ["tenant_id", "user_id"],
    fail_closed: true,
  },
  servant_policy: {
    persona_class: "agora_servant",
    owner_scope: "user_private",
    visibility_scope: "private",
    memory_scope: "private_user",
    persona_registry_backed: true,
    execution_authority: "none",
    prohibited_authority: ["runtime_binding", "broker_order", "capital_binding"],
  },
  created_at: "2026-01-01T00:00:00Z",
};

function adaptUserScope(body: unknown): AgoraUserScope | undefined {
  if (!body || typeof body !== "object" || Array.isArray(body)) return undefined;
  const envelope = body as Record<string, unknown>;
  const data = envelope.data ?? envelope;
  if (!data || typeof data !== "object" || Array.isArray(data)) return undefined;
  const scope = data as Record<string, unknown>;
  if (typeof scope.scope_id !== "string") return undefined;
  return scope as unknown as AgoraUserScope;
}

function adaptCapabilities(body: unknown): AgoraCapabilityName[] {
  if (Array.isArray(body)) return body.filter((c) => typeof c === "string") as AgoraCapabilityName[];
  if (!body || typeof body !== "object" || Array.isArray(body)) return [];
  const envelope = body as Record<string, unknown>;
  const caps = envelope.capabilities ?? envelope.granted_capabilities ?? envelope.data;
  if (Array.isArray(caps)) return caps.filter((c) => typeof c === "string") as AgoraCapabilityName[];
  return [];
}

async function getMe(): Promise<AgoraUserScope> {
  return withStrictLiveOrMock<AgoraUserScope>(
    { method: "GET", path: "/bff/agora/me" },
    async () => MOCK_USER_SCOPE,
    (body) => adaptUserScope(body) ?? MOCK_USER_SCOPE,
  );
}

async function getCapabilities(): Promise<AgoraCapabilityName[]> {
  return withStrictLiveOrMock<AgoraCapabilityName[]>(
    { method: "GET", path: "/bff/agora/capabilities" },
    async () => [],
    adaptCapabilities,
  );
}

export const agoraIdentityClient = { getMe, getCapabilities } as const;
