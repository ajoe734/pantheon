import type { ServantProfile } from "@/lib/bff-v1/agora/types";
import { BffError } from "@/lib/bff/liveRead";

const MOCK_SERVANT: ServantProfile = {
  spec_version: "1.0",
  persona_id: "mock-servant",
  display_name: "Agora Servant (mock)",
  status: "active",
  tenant_id: "mock-tenant",
  agora_user_id: "mock-user",
  persona_class: "agora_servant",
  owner_scope: "user_private",
  visibility_scope: "private",
  memory_scope: "private_user",
  capability_summary: {
    can_ask: true,
    can_research: false,
    can_workshop: false,
  },
  policy: {
    persona_class: "agora_servant",
    owner_scope: "user_private",
    visibility_scope: "private",
    memory_scope: "private_user",
    persona_registry_backed: true,
    execution_authority: "none",
    prohibited_authority: ["runtime_binding", "broker_order", "capital_binding"],
  },
};

function adaptServantProfile(body: unknown): ServantProfile | undefined {
  if (!body || typeof body !== "object" || Array.isArray(body)) return undefined;
  const envelope = body as Record<string, unknown>;
  const data = envelope.data ?? envelope;
  if (!data || typeof data !== "object" || Array.isArray(data)) return undefined;
  const profile = data as Record<string, unknown>;
  if (typeof profile.persona_id !== "string") return undefined;
  return profile as unknown as ServantProfile;
}

async function ensureStrict(): Promise<ServantProfile> {
  const bffMode = typeof import.meta !== "undefined" && import.meta.env?.VITE_BFF_MODE;
  if (!bffMode || bffMode !== "live") {
    return MOCK_SERVANT;
  }
  const res = await fetch("/bff/agora/servant/ensure", {
    method: "POST",
    credentials: "include",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: "{}",
  });
  if (!res.ok) {
    throw new BffError(`POST /bff/agora/servant/ensure: HTTP ${res.status}`, res.status);
  }
  const json: unknown = await res.json();
  return adaptServantProfile(json) ?? (json as ServantProfile);
}

export const agoraServantClient = { ensure: ensureStrict } as const;
