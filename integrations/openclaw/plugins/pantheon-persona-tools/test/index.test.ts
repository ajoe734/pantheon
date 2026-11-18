import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import plugin, {
  PANTHEON_TOOLS,
  TOOL_MENTAL_MODEL,
  TOOL_PROPOSAL,
  TOOL_RECALL,
  TOOL_SPECS,
  createPlugin,
  parseConfig,
} from "../src/index.js";

type Handler = (event: any, ctx: any) => Promise<any>;

const CONFIG = { brokerUrl: "http://adapter:8104/internal/persona-tools/v1/", brokerToken: "secret-1" };

interface Call {
  url: string;
  headers: Record<string, string>;
  body: any;
}

function harness(opts: { respond?: (call: Call) => { status: number; body: unknown } | Promise<never>; config?: unknown } = {}) {
  const calls: Call[] = [];
  const respond = opts.respond ?? (() => ({ status: 200, body: { allowed: true, allowed_tools: [...PANTHEON_TOOLS], result: { ok: true } } }));
  const fetchImpl = async (url: string, init: any) => {
    const call = { url, headers: init.headers, body: JSON.parse(init.body) };
    calls.push(call);
    const out = await respond(call);
    return { status: out.status, json: async () => out.body };
  };
  const instance = createPlugin({ fetchImpl: fetchImpl as any });
  let hook: Handler | undefined;
  let factory: ((ctx: any) => any) | undefined;
  let toolOptions: any;
  instance.register({
    pluginConfig: "config" in opts ? opts.config : CONFIG,
    registerTool(f: any, o: any) {
      factory = f;
      toolOptions = o;
    },
    on(_name: string, handler: Handler) {
      hook = handler;
    },
  } as any);
  return { calls, hook: hook!, factory: factory!, toolOptions };
}

const HOOK_CTX = { agentId: "persona-alpha", sessionKey: "agent:persona-alpha:openresponses-user:s1", runId: "run-1" };

test("manifest contracts.tools lists exactly the registered tools and they are optional", () => {
  const here = dirname(fileURLToPath(import.meta.url));
  const manifest = JSON.parse(readFileSync(join(here, "..", "..", "openclaw.plugin.json"), "utf8"));
  assert.deepEqual([...manifest.contracts.tools].sort(), [...PANTHEON_TOOLS].sort());
  assert.deepEqual(Object.keys(manifest.toolMetadata).sort(), [...PANTHEON_TOOLS].sort());
  assert.equal(manifest.id, plugin.id);
  assert.deepEqual(manifest.configSchema.required, ["brokerUrl", "brokerToken"]);
  assert.ok(!JSON.stringify(manifest).includes("reflect"));
  const pkg = JSON.parse(readFileSync(join(here, "..", "..", "package.json"), "utf8"));
  assert.deepEqual(pkg.openclaw.extensions, ["./dist/src/index.js"]);
});

test("registers three tools with JSON-schema parameters and no reflect tool", () => {
  const { factory, toolOptions } = harness();
  const tools = factory({ agentId: "persona-alpha", sessionKey: "k" });
  assert.deepEqual(tools.map((t: any) => t.name), [TOOL_RECALL, TOOL_MENTAL_MODEL, TOOL_PROPOSAL]);
  assert.deepEqual(toolOptions, { names: [...PANTHEON_TOOLS], optional: true });
  for (const tool of tools) {
    assert.equal(tool.parameters.type, "object");
    assert.equal(tool.parameters.additionalProperties, false);
    assert.equal(typeof tool.execute, "function");
  }
  assert.deepEqual(TOOL_SPECS.map((s) => s.name), [...PANTHEON_TOOLS]);
  assert.ok(!tools.some((t: any) => /reflect/i.test(t.name)));
  const recall = tools[0].parameters;
  assert.deepEqual(recall.required, ["query"]);
  assert.deepEqual(recall.properties.bank.enum, ["own", "facts"]);
  assert.equal(recall.properties.persona_id, undefined); // the agent cannot name a persona
});

test("no identity from the host means no tool is exposed", () => {
  const { factory } = harness();
  assert.equal(factory({}), null);
  assert.equal(factory({ agentId: "a" }), null);
});

test("authorized call forwards tool, params and host identity with the plugin-config secret only", async () => {
  const { calls, hook, factory } = harness();
  const params = { query: "momentum" };
  assert.equal(await hook({ toolName: TOOL_RECALL, params, toolCallId: "tc-1" }, HOOK_CTX), undefined);
  assert.equal(calls[0]!.url, "http://adapter:8104/internal/persona-tools/v1/authorize");
  const tool = factory({ agentId: HOOK_CTX.agentId, sessionKey: HOOK_CTX.sessionKey }).find((t: any) => t.name === TOOL_RECALL);
  const result = await tool.execute("tc-1", params);
  const call = calls[1]!;
  assert.equal(call.url, "http://adapter:8104/internal/persona-tools/v1/call");
  assert.deepEqual(call.body, {
    tool: TOOL_RECALL, params, agent_id: "persona-alpha", run_id: "run-1", session_key: HOOK_CTX.sessionKey,
  });
  assert.equal(call.headers["Authorization"], "Bearer secret-1");
  assert.deepEqual(Object.keys(call.headers).sort(), ["Authorization", "Content-Type"]);
  assert.deepEqual(result.details, { ok: true });
  assert.equal(JSON.parse(result.content[0].text).ok, true);
});

test("model-supplied identity in params is forwarded as params only, never as identity", async () => {
  const { calls, hook, factory } = harness();
  const params = { query: "q", agent_id: "persona-beta", run_id: "forged", session_key: "forged" };
  await hook({ toolName: TOOL_RECALL, params, toolCallId: "tc-2" }, HOOK_CTX);
  const tool = factory({ agentId: HOOK_CTX.agentId, sessionKey: HOOK_CTX.sessionKey })[0];
  await tool.execute("tc-2", params);
  for (const call of calls) {
    assert.equal(call.body.agent_id, "persona-alpha");
    assert.equal(call.body.run_id, "run-1");
    assert.equal(call.body.session_key, HOOK_CTX.sessionKey);
  }
});

test("hook blocks a non-persona agent when the broker refuses", async () => {
  const { hook } = harness({ respond: () => ({ status: 403, body: { error: "refused", reason: "not_a_persona_agent" } }) });
  const out = await hook({ toolName: TOOL_PROPOSAL, params: {}, toolCallId: "t" }, { ...HOOK_CTX, agentId: "main" });
  assert.equal(out.block, true);
  assert.match(out.blockReason, /not_a_persona_agent/);
});

test("hook blocks on handler error, timeout, broker 5xx and a non-allowed answer", async () => {
  const cases: Array<() => any> = [
    () => Promise.reject(new Error("ECONNREFUSED")),
    () => Promise.reject(Object.assign(new Error("timed out"), { name: "TimeoutError" })),
    () => ({ status: 503, body: { reason: "agent_registry_unavailable" } }),
    () => ({ status: 200, body: { allowed: false } }),
    () => ({ status: 200, body: {} }),
  ];
  for (const respond of cases) {
    const { hook } = harness({ respond });
    const out = await hook({ toolName: TOOL_RECALL, params: { query: "q" }, toolCallId: "t" }, HOOK_CTX);
    assert.equal(out?.block, true);
  }
});

test("a CLI-backend hook context without identity fields is blocked naming each missing field", async () => {
  const { hook, calls } = harness();
  const cli = { agentId: "persona-alpha" } as any; // no sessionKey, no runId
  const blocked = await hook({ toolName: TOOL_RECALL, params: { agent_id: "forged" } }, cli);
  assert.equal(blocked.block, true);
  assert.match(blocked.blockReason, /^persona_tools_blocked:missing_host_identity:session_key,run_id,tool_call_id/);
  assert.equal(calls.length, 0); // model-supplied identity is never accepted
  const noAgent = await hook({ toolName: TOOL_RECALL, runId: "r", toolCallId: "t" }, { sessionKey: "s" } as any);
  assert.match(noAgent.blockReason, /missing_host_identity:agent_id\b/);
});

test("hook blocks when host identity is incomplete and invalid config blocks everything", async () => {
  const { hook, calls } = harness();
  for (const ctx of [{ ...HOOK_CTX, agentId: undefined }, { ...HOOK_CTX, sessionKey: undefined }, { ...HOOK_CTX, runId: undefined }]) {
    assert.equal((await hook({ toolName: TOOL_RECALL, toolCallId: "t" }, ctx)).block, true);
  }
  assert.equal((await hook({ toolName: TOOL_RECALL, params: {} }, HOOK_CTX)).block, true); // no toolCallId
  assert.equal(calls.length, 0);
  const bad = harness({ config: { brokerUrl: "ftp://x", brokerToken: "" } });
  assert.equal((await bad.hook({ toolName: TOOL_RECALL, params: {}, toolCallId: "t" }, HOOK_CTX)).block, true);
  const missing = harness({ config: undefined });
  assert.equal((await missing.hook({ toolName: TOOL_RECALL, params: {}, toolCallId: "t" }, HOOK_CTX)).block, true);
});

test("execute without a hook authorization fails closed and never calls the broker", async () => {
  const { calls, factory } = harness();
  const tool = factory({ agentId: "persona-alpha", sessionKey: "k" })[0];
  await assert.rejects(tool.execute("never-authorized", { query: "q" }), /call_not_authorized_by_hook/);
  assert.equal(calls.length, 0);
});

test("an authorization is single-use and bound to the agent and session of the tool context", async () => {
  const { calls, hook, factory } = harness();
  await hook({ toolName: TOOL_RECALL, params: { query: "q" }, toolCallId: "tc-3" }, HOOK_CTX);
  const other = factory({ agentId: "persona-beta", sessionKey: HOOK_CTX.sessionKey })[0];
  await assert.rejects(other.execute("tc-3", { query: "q" }), /call_not_authorized_by_hook/);
  const own = factory({ agentId: HOOK_CTX.agentId, sessionKey: HOOK_CTX.sessionKey })[0];
  await own.execute("tc-3", { query: "q" });
  await assert.rejects(own.execute("tc-3", { query: "q" }), /call_not_authorized_by_hook/);
  assert.equal(calls.filter((c) => c.url.endsWith("/call")).length, 1);
});

test("broker refusal at call time surfaces as a tool error with the reason", async () => {
  const { hook, factory } = harness({
    respond: (call) => call.url.endsWith("/authorize")
      ? { status: 200, body: { allowed: true, allowed_tools: [TOOL_RECALL] } }
      : { status: 429, body: { error: "refused", reason: "run_call_limit_exceeded" } },
  });
  await hook({ toolName: TOOL_RECALL, params: { query: "q" }, toolCallId: "tc-4" }, HOOK_CTX);
  const tool = factory({ agentId: HOOK_CTX.agentId, sessionKey: HOOK_CTX.sessionKey })[0];
  await assert.rejects(tool.execute("tc-4", { query: "q" }), /run_call_limit_exceeded/);
});

test("a known persona agent is blocked from tools outside its profile; other agents are untouched", async () => {
  const { hook } = harness({
    respond: () => ({ status: 200, body: { allowed: true, allowed_tools: [TOOL_RECALL] } }),
  });
  assert.equal(await hook({ toolName: "exec", params: {} }, HOOK_CTX), undefined); // not yet known as a persona agent
  await hook({ toolName: TOOL_RECALL, params: { query: "q" }, toolCallId: "t" }, HOOK_CTX);
  assert.equal((await hook({ toolName: "exec", params: {} }, HOOK_CTX)).block, true);
  assert.equal((await hook({ toolName: "web_search", params: {} }, HOOK_CTX)).block, true);
  assert.equal(await hook({ toolName: "read", params: {} }, HOOK_CTX), undefined);
  assert.equal(await hook({ toolName: "exec", params: {} }, { ...HOOK_CTX, agentId: "main" }), undefined);
});

test("panel interpretation run: the broker's narrower tool set is what the hook enforces", async () => {
  const { hook } = harness({
    respond: (call) => call.body.tool === TOOL_RECALL
      ? { status: 200, body: { allowed: true, allowed_tools: [TOOL_RECALL] } }
      : { status: 403, body: { error: "refused", reason: "tool_not_allowed_for_purpose" } },
  });
  assert.equal(await hook({ toolName: TOOL_RECALL, params: { query: "q" }, toolCallId: "a" }, HOOK_CTX), undefined);
  for (const toolName of [TOOL_MENTAL_MODEL, TOOL_PROPOSAL]) {
    const out = await hook({ toolName, params: {}, toolCallId: "b" }, HOOK_CTX);
    assert.match(out.blockReason, /tool_not_allowed_for_purpose/);
  }
});

test("config parsing", () => {
  assert.equal(parseConfig(CONFIG).success, true);
  const ok = parseConfig({ ...CONFIG, callTimeoutMs: 1000 });
  assert.ok(ok.success && ok.data.callTimeoutMs === 1000 && ok.data.brokerUrl.endsWith("/v1"));
  for (const bad of [undefined, null, [], {}, { ...CONFIG, extra: 1 }, { ...CONFIG, callTimeoutMs: 5 }, { ...CONFIG, brokerToken: " " }]) {
    assert.equal(parseConfig(bad).success, false);
  }
});

test("the plugin source holds no memory or persona credential and no SDK import", () => {
  const here = dirname(fileURLToPath(import.meta.url));
  const source = readFileSync(join(here, "..", "..", "src", "index.ts"), "utf8");
  assert.ok(!/MEMORY_AUTH|PERSONA_SERVICE|process\.env|hindsight/i.test(source.replace(/\/\*[\s\S]*?\*\//, "")));
  assert.ok(!/from "openclaw/.test(source));
});
