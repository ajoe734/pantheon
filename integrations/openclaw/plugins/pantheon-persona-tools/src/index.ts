/**
 * Pantheon persona tools (design H9).
 *
 * Registers three agent tools - persona_memory_recall, persona_mental_model_read,
 * persona_proposal_submit - and forwards every call, with the host-provided agent
 * id, run id and session key, to the Pantheon adapter broker over the internal
 * network. The broker (not this plugin) resolves the persona, enforces the tool
 * profile and run purpose, reads memory and records the call. This plugin holds
 * only the broker secret from its own plugin config: no memory or persona-service
 * credential, no Hindsight path, no write path. There is no reflect tool; the
 * persona reasons itself.
 *
 * A before_tool_call hook (same plugin) asks the broker to authorize each
 * Pantheon tool call and blocks on any refusal, handler error or timeout, so a
 * call from an agent that is not a registered persona agent, or to a tool outside
 * that persona's profile, never reaches execute().
 *
 * The plugin has no runtime import from the OpenClaw SDK: it exports the plain
 * plugin entry object, so it loads from a path without dependency resolution.
 */

export const PLUGIN_ID = "pantheon-persona-tools";
export const TOOL_RECALL = "persona_memory_recall";
export const TOOL_MENTAL_MODEL = "persona_mental_model_read";
export const TOOL_PROPOSAL = "persona_proposal_submit";
export const PANTHEON_TOOLS = [TOOL_RECALL, TOOL_MENTAL_MODEL, TOOL_PROPOSAL] as const;
export type PantheonTool = (typeof PANTHEON_TOOLS)[number];

const DEFAULT_AUTHORIZE_TIMEOUT_MS = 5_000;
const DEFAULT_CALL_TIMEOUT_MS = 30_000;
const BINDING_TTL_MS = 10 * 60_000;
const MAX_BINDINGS = 2_000;
const PERSONA_CACHE_TTL_MS = 60_000;

export interface PluginConfig {
  brokerUrl: string;
  brokerToken: string;
  authorizeTimeoutMs: number;
  callTimeoutMs: number;
}

export type ConfigResult =
  | { success: true; data: PluginConfig }
  | { success: false; error: { issues: { path: (string | number)[]; message: string }[] } };

function fail(path: string, message: string): ConfigResult {
  return { success: false, error: { issues: [{ path: [path], message }] } };
}

export function parseConfig(raw: unknown): ConfigResult {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return fail("", "expected config object");
  }
  const value = raw as Record<string, unknown>;
  const allowed = new Set(["brokerUrl", "brokerToken", "authorizeTimeoutMs", "callTimeoutMs"]);
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) return fail(key, "unknown config key");
  }
  const brokerUrl = value["brokerUrl"];
  const brokerToken = value["brokerToken"];
  if (typeof brokerUrl !== "string" || !/^https?:\/\//.test(brokerUrl)) {
    return fail("brokerUrl", "brokerUrl must be an http(s) URL");
  }
  if (typeof brokerToken !== "string" || brokerToken.trim() === "") {
    return fail("brokerToken", "brokerToken is required");
  }
  const timeouts: Record<string, number> = {
    authorizeTimeoutMs: DEFAULT_AUTHORIZE_TIMEOUT_MS,
    callTimeoutMs: DEFAULT_CALL_TIMEOUT_MS,
  };
  for (const key of Object.keys(timeouts)) {
    const given = value[key];
    if (given === undefined) continue;
    if (typeof given !== "number" || !Number.isInteger(given) || given < 100 || given > 120_000) {
      return fail(key, `${key} must be an integer between 100 and 120000`);
    }
    timeouts[key] = given;
  }
  return {
    success: true,
    data: {
      brokerUrl: brokerUrl.replace(/\/+$/, ""),
      brokerToken: brokerToken.trim(),
      authorizeTimeoutMs: timeouts["authorizeTimeoutMs"] as number,
      callTimeoutMs: timeouts["callTimeoutMs"] as number,
    },
  };
}

export const CONFIG_JSON_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["brokerUrl", "brokerToken"],
  properties: {
    brokerUrl: { type: "string" },
    brokerToken: { type: "string", minLength: 1 },
    authorizeTimeoutMs: { type: "integer", minimum: 100, maximum: 30_000 },
    callTimeoutMs: { type: "integer", minimum: 100, maximum: 120_000 },
  },
} as const;

// --- tool parameter schemas (JSON Schema; mirrored by the broker's own validation) ---------

const recallParameters = {
  type: "object",
  additionalProperties: false,
  required: ["query"],
  properties: {
    query: { type: "string", minLength: 1, maxLength: 1000, description: "What to look up in memory." },
    bank: {
      type: "string",
      enum: ["own", "facts"],
      description: "own = your own memory; facts = the shared tenant facts. Separate calls, never mixed.",
    },
    limit: { type: "integer", minimum: 1, maximum: 10 },
    tags: { type: "array", maxItems: 10, items: { type: "string" } },
  },
} as const;

const mentalModelParameters = { type: "object", additionalProperties: false, properties: {} } as const;

const proposalParameters = {
  type: "object",
  additionalProperties: false,
  required: ["kind", "reason", "evidence_refs"],
  properties: {
    kind: {
      type: "string",
      enum: [
        "demand", "admit", "reweight", "mutate_request", "remove",
        "widen_universe", "consult_request", "harness_patch", "no_action",
      ],
    },
    reason: { type: "string", minLength: 1, maxLength: 4000 },
    evidence_refs: { type: "array", maxItems: 100, items: { type: "string" } },
    parameters: { type: "object" },
    case_file_item_ids: { type: "array", maxItems: 100, items: { type: "string" } },
    none_applicable_reason: { type: "string", maxLength: 2000 },
  },
} as const;

interface ToolSpec {
  name: PantheonTool;
  label: string;
  description: string;
  parameters: object;
}

export const TOOL_SPECS: readonly ToolSpec[] = [
  {
    name: TOOL_RECALL,
    label: "Persona memory recall",
    description:
      "Recall canonical memory for your own persona (bank=own) or the shared tenant facts (bank=facts). " +
      "Results carry canonical ids; cite them. Read-only.",
    parameters: recallParameters,
  },
  {
    name: TOOL_MENTAL_MODEL,
    label: "Persona standing-question answers",
    description:
      "Read your own standing-question answers (derived, with canonical citations). Read-only; " +
      "not available in a pre-meeting interpretation run.",
    parameters: mentalModelParameters,
  },
  {
    name: TOOL_PROPOSAL,
    label: "Persona proposal submit",
    description:
      "Record one proposal. It is only recorded for the daily-cycle controller; nothing is applied and " +
      "deterministic gates decide. Cite case_file_item_ids or give none_applicable_reason.",
    parameters: proposalParameters,
  },
];

// --- minimal structural types for the parts of the OpenClaw plugin API used ---------------

type Json = Record<string, unknown>;
type FetchLike = (url: string, init: { method: string; headers: Record<string, string>; body: string; signal: AbortSignal }) => Promise<{
  status: number;
  json(): Promise<unknown>;
}>;

interface ToolContext {
  agentId?: string;
  sessionKey?: string;
}

interface HookEvent {
  toolName: string;
  params?: Json;
  runId?: string;
  toolCallId?: string;
}

interface HookContext {
  agentId?: string;
  sessionKey?: string;
  runId?: string;
}

interface AgentTool {
  name: string;
  label: string;
  description: string;
  parameters: object;
  execute(toolCallId: string, params: Json, signal?: AbortSignal): Promise<{ content: { type: "text"; text: string }[]; details: unknown }>;
}

export interface PluginApi {
  pluginConfig?: unknown;
  logger?: { warn?(message: string): void; error?(message: string): void; info?(message: string): void };
  registerTool(
    factory: (ctx: ToolContext) => AgentTool[] | AgentTool | null | undefined,
    options?: { names?: string[]; optional?: boolean },
  ): void;
  on(
    name: "before_tool_call",
    handler: (event: HookEvent, ctx: HookContext) => Promise<{ block?: boolean; blockReason?: string } | undefined>,
    options?: { priority?: number },
  ): void;
}

interface Binding {
  agentId: string;
  sessionKey: string;
  runId: string;
  at: number;
}

export interface PluginDeps {
  fetchImpl?: FetchLike;
  now?: () => number;
  env?: Record<string, string | undefined>;
}

export const ENV_BROKER_URL = "PERSONA_TOOL_BROKER_URL";
export const ENV_BROKER_TOKEN = "PERSONA_TOOL_BROKER_TOKEN";

class BrokerRefused extends Error {
  constructor(readonly status: number, readonly reason: string) {
    super(`persona tool refused: ${reason}`);
  }
}

export function createPlugin(deps: PluginDeps = {}) {
  const doFetch: FetchLike = deps.fetchImpl ?? ((url, init) => fetch(url, init) as ReturnType<FetchLike>);
  const now = deps.now ?? Date.now;
  const env = deps.env ?? process.env;

  return {
    id: PLUGIN_ID,
    name: "Pantheon Persona Tools",
    description: "Brokered persona agent tools: memory recall, standing-question read, proposal submit.",
    configSchema: { safeParse: parseConfig, jsonSchema: CONFIG_JSON_SCHEMA },
    register(api: PluginApi): void {
      // Shipped as a stock plugin (enabled by default, no entry in openclaw.json) so the gateway config
      // stays a plain writable file. With no plugin config the broker comes from the process env; with
      // no broker token at all the plugin is inert (no hook, no tools).
      let rawConfig = api.pluginConfig;
      if (rawConfig === undefined || rawConfig === null) {
        const token = (env[ENV_BROKER_TOKEN] ?? "").trim();
        if (!token) return;
        rawConfig = { brokerUrl: env[ENV_BROKER_URL] ?? "", brokerToken: token };
      }
      const parsed = parseConfig(rawConfig);
      const config = parsed.success ? parsed.data : null;
      if (!config) {
        api.logger?.error?.(`${PLUGIN_ID}: invalid plugin config; every Pantheon persona tool call will be blocked`);
      }
      // toolCallId -> host identity captured by the hook; execute() never trusts model-supplied identity.
      const bindings = new Map<string, Binding>();
      // agent id -> tools its broker run allows, learned from authorize responses.
      const personaAgents = new Map<string, { tools: Set<string>; at: number }>();

      function prune(): void {
        const cutoff = now() - BINDING_TTL_MS;
        for (const [id, binding] of bindings) {
          if (binding.at < cutoff) bindings.delete(id);
        }
        while (bindings.size > MAX_BINDINGS) {
          const oldest = bindings.keys().next().value;
          if (oldest === undefined) break;
          bindings.delete(oldest);
        }
      }

      async function post(path: "authorize" | "call", body: Json, timeoutMs: number): Promise<Json> {
        if (!config) throw new BrokerRefused(503, "plugin_config_invalid");
        const response = await doFetch(`${config.brokerUrl}/${path}`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Authorization: `Bearer ${config.brokerToken}` },
          body: JSON.stringify(body),
          signal: AbortSignal.timeout(timeoutMs),
        });
        let payload: Json = {};
        try {
          payload = ((await response.json()) ?? {}) as Json;
        } catch {
          payload = {};
        }
        if (response.status !== 200) {
          throw new BrokerRefused(response.status, String(payload["reason"] ?? payload["error"] ?? `http_${response.status}`));
        }
        return payload;
      }

      api.on(
        "before_tool_call",
        async (event, ctx) => {
          const tool = event.toolName;
          const isPantheon = (PANTHEON_TOOLS as readonly string[]).includes(tool);
          if (!isPantheon) {
            // Defense in depth for agents already known as persona agents: the agent's own
            // tools.allow/deny stays the primary control, this only narrows further.
            const known = ctx.agentId ? personaAgents.get(ctx.agentId) : undefined;
            if (known && now() - known.at <= PERSONA_CACHE_TTL_MS && tool !== "read" && !known.tools.has(tool)) {
              return { block: true, blockReason: `tool ${tool} is outside the persona tool profile` };
            }
            return undefined;
          }
          const agentId = ctx.agentId;
          const sessionKey = ctx.sessionKey;
          const runId = event.runId ?? ctx.runId;
          const toolCallId = event.toolCallId;
          if (!agentId || !sessionKey || !runId || !toolCallId) {
            return { block: true, blockReason: "missing host identity (agent, session, run or tool call id)" };
          }
          try {
            const result = await post(
              "authorize",
              { tool, params: event.params ?? {}, agent_id: agentId, run_id: runId, session_key: sessionKey },
              config?.authorizeTimeoutMs ?? DEFAULT_AUTHORIZE_TIMEOUT_MS,
            );
            if (result["allowed"] !== true) {
              return { block: true, blockReason: "broker did not allow the call" };
            }
            const allowedTools = Array.isArray(result["allowed_tools"]) ? (result["allowed_tools"] as unknown[]).map(String) : [];
            personaAgents.set(agentId, { tools: new Set(allowedTools), at: now() });
            prune();
            bindings.set(toolCallId, { agentId, sessionKey, runId, at: now() });
            return undefined;
          } catch (error) {
            // Handler error, timeout, refusal and unreachable broker all block (fail closed).
            const reason = error instanceof BrokerRefused ? error.reason : "broker_unreachable_or_timed_out";
            return { block: true, blockReason: `persona tool blocked: ${reason}` };
          }
        },
        { priority: 100 },
      );

      api.registerTool(
        (ctx) => {
          // Without a host-provided agent and session there is no identity to forward.
          if (!ctx.agentId || !ctx.sessionKey) return null;
          return TOOL_SPECS.map((spec): AgentTool => ({
            name: spec.name,
            label: spec.label,
            description: spec.description,
            parameters: spec.parameters,
            async execute(toolCallId, params) {
              const binding = bindings.get(toolCallId);
              // The binding is created only by the authorized hook for this very call; a missing or
              // mismatched one means the hook did not run, so the call fails closed.
              if (!binding || binding.agentId !== ctx.agentId || binding.sessionKey !== ctx.sessionKey) {
                throw new BrokerRefused(403, "call_not_authorized_by_hook");
              }
              bindings.delete(toolCallId);
              const result = await post(
                "call",
                { tool: spec.name, params: params ?? {}, agent_id: binding.agentId, run_id: binding.runId, session_key: binding.sessionKey },
                config?.callTimeoutMs ?? DEFAULT_CALL_TIMEOUT_MS,
              );
              const body = result["result"] ?? {};
              return { content: [{ type: "text", text: JSON.stringify(body) }], details: body };
            },
          }));
        },
        { names: [...PANTHEON_TOOLS], optional: true },
      );
    },
  };
}

export default createPlugin();
